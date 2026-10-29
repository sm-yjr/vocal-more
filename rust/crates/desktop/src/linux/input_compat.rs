// SPDX-License-Identifier: GPL-3.0-only
//! Explicit keyboard-only portal authorization for accessible Chromium editors.
use super::platform::{self, Session as DesktopSession};
use crate::bridge::CommandSink;
use anyhow::{Context, Result, ensure};
use ashpd::desktop::{
    PersistMode, Session,
    remote_desktop::{DeviceType, KeyState, RemoteDesktop, SelectDevicesOptions},
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    io::{Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::atomic::{AtomicBool, Ordering},
    time::Duration,
};

#[derive(Default, Serialize, Deserialize)]
struct Preference {
    enabled: bool,
    restore_token: Option<String>,
}
pub struct Compatibility {
    path: PathBuf,
    preference: Preference,
    pending: Option<tokio::task::JoinHandle<Result<Keyboard>>>,
    keyboard: Option<Keyboard>,
    message: String,
}
pub struct Keyboard {
    portal: RemoteDesktop,
    session: Session<RemoteDesktop>,
    token: Option<String>,
}
impl Compatibility {
    pub fn new(data: &Path) -> Result<Self> {
        let path = data.join("linux-input.json");
        let preference = if path.exists() {
            ensure!(path.metadata()?.len() <= 8192, "输入设置文件过大");
            serde_json::from_slice(&std::fs::read(&path)?)?
        } else {
            Preference::default()
        };
        let mut this = Self {
            path,
            preference,
            pending: None,
            keyboard: None,
            message: String::new(),
        };
        if this.preference.enabled {
            this.authorize();
        }
        Ok(this)
    }
    fn save(&self) -> Result<()> {
        let mut file = tempfile::NamedTempFile::new_in(self.path.parent().unwrap())?;
        file.write_all(&serde_json::to_vec(&self.preference)?)?;
        file.as_file().sync_all()?;
        file.persist(&self.path)?;
        std::fs::File::open(self.path.parent().unwrap())?.sync_all()?;
        Ok(())
    }
    fn authorize(&mut self) {
        self.message = "请在系统授权窗口中允许键盘控制；不请求屏幕或鼠标权限。".into();
        let token = self.preference.restore_token.clone();
        self.pending = Some(tokio::spawn(async move {
            tokio::time::timeout(Duration::from_secs(120), Keyboard::open(token))
                .await
                .context("兼容输入授权超时")?
        }));
    }
    pub fn status(&self) -> Value {
        json!({"enabled":self.preference.enabled,"ready":self.keyboard.is_some(),"pending":self.pending.is_some(),"message":self.message,"codex_configured":codex_configured()})
    }
    pub async fn configure(&mut self, enabled: bool) -> Result<Value> {
        self.close().await;
        self.preference.enabled = enabled;
        self.save()?;
        if enabled {
            self.authorize();
        } else {
            self.message.clear();
        }
        Ok(self.status())
    }
    pub async fn poll(&mut self) -> Option<Result<Value>> {
        if !self.pending.as_ref().is_some_and(|task| task.is_finished()) {
            return None;
        }
        let result = self.pending.take().unwrap().await;
        match result {
            Ok(Ok(keyboard)) => {
                self.preference.restore_token = keyboard.token.clone();
                if let Err(error) = self.save() {
                    let _ = keyboard.session.close().await;
                    return Some(Err(error));
                }
                self.keyboard = Some(keyboard);
                self.message = "兼容自动插入已就绪。Codex 首次配置后需要重启一次。".into();
            }
            error => {
                self.message = match error {
                    Ok(Err(error)) => error.to_string(),
                    Err(error) => error.to_string(),
                    _ => unreachable!(),
                };
            }
        }
        Some(Ok(self.status()))
    }
    pub fn keyboard(&self) -> Result<&Keyboard> {
        self.keyboard
            .as_ref()
            .context("该应用需要启用兼容自动插入；请到设置 → 通用开启，并为 Codex 配置兼容启动")
    }
    pub async fn close(&mut self) {
        if let Some(task) = self.pending.take() {
            task.abort();
            let _ = task.await;
        }
        if let Some(keyboard) = self.keyboard.take() {
            let _ =
                tokio::time::timeout(Duration::from_millis(300), keyboard.session.close()).await;
        }
    }
}
impl Keyboard {
    async fn open(token: Option<String>) -> Result<Self> {
        let connection = zbus::Connection::session().await?;
        let id = if std::env::var("VOCAL_MORE_PRIVATE_GESTURE_TEST").as_deref() == Ok("1") {
            "io.github.sm_yjr.VocalMore.Test"
        } else {
            "io.github.sm_yjr.VocalMore"
        };
        ashpd::register_host_app_with_connection(connection.clone(), id.parse()?).await?;
        let portal = RemoteDesktop::with_connection(connection).await?;
        let session = portal.create_session(Default::default()).await?;
        let result = async {
            portal
                .select_devices(
                    &session,
                    SelectDevicesOptions::default()
                        .set_devices(Some(DeviceType::Keyboard.into()))
                        .set_persist_mode(PersistMode::ExplicitlyRevoked)
                        .set_restore_token(token.as_deref()),
                )
                .await?
                .response()?;
            let reply = portal
                .start(&session, None, Default::default())
                .await?
                .response()?;
            ensure!(
                reply.devices().contains(DeviceType::Keyboard),
                "系统未授予键盘控制权限"
            );
            Ok::<_, anyhow::Error>(reply.restore_token().map(str::to_owned))
        }
        .await;
        match result {
            Ok(token) => Ok(Self {
                portal,
                session,
                token,
            }),
            Err(error) => {
                let _ = session.close().await;
                Err(error)
            }
        }
    }
    async fn key(&self, code: i32, state: KeyState) -> Result<()> {
        tokio::time::timeout(
            Duration::from_millis(150),
            self.portal
                .notify_keyboard_keycode(&self.session, code, state, Default::default()),
        )
        .await
        .context("系统键盘服务响应超时")??;
        Ok(())
    }
    pub async fn paste(
        &self,
        sink: &CommandSink,
        epoch: u64,
        generation: u64,
        stop: &AtomicBool,
        terminal: bool,
    ) -> Result<()> {
        ensure!(
            sink.can_paste(epoch, generation) && !stop.load(Ordering::Acquire),
            "已取消过期输出"
        );
        if terminal {
            // Ghostty binds the dedicated Paste key. Avoid modifier chords,
            // whose releases can leak CSI-u sequences into a terminal application.
            let pressed = self.key(135, KeyState::Pressed).await;
            let released = self.key(135, KeyState::Released).await;
            pressed?;
            return released;
        }
        let result = async {
            self.key(29, KeyState::Pressed).await?;
            ensure!(
                sink.can_paste(epoch, generation) && !stop.load(Ordering::Acquire),
                "已取消过期输出"
            );
            self.key(47, KeyState::Pressed).await?;
            Ok::<_, anyhow::Error>(())
        }
        .await;
        // Release both keys even if cancellation or an RPC failure interrupted the pair.
        let released_v = self.key(47, KeyState::Released).await;
        let released_ctrl = self.key(29, KeyState::Released).await;
        result?;
        released_v?;
        released_ctrl?;
        Ok(())
    }
}

pub struct Clipboard {
    owner: ClipboardOwner,
    text: String,
    previous: Option<ClipboardSnapshot>,
    restore: bool,
}
enum ClipboardOwner {
    Process(Child),
    Shell {
        connection: zbus::blocking::Connection,
        serial: u32,
    },
}
pub(super) fn shell_clipboard() -> Option<zbus::blocking::Connection> {
    let connection = zbus::blocking::connection::Builder::session()
        .ok()?
        .method_timeout(Duration::from_secs(3))
        .build()
        .ok()?;
    let reply = connection
        .call_method(
            Some(super::shell::NAME),
            super::shell::PATH,
            Some(super::shell::NAME),
            "ClipboardVersion",
            &(),
        )
        .ok()?;
    (reply.body().deserialize::<u32>().ok()? == 1).then_some(connection)
}
fn set_shell_clipboard(
    connection: &zbus::blocking::Connection,
    mime: &str,
    bytes: &[u8],
) -> Result<u32> {
    Ok(connection
        .call_method(
            Some(super::shell::NAME),
            super::shell::PATH,
            Some(super::shell::NAME),
            "ClipboardSet",
            &(mime, bytes),
        )?
        .body()
        .deserialize()?)
}
fn clipboard_types(shell: Option<&zbus::blocking::Connection>) -> Result<Vec<u8>> {
    if let Some(connection) = shell {
        let types: Vec<String> = connection
            .call_method(
                Some(super::shell::NAME),
                super::shell::PATH,
                Some(super::shell::NAME),
                "ClipboardTypes",
                &(),
            )?
            .body()
            .deserialize()?;
        return Ok(types.join("\n").into_bytes());
    }
    platform::run("wl-paste", &["--list-types"], None)
}
fn clipboard_bytes(shell: Option<&zbus::blocking::Connection>, mime: &str) -> Result<Vec<u8>> {
    if let Some(connection) = shell {
        return Ok(connection
            .call_method(
                Some(super::shell::NAME),
                super::shell::PATH,
                Some(super::shell::NAME),
                "ClipboardRead",
                &(mime,),
            )?
            .body()
            .deserialize()?);
    }
    platform::clipboard_format(mime)
}
impl Clipboard {
    pub fn prepare(text: String, restore: bool) -> Result<Self> {
        ensure!(text.len() <= 4 * 1024 * 1024, "识别结果过长");
        let shell = shell_clipboard();
        let previous = if restore {
            match clipboard_types(shell.as_ref()) {
                Ok(types) if types.is_empty() => None,
                Ok(types) => {
                    let previous = ClipboardSnapshot::capture(&types, shell.as_ref())?;
                    Some(previous)
                }
                Err(error) if shell.is_some() => return Err(error),
                Err(_) => None,
            }
        } else {
            None
        };
        if let Some(connection) = shell {
            let serial =
                set_shell_clipboard(&connection, "text/plain;charset=utf-8", text.as_bytes())?;
            return Ok(Self {
                owner: ClipboardOwner::Shell { connection, serial },
                text,
                previous,
                restore,
            });
        }
        let mut input = tempfile::tempfile()?;
        input.write_all(text.as_bytes())?;
        input.seek(SeekFrom::Start(0))?;
        let child = Command::new("wl-copy")
            .args(["--foreground", "--type", "text/plain;charset=utf-8"])
            .stdin(Stdio::from(input))
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()?;
        Ok(Self {
            owner: ClipboardOwner::Process(child),
            text,
            previous,
            restore,
        })
    }
    pub fn owned(&mut self) -> Result<bool> {
        let owned = match &mut self.owner {
            ClipboardOwner::Process(child) => child.try_wait()?.is_none(),
            ClipboardOwner::Shell { connection, serial } => {
                return Ok(connection
                    .call_method(
                        Some(super::shell::NAME),
                        super::shell::PATH,
                        Some(super::shell::NAME),
                        "ClipboardOwned",
                        &(*serial,),
                    )?
                    .body()
                    .deserialize::<bool>()?);
            }
        };
        Ok(owned && platform::clipboard(DesktopSession::Wayland)? == self.text.as_bytes())
    }
    pub fn finish(mut self) -> Result<()> {
        let result = (|| {
            if self.restore && self.owned()? {
                if let ClipboardOwner::Shell { connection, .. } = &self.owner {
                    let (mime, bytes) = self
                        .previous
                        .as_ref()
                        .map(|p| (p.mime.as_str(), p.bytes.as_slice()))
                        .unwrap_or(("text/plain;charset=utf-8", b""));
                    set_shell_clipboard(connection, mime, bytes)?;
                    return Ok(());
                }
                if let Some(previous) = &self.previous {
                    platform::run(
                        "wl-copy",
                        &["--type", &previous.mime],
                        Some(&previous.bytes),
                    )?;
                } else {
                    platform::run("wl-copy", &["--clear"], None)?;
                }
            }
            Ok(())
        })();
        if self.restore
            && let ClipboardOwner::Process(child) = &mut self.owner
        {
            let _ = child.kill();
            let _ = child.wait();
        }
        result
    }
}
struct ClipboardSnapshot {
    mime: String,
    bytes: Vec<u8>,
}
impl ClipboardSnapshot {
    fn capture(types: &[u8], shell: Option<&zbus::blocking::Connection>) -> Result<Self> {
        let names: Vec<_> = std::str::from_utf8(types)?.lines().collect();
        ensure!(
            !names.is_empty() && names.len() <= 32,
            "剪贴板格式数量无效，已保留原内容"
        );
        let distinct: Vec<_> = names
            .iter()
            .filter(|mime| !plain_types(mime.as_bytes()))
            .collect();
        ensure!(
            distinct.len() <= 1 && !names.contains(&"application/vnd.portal.filetransfer"),
            "剪贴板包含多个独立格式，暂不支持安全恢复；原内容已保留"
        );
        let mime = distinct.first().copied().copied().unwrap_or(names[0]);
        let bytes = clipboard_bytes(shell, mime)?;
        for format in &names {
            let alias = clipboard_bytes(shell, format)?;
            ensure!(
                alias == bytes,
                "剪贴板各格式内容不同，暂不支持安全恢复；原内容已保留"
            );
        }
        ensure!(clipboard_types(shell)? == types, "剪贴板在备份期间变化");
        let verify = clipboard_bytes(shell, mime)?;
        ensure!(verify == bytes, "剪贴板在备份期间变化");
        Ok(Self {
            mime: mime.into(),
            bytes,
        })
    }
}

fn plain_types(types: &[u8]) -> bool {
    std::str::from_utf8(types).is_ok_and(|s| {
        s.lines().all(|mime| {
            matches!(mime, "text/plain" | "UTF8_STRING" | "TEXT" | "STRING")
                || mime.starts_with("text/plain;charset=")
        })
    })
}

pub fn codex_configured() -> bool {
    codex_launcher()
        .ok()
        .and_then(|p| std::fs::read_to_string(p).ok())
        .is_some_and(|s| compatible_launcher(&s).is_ok_and(|updated| updated.is_none()))
}
fn codex_launcher() -> Result<PathBuf> {
    let root = std::env::var_os("XDG_DATA_HOME")
        .filter(|p| Path::new(p).is_absolute())
        .map(PathBuf::from)
        .unwrap_or(
            PathBuf::from(std::env::var_os("HOME").context("HOME unavailable")?)
                .join(".local/share"),
        );
    Ok(root.join("applications/chatgpt.desktop"))
}
fn compatible_launcher(original: &str) -> Result<Option<String>> {
    ensure!(original.len() <= 64 * 1024, "启动入口过大");
    let mut changed = false;
    let mut configured = false;
    let mut section = false;
    let lines: Vec<String> = original
        .lines()
        .map(|line| {
            if line.starts_with('[') {
                section = line == "[Desktop Entry]";
            }
            if section && line.starts_with("Exec=") && !changed {
                let command = &line[5..];
                let words = command.split_whitespace().collect::<Vec<_>>();
                let mut index = 0;
                if matches!(words.first(), Some(&"env" | &"/usr/bin/env")) {
                    index = 1;
                    while words
                        .get(index)
                        .is_some_and(|word| matches!(*word, "-u" | "--unset"))
                    {
                        if !words.get(index + 1).is_some_and(|name| {
                            !name.is_empty()
                                && name.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
                        }) {
                            index = words.len();
                            break;
                        }
                        index += 2;
                    }
                }
                if let Some(binary) = words.get(index)
                    && matches!(*binary, "chatgpt" | "/usr/bin/chatgpt")
                {
                    // Only known env-unset prefixes are accepted. Preserve the
                    // original entry verbatim, including its Wayland IME flags.
                    let start = binary.as_ptr() as usize - command.as_ptr() as usize;
                    let split = start + binary.len();
                    if command[split..]
                        .split_whitespace()
                        .any(|arg| arg == "--force-renderer-accessibility")
                    {
                        configured = true;
                        return line.into();
                    }
                    changed = true;
                    return format!(
                        "Exec={} --force-renderer-accessibility{}",
                        &command[..split],
                        &command[split..]
                    );
                }
            }
            line.into()
        })
        .collect();
    ensure!(
        changed || configured,
        "Codex 启动入口使用了未知命令，请保留原配置"
    );
    Ok(changed.then(|| format!("{}\n", lines.join("\n"))))
}
pub fn configure_codex(data: &Path) -> Result<()> {
    let destination = codex_launcher()?;
    let original = if destination.exists() {
        std::fs::read_to_string(&destination)?
    } else {
        std::fs::read_to_string("/usr/share/applications/chatgpt.desktop")
            .context("未找到 Codex 启动入口")?
    };
    let Some(updated) = compatible_launcher(&original)? else {
        return Ok(());
    };
    let backup = data.join("codex-launcher-before-accessibility.desktop");
    if !backup.exists() {
        let mut copy = tempfile::NamedTempFile::new_in(data)?;
        copy.write_all(original.as_bytes())?;
        copy.as_file().sync_all()?;
        copy.persist_noclobber(&backup)?;
        std::fs::File::open(data)?.sync_all()?;
    }
    std::fs::create_dir_all(destination.parent().unwrap())?;
    let mut file = tempfile::NamedTempFile::new_in(destination.parent().unwrap())?;
    file.write_all(updated.as_bytes())?;
    file.as_file().sync_all()?;
    file.persist(&destination)?;
    std::fs::File::open(destination.parent().unwrap())?.sync_all()?;
    Ok(())
}
#[cfg(test)]
mod tests {
    #[test]
    fn codex_launcher_preserves_arguments_and_other_actions() {
        let original = "[Desktop Entry]\nName=Codex\nExec=/usr/bin/chatgpt --profile test %U\n[Desktop Action New]\nExec=chatgpt --new-window\n";
        let updated = super::compatible_launcher(original).unwrap().unwrap();
        assert_eq!(
            updated,
            original.replacen(
                "Exec=/usr/bin/chatgpt",
                "Exec=/usr/bin/chatgpt --force-renderer-accessibility",
                1
            )
        );
        assert!(super::compatible_launcher(&updated).unwrap().is_none());
        assert!(super::compatible_launcher("[Desktop Entry]\nExec=custom-wrapper %U\n").is_err());
        let elsewhere =
            "[Desktop Entry]\nComment=--force-renderer-accessibility\nExec=chatgpt %U\n";
        assert!(super::compatible_launcher(elsewhere).unwrap().is_some());
        let ime = "[Desktop Entry]\nExec=/usr/bin/env -u GTK_IM_MODULE -u GDK_BACKEND chatgpt --ozone-platform=wayland %U\n";
        assert_eq!(
            super::compatible_launcher(ime).unwrap().unwrap(),
            ime.replace(
                "chatgpt --ozone",
                "chatgpt --force-renderer-accessibility --ozone"
            )
        );
    }
    #[test]
    fn mixed_clipboard_formats_are_not_flattened() {
        assert!(super::plain_types(
            b"text/plain\ntext/plain;charset=utf-8\n"
        ));
        assert!(!super::plain_types(b"text/plain\nimage/png\n"));
        assert!(!super::plain_types(b"text/html\n"));
    }
}
