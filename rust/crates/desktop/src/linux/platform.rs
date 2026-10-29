// SPDX-License-Identifier: GPL-3.0-only
//! Serial, bounded external desktop operations. No subprocess waits on the UI.
use crate::bridge::CommandSink;
use anyhow::{Context, Result, bail, ensure};
use base64::{Engine as _, engine::general_purpose::STANDARD};
use serde_json::{Value, json};
use std::{
    fs::File,
    io::{Read, Seek, SeekFrom, Write},
    process::{Command, Stdio},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
        mpsc::{Receiver, RecvTimeoutError, SyncSender, sync_channel},
    },
    time::{Duration, Instant},
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Session {
    Wayland,
    X11,
}
impl Session {
    pub fn current() -> Self {
        if std::env::var("XDG_SESSION_TYPE").as_deref() == Ok("wayland")
            || std::env::var_os("WAYLAND_DISPLAY").is_some()
        {
            Self::Wayland
        } else {
            Self::X11
        }
    }
}

pub struct DeliveryJob {
    pub text: String,
    pub epoch: u64,
    pub generation: u64,
    pub auto_paste: bool,
    pub restore: bool,
}
enum Job {
    Deliver(DeliveryJob),
    Copy(String),
    Open(String),
    Play {
        id: Option<String>,
        path: String,
        wav: bool,
    },
    Stop(Option<String>),
}
pub struct DesktopWorker {
    sender: Option<SyncSender<Job>>,
    completed: Receiver<Value>,
    closing: Arc<AtomicBool>,
    finished: Arc<AtomicBool>,
}
impl DesktopWorker {
    pub fn new(sink: CommandSink, session: Session) -> Result<Self> {
        let (sender, receiver) = sync_channel(8);
        let (completed_tx, completed) = sync_channel(16);
        let closing = Arc::new(AtomicBool::new(false));
        let finished = Arc::new(AtomicBool::new(false));
        let stop = closing.clone();
        let done = finished.clone();
        std::thread::Builder::new().name("vocal-more-linux-output".into()).spawn(move || {
            let mut playback: Option<Playback> = None;
            loop {
                if playback.as_mut().is_some_and(|playback| playback.poll(&sink)) { playback.take(); }
                if stop.load(Ordering::Acquire) { break; }
                let job = match receiver.recv_timeout(Duration::from_millis(20)) {
                    Ok(job) => job,
                    Err(RecvTimeoutError::Timeout) => continue,
                    Err(RecvTimeoutError::Disconnected) => break,
                };
                if stop.load(Ordering::Acquire) { break; }
                match job {
                    Job::Deliver(job) => {
                        let result = deliver(&sink, session, &job);
                        // At most one FIFO delivery is active. Unlike lossy visual events,
                        // its completion has reserved capacity and is polled by the host.
                        let _ = completed_tx.try_send(json!({"epoch":job.epoch,"generation":job.generation,"ok":result.is_ok(),
                            "message":match result {Ok(message)=>message.to_owned(),Err(error)=>error.to_string()}}));
                    },
                    Job::Copy(text) => {
                        let result = copy(session, &text);
                        sink.emit("linux_notice",json!({"message":match result {
                            Ok(())=>"已复制到剪贴板".to_owned(),Err(error)=>error.to_string()}}));
                    },
                    Job::Open(url) => {
                        if let Err(error) = run("xdg-open", &[&url], None) {
                            sink.emit("linux_notice",json!({"message":error.to_string()}));
                        }
                    },
                    Job::Play { id, path, wav } => {
                        if let Some(mut old) = playback.take() { old.stop(&sink); }
                        match Playback::start(id.clone(),&path,wav) {
                            Ok(new) => {
                                if id.is_some() { sink.emit("recording_playback_started",json!({"id":id})); }
                                playback = Some(new);
                            },
                            Err(error) => sink.emit(if id.is_some(){"recording_playback_error"}else{"mic_test_playback_error"},
                                json!({"id":id,"message":error.to_string()})),
                        }
                    },
                    Job::Stop(id) => {
                        if playback.as_ref().is_some_and(|p| id.is_none() || p.id == id)
                            && let Some(mut old) = playback.take() { old.stop(&sink); }
                    },
                }
            }
            if let Some(mut old) = playback { old.stop(&sink); }
            done.store(true,Ordering::Release);
        })?;
        Ok(Self {
            sender: Some(sender),
            completed,
            closing,
            finished,
        })
    }
    fn submit(&self, job: Job) -> Result<()> {
        self.sender
            .as_ref()
            .context("desktop worker is closed")?
            .try_send(job)
            .map_err(|_| {
                anyhow::anyhow!("desktop output queue is full; retry after the current operation")
            })
    }
    pub fn deliver(&self, job: DeliveryJob) -> Result<()> {
        self.submit(Job::Deliver(job))
    }
    pub fn copy(&self, text: String) -> Result<()> {
        self.submit(Job::Copy(text))
    }
    pub fn open(&self, url: &str) -> Result<()> {
        ensure!(
            url.starts_with("https://")
                || url.starts_with("http://")
                || url.starts_with("file:///"),
            "unsupported Linux URL"
        );
        self.submit(Job::Open(url.to_owned()))
    }
    pub fn play(&self, id: Option<String>, path: String, wav: bool) -> Result<()> {
        self.submit(Job::Play { id, path, wav })
    }
    pub fn stop(&self, id: Option<String>) -> Result<()> {
        self.submit(Job::Stop(id))
    }
    pub fn completion(&self) -> Option<Value> {
        self.completed.try_recv().ok()
    }
    pub fn finished(&self) -> bool {
        self.finished.load(Ordering::Acquire)
    }
    pub fn close(&mut self) {
        self.closing.store(true, Ordering::Release);
        self.sender.take();
    }
}

struct Playback {
    child: std::process::Child,
    id: Option<String>,
    _wave: Option<tempfile::NamedTempFile>,
}
impl Playback {
    fn start(id: Option<String>, path: &str, wav: bool) -> Result<Self> {
        let mut wave = None;
        let path = if wav {
            ensure!(path.len() < 16 * 1024 * 1024, "preview audio is too large");
            let data = STANDARD.decode(path)?;
            ensure!(
                data.starts_with(b"RIFF") && data.get(8..12) == Some(b"WAVE"),
                "invalid preview WAV"
            );
            let mut file = tempfile::Builder::new().suffix(".wav").tempfile()?;
            file.write_all(&data)?;
            let path = file.path().to_owned();
            wave = Some(file);
            path
        } else {
            std::path::PathBuf::from(path)
        };
        ensure!(path.is_file(), "audio file is unavailable");
        let mut last_error = None;
        for binary in ["pw-play", "paplay"] {
            match Command::new(binary)
                .arg(&path)
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()
            {
                Ok(child) => {
                    return Ok(Self {
                        child,
                        id,
                        _wave: wave,
                    });
                }
                Err(error) => last_error = Some(error),
            }
        }
        Err(last_error.unwrap())
            .context("install PipeWire's pw-play or PulseAudio's paplay for playback")
    }
    fn ended(&self, sink: &CommandSink) {
        sink.emit(
            if self.id.is_some() {
                "recording_playback_ended"
            } else {
                "mic_test_playback_ended"
            },
            json!({"id":self.id}),
        );
    }
    fn poll(&mut self, sink: &CommandSink) -> bool {
        match self.child.try_wait() {
            Ok(None) => false,
            Ok(Some(status)) if status.success() => {
                self.ended(sink);
                true
            }
            _ => {
                sink.emit(
                    if self.id.is_some() {
                        "recording_playback_error"
                    } else {
                        "mic_test_playback_error"
                    },
                    json!({"id":self.id,"message":"Linux 音频播放失败"}),
                );
                true
            }
        }
    }
    fn stop(&mut self, sink: &CommandSink) {
        let _ = self.child.kill();
        let _ = self.child.wait();
        self.ended(sink);
    }
}
impl Drop for DesktopWorker {
    fn drop(&mut self) {
        self.close();
    }
}

fn command(session: Session, read: bool) -> (&'static str, Vec<&'static str>) {
    match (session, read) {
        (Session::Wayland, false) => ("wl-copy", vec!["--type", "text/plain;charset=utf-8"]),
        (Session::Wayland, true) => ("wl-paste", vec!["--no-newline"]),
        (Session::X11, false) => ("xclip", vec!["-selection", "clipboard", "-in"]),
        (Session::X11, true) => ("xclip", vec!["-selection", "clipboard", "-out"]),
    }
}
pub(super) fn copy(session: Session, text: &str) -> Result<()> {
    ensure!(
        text.len() <= 4 * 1024 * 1024,
        "text exceeds clipboard limit"
    );
    let (binary, args) = command(session, false);
    run(binary, &args, Some(text.as_bytes())).map(|_| ())
}
pub(super) fn clipboard(session: Session) -> Result<Vec<u8>> {
    let (binary, args) = command(session, true);
    run_bounded(binary, &args, None, CLIPBOARD_LIMIT)
}

pub(super) const CLIPBOARD_LIMIT: u64 = 64 * 1024 * 1024;
pub(super) fn clipboard_format(mime: &str) -> Result<Vec<u8>> {
    run_bounded(
        "wl-paste",
        &["--no-newline", "--type", mime],
        None,
        CLIPBOARD_LIMIT,
    )
}

fn deliver(sink: &CommandSink, session: Session, job: &DeliveryJob) -> Result<&'static str> {
    if !sink.can_paste(job.epoch, job.generation) {
        return Ok("已取消过期的文字输出");
    }
    let paste = session == Session::X11 && job.auto_paste;
    let target = if paste {
        run("xdotool", &["getwindowfocus"], None).ok()
    } else {
        None
    };
    let previous = if paste && target.is_some() && job.restore {
        // Do not replace images/files or mixed-format clipboard content with a
        // text-only snapshot. An empty/unowned selection has no formats.
        match run(
            "xclip",
            &["-selection", "clipboard", "-target", "TARGETS", "-out"],
            None,
        ) {
            Ok(targets) => {
                ensure!(
                    text_only_targets(&targets),
                    "剪贴板含非文本内容，已保留；请从窗口复制转写结果"
                );
                Some(clipboard(session).context("无法备份文字剪贴板，已保留原内容")?)
            }
            Err(_) => None,
        }
    } else {
        None
    };
    if !sink.can_paste(job.epoch, job.generation) {
        return Ok("已取消过期的文字输出");
    }
    copy(session, &job.text)?;
    if !paste || target.is_none() {
        return Ok("转写完成，已复制；按 Ctrl+V 粘贴");
    }
    std::thread::sleep(Duration::from_millis(50));
    let mut posted = false;
    let result = (|| {
        if !sink.can_paste(job.epoch, job.generation) {
            return Ok("已取消粘贴");
        }
        ensure!(
            run("xdotool", &["getwindowfocus"], None).ok() == target,
            "焦点已变化，文字保留在剪贴板，请手动粘贴"
        );
        ensure!(
            clipboard(session)? == job.text.as_bytes(),
            "剪贴板已被其他应用修改，已取消粘贴"
        );
        if !sink.can_paste(job.epoch, job.generation) {
            return Ok("已取消粘贴");
        }
        run("xdotool", &["key", "--clearmodifiers", "ctrl+v"], None)?;
        posted = true;
        Ok("已发送粘贴快捷键")
    })();
    // Keep the FIFO item active through the receiver's clipboard read grace.
    if posted {
        std::thread::sleep(Duration::from_millis(600));
    }
    if let Some(previous) = previous
        && clipboard(session).is_ok_and(|current| current == job.text.as_bytes())
        && let Ok(text) = std::str::from_utf8(&previous)
    {
        copy(session, text)?;
    }
    result
}

fn text_only_targets(targets: &[u8]) -> bool {
    std::str::from_utf8(targets).is_ok_and(|targets| {
        targets.lines().all(|target| {
            let target = target.trim();
            target.is_empty()
                || matches!(
                    target,
                    "TARGETS"
                        | "TIMESTAMP"
                        | "MULTIPLE"
                        | "SAVE_TARGETS"
                        | "UTF8_STRING"
                        | "TEXT"
                        | "STRING"
                )
                || target == "text/plain"
                || target.starts_with("text/plain;charset=")
        })
    })
}

/// Temp files prevent stdin/stdout pipe deadlocks. Commands are fixed binaries,
/// text is never an argument or shell program. Polling bounds each child wait.
pub(super) fn run(binary: &str, args: &[&str], input: Option<&[u8]>) -> Result<Vec<u8>> {
    run_bounded(binary, args, input, 4 * 1024 * 1024)
}
fn run_bounded(binary: &str, args: &[&str], input: Option<&[u8]>, limit: u64) -> Result<Vec<u8>> {
    let mut source = tempfile::tempfile()?;
    if let Some(input) = input {
        source.write_all(input)?;
        source.seek(SeekFrom::Start(0))?;
    }
    let mut output = tempfile::tempfile()?;
    let mut child = Command::new(binary)
        .args(args)
        .stdin(Stdio::from(source))
        .stdout(Stdio::from(output.try_clone()?))
        .stderr(Stdio::null())
        .spawn()
        .with_context(|| format!("无法运行 {binary}，请安装 Linux 桌面依赖"))?;
    let deadline = Instant::now() + Duration::from_secs(3);
    loop {
        if let Some(status) = child.try_wait()? {
            ensure!(status.success(), "{binary} 执行失败，请检查桌面会话和权限");
            break;
        }
        if output.metadata()?.len() > limit {
            let _ = child.kill();
            let _ = child.wait();
            bail!("desktop response exceeds size limit");
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            bail!("{binary} 执行超时");
        }
        std::thread::sleep(Duration::from_millis(5));
    }
    read_output_bounded(&mut output, limit)
}
#[cfg(test)]
fn read_output(file: &mut File) -> Result<Vec<u8>> {
    read_output_bounded(file, 4 * 1024 * 1024)
}
fn read_output_bounded(file: &mut File, limit: u64) -> Result<Vec<u8>> {
    ensure!(
        file.metadata()?.len() <= limit,
        "desktop response exceeds size limit"
    );
    file.seek(SeekFrom::Start(0))?;
    let mut output = Vec::new();
    file.take(limit + 1).read_to_end(&mut output)?;
    ensure!(
        output.len() as u64 <= limit,
        "desktop response exceeds size limit"
    );
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn clipboard_images_have_a_separate_bounded_read_budget() {
        let mut file = tempfile::tempfile().unwrap();
        file.set_len(5 * 1024 * 1024).unwrap();
        assert!(read_output(&mut file).is_err());
        assert_eq!(
            read_output_bounded(&mut file, CLIPBOARD_LIMIT)
                .unwrap()
                .len(),
            5 * 1024 * 1024
        );
        file.set_len(CLIPBOARD_LIMIT + 1).unwrap();
        assert!(read_output_bounded(&mut file, CLIPBOARD_LIMIT).is_err());
    }
    #[test]
    fn text_never_becomes_shell_syntax() {
        let input = b"$HOME `touch /tmp/unwanted`\n'\";";
        assert_eq!(run("cat", &[], Some(input)).unwrap(), input);
    }
    #[test]
    fn mixed_clipboard_formats_cannot_be_restored_as_plain_text() {
        assert!(text_only_targets(
            b"TARGETS\nUTF8_STRING\ntext/plain;charset=utf-8\n"
        ));
        assert!(!text_only_targets(b"UTF8_STRING\nimage/png\n"));
        assert!(!text_only_targets(b"text/uri-list\n"));
    }
    #[test]
    fn wayland_never_selects_x11_output_tools() {
        assert_eq!(command(Session::Wayland, false).0, "wl-copy");
        assert_eq!(command(Session::Wayland, true).0, "wl-paste");
    }
}

#[cfg(test)]
mod lifecycle_tests {
    use super::*;
    use crate::bridge::{BackendDriver, UI_QUEUE_CAPACITY};
    use vocal_more_backend::application::Options;

    #[test]
    fn revoked_delivery_completes_even_when_visual_events_are_full() {
        let data = tempfile::tempdir().unwrap();
        let (driver, sink, _events) =
            BackendDriver::start(Options::new(data.path().to_owned())).unwrap();
        sink.update_session("idle", 1);
        let epoch = sink.paste_epoch();
        sink.request("cancel", json!({}));
        for _ in 0..UI_QUEUE_CAPACITY {
            sink.emit("synthetic_visual_event", json!({}));
        }
        let mut worker = DesktopWorker::new(sink, Session::Wayland).unwrap();
        worker
            .deliver(DeliveryJob {
                text: "must not reach a clipboard".into(),
                epoch,
                generation: 1,
                auto_paste: true,
                restore: true,
            })
            .unwrap();
        let deadline = Instant::now() + Duration::from_secs(2);
        let completion = loop {
            if let Some(completion) = worker.completion() {
                break completion;
            }
            assert!(Instant::now() < deadline, "reserved completion was lost");
            std::thread::sleep(Duration::from_millis(5));
        };
        assert_eq!(completion["epoch"], epoch);
        assert_eq!(completion["message"], "已取消过期的文字输出");
        worker.close();
        driver.close();
        while !worker.finished() || !driver.finished() {
            assert!(Instant::now() < deadline, "shutdown did not finish");
            std::thread::sleep(Duration::from_millis(5));
        }
    }
}
