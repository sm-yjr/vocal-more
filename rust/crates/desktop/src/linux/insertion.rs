// SPDX-License-Identifier: GPL-3.0-only
//! Wayland native accessible editing. One owner, bounded admission, no key injection.
use super::accessibility::{self, Shared};
use crate::bridge::CommandSink;
use anyhow::{Context, Result, ensure};
use atspi_proxies::{
    common::{ObjectRefOwned, Role, State, events::object::StateChangedEvent},
    editable_text::EditableTextProxy,
    text::TextProxy,
};
use futures_lite::StreamExt;
use serde_json::{Value, json};
use std::{
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, Instant},
};

struct Target {
    object: ObjectRefOwned,
    snapshot: Value,
    epoch: u64,
}
enum Job {
    Capture {
        epoch: u64,
        expires: Instant,
    },
    Insert {
        epoch: u64,
        generation: u64,
        text: String,
    },
    Exclude(Vec<String>),
    Compatibility {
        request: u64,
        enabled: bool,
    },
    Codex {
        request: u64,
    },
}
pub enum Event {
    Compatibility {
        request: u64,
        result: Result<Value>,
    },
    Captured {
        epoch: u64,
        snapshot: Value,
    },
    Completed {
        epoch: u64,
        generation: u64,
        result: Result<Value>,
    },
}
pub struct Worker {
    jobs: async_channel::Sender<Job>,
    events: async_channel::Receiver<Event>,
    stopped: Arc<AtomicBool>,
    finished: Arc<AtomicBool>,
}
impl Worker {
    pub fn new(sink: CommandSink, data: std::path::PathBuf) -> Result<Self> {
        let (jobs, receiver) = async_channel::bounded(4);
        // At most one FIFO insertion is in flight; keep completion capacity
        // independent from lossy waveform/UI notifications.
        let (output, events) = async_channel::bounded(8);
        let stopped = Arc::new(AtomicBool::new(false));
        let finished = Arc::new(AtomicBool::new(false));
        let stop = stopped.clone();
        let done = finished.clone();
        std::thread::Builder::new().name("vocal-more-wayland-insertion".into()).spawn(move || {
            if let Ok(runtime)=tokio::runtime::Builder::new_current_thread().enable_all().build() {
                runtime.block_on(async {
                    let privacy=Shared::output();
                    let mut compatibility=super::input_compat::Compatibility::new(&data);
                    let initial_status=compatibility.as_ref().map(|c|c.status()).map_err(|e|anyhow::anyhow!(e.to_string()));
                    let _=output.try_send(Event::Compatibility{request:0,result:initial_status});
                    let initial=tokio::time::timeout(Duration::from_millis(1500),connect()).await.ok().and_then(Result::ok);
                    let (mut connection,mut signals,mut focused)=match initial {
                        Some((bus,stream,focus))=>(Some(bus),Some(stream),focus),
                        None=>(None,None,None),
                    };
                    let mut target:Option<Target>=None;
                    let mut tick=tokio::time::interval(Duration::from_millis(50));
                    loop {
                        if stop.load(Ordering::Acquire) {break;}
                        tokio::select! {
                            biased;
                            _=tick.tick()=>{
                                if let Ok(compat)=&mut compatibility && let Some(result)=compat.poll().await {
                                    let _=output.try_send(Event::Compatibility{request:0,result});
                                }
                            },
                            job=receiver.recv()=> {
                                let Ok(job)=job else{break;};
                                if let Job::Compatibility{request,enabled}=job {
                                    let result=match &mut compatibility {Ok(compat)=>compat.configure(enabled).await,Err(e)=>Err(anyhow::anyhow!(e.to_string()))};
                                    let _=output.try_send(Event::Compatibility{request,result});continue;
                                }
                                if let Job::Codex{request}=job {
                                    let result=super::input_compat::configure_codex(&data).and_then(|_|compatibility.as_ref().map(|c|c.status()).map_err(|e|anyhow::anyhow!(e.to_string())));
                                    let _=output.try_send(Event::Compatibility{request,result});continue;
                                }
                                if matches!(job,Job::Exclude(_)) {
                                    if let Job::Exclude(excluded)=job {privacy.exclude(excluded);}
                                    continue;
                                }
                                if connection.is_none() {
                                    if let Ok(Ok((bus,stream,initial)))=tokio::time::timeout(Duration::from_millis(1000),connect()).await {
                                        connection=Some(bus);signals=Some(stream);focused=initial;
                                    }
                                    // Reconnecting cannot establish the target that was
                                    // focused before this key press. Fail this capture closed.
                                    if let Job::Capture{epoch,..}=job {
                                        let _=output.try_send(Event::Captured{epoch,snapshot:Value::Null});
                                        continue;
                                    }
                                }
                                match job {
                                    Job::Capture {epoch, expires}=> {
                                        target=None;
                                        let value=if let Some(bus)=&connection {
                                            tokio::time::timeout(expires.saturating_duration_since(Instant::now()),async {
                                                let object=match &focused {Some(object)=>object.clone(),None=>accessibility::find_focused_output(bus).await?};
                                                let snapshot=accessibility::read(bus,&object,true,&privacy).await?;
                                                active_window(bus,&object).await?;
                                                Ok::<_,anyhow::Error>((object,snapshot))
                                            }).await.context("输入位置采集超时").and_then(|r|r)
                                        } else {Err(anyhow::anyhow!("无法连接桌面辅助功能服务"))};
                                        let snapshot=match value {
                                            Ok((object,snapshot))=> {target=Some(Target{object,snapshot:snapshot.clone(),epoch});snapshot},
                                            Err(error)=> {
                                                if std::env::var_os("VOCAL_MORE_INPUT_ACCEPTANCE").is_some(){eprintln!("Acceptance capture refused: {error}");}
                                                Value::Null
                                            }
                                        };
                                        let _=output.try_send(Event::Captured{epoch,snapshot});
                                    },
                                    Job::Insert {epoch,generation,text}=> {
                                        let result=match (&connection,&mut target) {
                                            (Some(bus),Some(target))=>{
                                                let native=tokio::time::timeout(Duration::from_millis(800),insert(bus,target,&text,(epoch,generation),&sink,&privacy,&stop)).await
                                                    .context("输入应用响应超时；请检查原输入框，避免重复插入").and_then(|r|r);
                                                if native.as_ref().is_err_and(|e|e.downcast_ref::<NeedsKeyboard>().is_some()) {
                                                    match &compatibility {
                                                        Ok(compat)=>compat_insert(bus,target,&text,(epoch,generation),&sink,&privacy,&stop,compat).await,
                                                        Err(error)=>Err(anyhow::anyhow!(error.to_string())),
                                                    }
                                                } else {native}
                                            },
                                            _=>Err(anyhow::anyhow!("无法确认录音开始时的输入位置；Codex 请先在设置 → 通用配置兼容启动并重启一次")),
                                        };
                                        let _=output.try_send(Event::Completed{epoch,generation,result});
                                    },
                                    Job::Exclude(_)|Job::Compatibility{..}|Job::Codex{..}=>{},
                                }
                            },
                            message=async {match &mut signals {Some(stream)=>stream.next().await,None=>std::future::pending().await}}=> {
                                if let Some(Ok(message))=message {
                                    if let Ok(event)=StateChangedEvent::try_from(&message) && event.state==State::Focused {
                                        if event.enabled {focused=Some(event.item);} else if focused.as_ref()==Some(&event.item) {focused=None;}
                                    }
                                } else {connection=None;signals=None;focused=None;target=None;}
                            }
                        }
                    }
                    privacy.stop();
                    while let Ok(job)=receiver.try_recv() {
                        let result=match job {
                            Job::Compatibility{request,enabled}=>Some((request,match &mut compatibility{Ok(compat)=>compat.configure(enabled).await,Err(e)=>Err(anyhow::anyhow!(e.to_string()))})),
                            Job::Codex{request}=>Some((request,super::input_compat::configure_codex(&data).and_then(|_|compatibility.as_ref().map(|c|c.status()).map_err(|e|anyhow::anyhow!(e.to_string()))))),
                            _=>None,
                        };
                        if let Some((request,result))=result{let _=output.try_send(Event::Compatibility{request,result});}
                    }
                    if let Ok(compat)=&mut compatibility {compat.close().await;}
                });
            }
            done.store(true,Ordering::Release);
        })?;
        Ok(Self {
            jobs,
            events,
            stopped,
            finished,
        })
    }
    pub fn configure_compatibility(&self, request: u64, enabled: bool) -> Result<()> {
        self.jobs
            .try_send(Job::Compatibility { request, enabled })
            .context("输入服务繁忙")
    }
    pub fn configure_codex(&self, request: u64) -> Result<()> {
        self.jobs
            .try_send(Job::Codex { request })
            .context("输入服务繁忙")
    }
    pub fn capture(&self, epoch: u64) -> Result<()> {
        self.jobs
            .try_send(Job::Capture {
                epoch,
                expires: Instant::now() + Duration::from_millis(500),
            })
            .context("输入位置采集繁忙")
    }
    pub fn insert(&self, epoch: u64, generation: u64, text: String) -> Result<()> {
        self.jobs
            .try_send(Job::Insert {
                epoch,
                generation,
                text,
            })
            .context("原位输入队列繁忙")
    }
    pub fn exclude(&self, ids: Vec<String>) {
        let _ = self.jobs.try_send(Job::Exclude(ids));
    }
    pub fn poll(&self) -> Option<Event> {
        self.events.try_recv().ok()
    }
    pub fn close(&self) {
        self.stopped.store(true, Ordering::Release);
        self.jobs.close();
    }
    pub fn finished(&self) -> bool {
        self.finished.load(Ordering::Acquire)
    }
}
impl Drop for Worker {
    fn drop(&mut self) {
        self.close();
    }
}
async fn connect() -> Result<(
    zbus::Connection,
    zbus::MessageStream,
    Option<ObjectRefOwned>,
)> {
    let session = zbus::Connection::session().await?;
    let address = atspi_proxies::bus::BusProxy::new(&session)
        .await?
        .get_address()
        .await?;
    let connection = zbus::connection::Builder::address(address.as_str())?
        .build()
        .await?;
    let rule = zbus::MatchRule::builder()
        .msg_type(zbus::message::Type::Signal)
        .interface("org.a11y.atspi.Event.Object")?
        .member("StateChanged")?
        .arg(0, "focused")?
        .build();
    let stream = zbus::MessageStream::for_match_rule(rule, &connection, Some(32)).await?;
    atspi_proxies::registry::RegistryProxy::new(&connection)
        .await?
        .register_event("object:state-changed:focused")
        .await?;
    let initial = tokio::time::timeout(
        Duration::from_millis(500),
        accessibility::find_focused_output(&connection),
    )
    .await
    .ok()
    .and_then(Result::ok);
    Ok((connection, stream, initial))
}
async fn active_window(bus: &zbus::Connection, object: &ObjectRefOwned) -> Result<()> {
    let mut current = object.clone();
    for _ in 0..32 {
        let proxy = accessibility::accessible(bus, &current).await?;
        if matches!(
            proxy.get_role().await?,
            Role::Frame | Role::Window | Role::Dialog
        ) {
            ensure!(
                proxy.get_state().await?.contains(State::Active),
                "原输入窗口已失去焦点"
            );
            return Ok(());
        }
        let parent = proxy.parent().await?;
        ensure!(
            parent != current && parent.path_as_str() != "/org/a11y/atspi/null",
            "无法确认输入窗口"
        );
        current = parent;
    }
    anyhow::bail!("无法确认输入窗口")
}
fn replacement(snapshot: &Value, text: &str) -> Result<(String, i32)> {
    ensure!(text.len() <= 4 * 1024 * 1024, "识别结果过长");
    let before = snapshot["value"].as_str().context("缺少输入位置")?;
    let chars: Vec<_> = before.chars().collect();
    let start = usize::try_from(snapshot["selection_start"].as_u64().context("缺少光标")?)?;
    let selected = usize::try_from(snapshot["selection_length"].as_u64().context("缺少选区")?)?;
    let end = start.checked_add(selected).context("输入选区溢出")?;
    ensure!(end <= chars.len(), "输入选区已失效");
    let value: String = chars[..start]
        .iter()
        .chain(text.chars().collect::<Vec<_>>().iter())
        .chain(chars[end..].iter())
        .collect();
    ensure!(
        value.chars().count() <= accessibility::OUTPUT_TEXT_LIMIT,
        "输入框过长，保留结果供手动粘贴"
    );
    Ok((value, (start + text.chars().count()) as i32))
}
async fn insert(
    bus: &zbus::Connection,
    target: &mut Target,
    text: &str,
    admission: (u64, u64),
    sink: &CommandSink,
    privacy: &Shared,
    stop: &AtomicBool,
) -> Result<Value> {
    let (epoch, generation) = admission;
    ensure!(
        target.epoch == epoch && sink.can_paste(epoch, generation) && !stop.load(Ordering::Acquire),
        "已取消过期的文字输出"
    );
    let before = accessibility::read(bus, &target.object, true, privacy).await?;
    active_window(bus, &target.object).await?;
    for key in ["target_id", "value", "selection_start", "selection_length"] {
        ensure!(
            before[key] == target.snapshot[key],
            "输入目标、光标或内容已变化，已取消自动插入"
        );
    }
    if before["terminal"] == true {
        return Err(NeedsKeyboard.into());
    }
    let interfaces = accessibility::accessible(bus, &target.object)
        .await?
        .get_interfaces()
        .await?;
    if !interfaces.contains(atspi_proxies::common::Interface::EditableText) {
        return Err(NeedsKeyboard.into());
    }
    let (value, caret) = replacement(&before, text)?;
    let proxy = EditableTextProxy::builder(bus)
        .destination(target.object.name_as_str().unwrap())?
        .path(target.object.path())?
        .cache_properties(zbus::proxy::CacheProperties::No)
        .build()
        .await?;
    // InsertText preserves formatting/undo around a caret. Selection replacement
    // uses one atomic edit only for single-line controls; never flatten a rich
    // document or risk a failed delete-then-insert losing the user's selection.
    let selected = before["selection_length"].as_u64().unwrap_or(0) > 0;
    if selected {
        let states = accessibility::accessible(bus, &target.object)
            .await?
            .get_state()
            .await?;
        if states.contains(State::MultiLine)
            || !matches!(before["role"].as_str(), Some("Entry" | "Text" | "ComboBox"))
        {
            return Err(NeedsKeyboard.into());
        }
        let mut ancestor = target.object.clone();
        for _ in 0..32 {
            let node = accessibility::accessible(bus, &ancestor).await?;
            let role = node.get_role().await?;
            if matches!(
                role,
                Role::Paragraph
                    | Role::DocumentFrame
                    | Role::DocumentText
                    | Role::DocumentWeb
                    | Role::DocumentEmail
            ) {
                return Err(NeedsKeyboard.into());
            }
            if matches!(role, Role::Frame | Role::Window | Role::Dialog) {
                break;
            }
            ancestor = node.parent().await?;
        }
    }
    ensure!(
        sink.can_paste(epoch, generation) && !stop.load(Ordering::Acquire),
        "已取消过期的文字输出"
    );
    ensure!(
        if selected {
            proxy.set_text_contents(&value).await?
        } else {
            proxy
                .insert_text(
                    before["selection_start"].as_i64().unwrap_or(0) as i32,
                    text,
                    text.chars().count() as i32,
                )
                .await?
        },
        "该输入框不支持原生自动插入"
    );
    let text_proxy = TextProxy::builder(bus)
        .destination(target.object.name_as_str().unwrap())?
        .path(target.object.path())?
        .cache_properties(zbus::proxy::CacheProperties::No)
        .build()
        .await?;
    ensure!(
        text_proxy.get_text(0, -1).await? == value,
        "输入框未确认写入；请检查原输入框，避免重复插入"
    );
    if active_window(bus, &target.object).await.is_ok() {
        let _ = text_proxy.set_caret_offset(caret).await?;
    }
    target.snapshot = before;
    target.snapshot["value"] = json!(value);
    target.snapshot["selection_start"] = json!(caret);
    target.snapshot["selection_length"] = json!(0);
    Ok(target.snapshot.clone())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn unicode_selection_replacement_preserves_prefix_and_suffix() {
        let (value, caret) = replacement(
            &json!({"value":"前缀旧值后缀","selection_start":2,"selection_length":2}),
            "新🦀词",
        )
        .unwrap();
        assert_eq!(value, "前缀新🦀词后缀");
        assert_eq!(caret, 5);
    }
    #[test]
    fn long_unicode_document_and_multiline_replacement() {
        let prefix = "前🦀".repeat(6000);
        let (value, caret) = replacement(
            &json!({"value":format!("{prefix}旧\n值后缀"),"selection_start":12000,"selection_length":3}),
            "新\n文字",
        ).unwrap();
        assert_eq!(value, format!("{prefix}新\n文字后缀"));
        assert_eq!(caret, 12004);
    }
    #[test]
    fn invalid_or_excessive_selections_are_rejected_without_panicking() {
        for (start, length) in [(u64::MAX, 1), (1, u64::MAX), (3, 0), (0, 3)] {
            assert!(
                replacement(
                    &json!({"value":"ab","selection_start":start,"selection_length":length}),
                    "x"
                )
                .is_err()
            );
        }
        assert!(replacement(&json!({"value":"a".repeat(accessibility::OUTPUT_TEXT_LIMIT),"selection_start":0,"selection_length":0}), "x").is_err());
    }
    #[test]
    #[ignore = "requires the owned GTK acceptance session"]
    fn linux_native_wayland_insertion() -> Result<()> {
        let path = std::path::PathBuf::from(
            std::env::var_os("VOCAL_MORE_ATSPI_TEST_CONTROL").context("use the private runner")?,
        );
        let profile = tempfile::tempdir()?;
        let (driver, sink, _events) = crate::bridge::BackendDriver::start(
            vocal_more_backend::application::Options::new(profile.path().into()),
        )?;
        let worker = Worker::new(sink.clone(), profile.path().into())?;
        fn capture(worker: &Worker, epoch: u64) -> Value {
            worker.capture(epoch).unwrap();
            let end = Instant::now() + Duration::from_secs(3);
            while Instant::now() < end {
                if let Some(Event::Captured { snapshot, .. }) = worker.poll() {
                    return snapshot;
                }
                std::thread::sleep(Duration::from_millis(10));
            }
            panic!("native input capture timed out")
        }
        fn insert(worker: &Worker, sink: &CommandSink, text: &str) -> Result<Value> {
            worker.insert(sink.paste_epoch(), sink.generation(), text.into())?;
            let end = Instant::now() + Duration::from_secs(3);
            while Instant::now() < end {
                if let Some(Event::Completed { result, .. }) = worker.poll() {
                    return result;
                }
                std::thread::sleep(Duration::from_millis(10));
            }
            anyhow::bail!("native insertion completion timed out")
        }
        std::thread::sleep(Duration::from_millis(500));
        let before = capture(&worker, sink.paste_epoch());
        ensure!(
            before["value"] == "前缀：旧值后缀",
            "initial GTK field unavailable: {before}"
        );
        let after = insert(&worker, &sink, "新🦀词")?;
        assert_eq!(after["value"], "前缀：新🦀词后缀");
        assert_eq!(after["selection_start"], 6);
        assert_eq!(insert(&worker, &sink, "A")?["value"], "前缀：新🦀词A后缀");
        // Output may target long documents without expanding correction-learning reads.
        std::fs::write(&path, "long")?;
        std::thread::sleep(Duration::from_millis(250));
        let long = format!("{}旧值后缀", "前🦀".repeat(6000));
        assert_eq!(capture(&worker, sink.paste_epoch())["value"], long);
        assert_eq!(
            insert(&worker, &sink, "新🦀词")?["value"],
            format!("{}新🦀词后缀", "前🦀".repeat(6000))
        );
        // A multiline selection must never be flattened by SetTextContents.
        // This fixture has no keyboard permission, so refuse without an edit.
        std::fs::write(&path, "multiline")?;
        std::thread::sleep(Duration::from_millis(250));
        let multiline = capture(&worker, sink.paste_epoch());
        assert_eq!(multiline["value"], "前缀：旧\n值后缀");
        assert!(insert(&worker, &sink, "MUST NOT REPLACE DOCUMENT").is_err());
        assert_eq!(
            capture(&worker, sink.paste_epoch())["value"],
            multiline["value"]
        );
        // A new focus must never receive output admitted for the old target.
        std::fs::write(&path, "other")?;
        std::thread::sleep(Duration::from_millis(250));
        assert!(insert(&worker, &sink, "NEVER").is_err());
        std::fs::write(&path, "reset")?;
        std::thread::sleep(Duration::from_millis(250));
        assert_eq!(
            capture(&worker, sink.paste_epoch())["value"],
            "前缀：旧值后缀"
        );
        sink.request("cancel", json!({}));
        assert!(insert(&worker, &sink, "CANCELLED").is_err());
        std::fs::write(&path, "secure")?;
        std::thread::sleep(Duration::from_millis(250));
        assert!(capture(&worker, sink.paste_epoch()).is_null());
        std::fs::write(&path, "reset")?;
        std::thread::sleep(Duration::from_millis(250));
        worker.close();
        driver.close();
        let end = Instant::now() + Duration::from_secs(5);
        while !worker.finished() || !driver.finished() {
            ensure!(Instant::now() < end, "native input owner did not close");
            std::thread::sleep(Duration::from_millis(10));
        }
        Ok(())
    }
}

#[derive(Debug)]
struct NeedsKeyboard;
impl std::fmt::Display for NeedsKeyboard {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "该输入框需要兼容自动插入以保留选区和文档格式")
    }
}
impl std::error::Error for NeedsKeyboard {}

#[allow(clippy::too_many_arguments)]
async fn compat_insert(
    bus: &zbus::Connection,
    target: &mut Target,
    text: &str,
    admission: (u64, u64),
    sink: &CommandSink,
    privacy: &Shared,
    stop: &AtomicBool,
    compat: &super::input_compat::Compatibility,
) -> Result<Value> {
    let (epoch, generation) = admission;
    let keyboard = compat.keyboard()?;
    let before = tokio::time::timeout(Duration::from_millis(800), async {
        ensure!(
            target.epoch == epoch
                && sink.can_paste(epoch, generation)
                && !stop.load(Ordering::Acquire),
            "已取消过期输出"
        );
        let before = accessibility::read(bus, &target.object, true, privacy).await?;
        active_window(bus, &target.object).await?;
        for key in ["target_id", "value", "selection_start", "selection_length"] {
            ensure!(
                before[key] == target.snapshot[key],
                "原输入框、选区或内容已经变化"
            );
        }
        Ok::<_, anyhow::Error>(before)
    })
    .await
    .context("输入框验证超时")??;
    let terminal = before["terminal"] == true;
    let expected = if terminal {
        None
    } else {
        Some(replacement(&before, text)?.0)
    };
    let text = text.to_owned();
    trace_stage("clipboard-prepare");
    let mut clipboard =
        tokio::task::spawn_blocking(move || super::input_compat::Clipboard::prepare(text, true))
            .await??;
    trace_stage("clipboard-prepared");
    let result = async {
        tokio::time::sleep(Duration::from_millis(40)).await;
        let (owned, next) = tokio::join!(
            tokio::task::spawn_blocking(move || {
                let owned = clipboard.owned();
                (clipboard, owned)
            }),
            tokio::time::timeout(Duration::from_millis(800), async {
                loop {
                    let focused = accessibility::accessible(bus, &target.object)
                        .await?
                        .get_state()
                        .await?
                        .contains(State::Focused);
                    if focused && active_window(bus, &target.object).await.is_ok() {
                        let snapshot =
                            accessibility::read(bus, &target.object, true, privacy).await?;
                        for key in ["target_id", "value", "selection_start", "selection_length"] {
                            ensure!(snapshot[key] == before[key], "输入目标已变化，取消兼容粘贴");
                        }
                        trace_stage("focus-restored-after-clipboard");
                        break Ok::<_, anyhow::Error>(());
                    }
                    // wl-copy's fallback surface has no accessible input field.
                    // Wait for it to release focus, but never accept a different
                    // real target or a user-edited original field.
                    if let Ok(Ok(other)) = tokio::time::timeout(
                        Duration::from_millis(80),
                        accessibility::find_focused_output(bus),
                    )
                    .await
                    {
                        ensure!(
                            other == target.object,
                            "输入焦点已切换到其他应用，已停止自动插入"
                        );
                    }
                    tokio::time::sleep(Duration::from_millis(20)).await;
                }
            })
        );
        let (mut clipboard, owned) = owned.context("剪贴板保护任务失败")?;
        // Focus recovery may take several frames. Recheck clipboard ownership
        // after that wait so a user's intervening copy cannot be pasted.
        let (clipboard, still_owned) = tokio::task::spawn_blocking(move || {
            let still_owned = clipboard.owned();
            (clipboard, still_owned)
        })
        .await
        .context("剪贴板复核任务失败")?;
        let action = async {
            ensure!(owned? && still_owned?, "剪贴板已变化，取消兼容粘贴");
            next.context("原输入框验证超时")??;
            keyboard
                .paste(sink, epoch, generation, stop, terminal)
                .await?;
            if terminal {
                // A terminal may consume clipboard data asynchronously. It has
                // no readable input value, so do not fabricate text confirmation.
                tokio::time::sleep(Duration::from_millis(600)).await;
                return Ok(before.clone());
            }
            let end = Instant::now() + Duration::from_millis(900);
            loop {
                let snapshot = tokio::time::timeout(
                    Duration::from_millis(150),
                    accessibility::read(bus, &target.object, false, privacy),
                )
                .await;
                if let Ok(Ok(snapshot)) = snapshot
                    && snapshot["value"].as_str() == expected.as_deref()
                {
                    return Ok(snapshot);
                }
                ensure!(
                    Instant::now() < end,
                    "输入框未确认粘贴；请检查原输入框，避免重复插入"
                );
                tokio::time::sleep(Duration::from_millis(20)).await;
            }
        }
        .await;
        let restored = tokio::task::spawn_blocking(move || clipboard.finish())
            .await
            .context("剪贴板恢复任务失败")?;
        restored?;
        trace_stage("clipboard-restored");
        action
    }
    .await;
    if let Ok(snapshot) = &result {
        target.snapshot = snapshot.clone();
    }
    result
}

#[cfg(test)]
mod compatibility_tests {
    use super::*;
    #[test]
    #[ignore = "requires the private Chromium and keyboard-portal runner"]
    fn linux_real_compatible_chromium() -> Result<()> {
        ensure!(
            std::env::var("VOCAL_MORE_PRIVATE_GESTURE_TEST").as_deref() == Ok("1"),
            "private runner required"
        );
        let output = std::path::PathBuf::from(
            std::env::var_os("VOCAL_MORE_COMPAT_OUTPUT").context("private runner required")?,
        );
        let profile = tempfile::tempdir()?;
        let (driver, sink, _events) = crate::bridge::BackendDriver::start(
            vocal_more_backend::application::Options::new(profile.path().into()),
        )?;
        let worker = Worker::new(sink.clone(), profile.path().into())?;
        std::fs::write(output.join("phase"), "authorizing")?;
        worker.configure_compatibility(1, true)?;
        let end = Instant::now() + Duration::from_secs(90);
        loop {
            if let Some(Event::Compatibility { result, .. }) = worker.poll() {
                let status = result?;
                if status["ready"] == true {
                    break;
                }
                ensure!(
                    status["enabled"] != true || status["pending"] == true,
                    "keyboard authorization failed: {}",
                    status["message"]
                );
            }
            ensure!(
                Instant::now() < end,
                "native keyboard authorization timed out"
            );
            std::thread::sleep(Duration::from_millis(20));
        }
        std::fs::write(output.join("phase"), "authorized")?;
        while !output.join("focused").exists() {
            ensure!(Instant::now() < end, "owned Chromium focus timed out");
            std::thread::sleep(Duration::from_millis(20));
        }
        fn capture(worker: &Worker, epoch: u64) -> Result<Value> {
            worker.capture(epoch)?;
            let end = Instant::now() + Duration::from_secs(3);
            while Instant::now() < end {
                if let Some(Event::Captured { snapshot, .. }) = worker.poll() {
                    return Ok(snapshot);
                }
                std::thread::sleep(Duration::from_millis(10));
            }
            anyhow::bail!("Chromium capture timed out")
        }
        fn insert(worker: &Worker, sink: &CommandSink, text: &str) -> Result<Value> {
            let phase =
                std::path::PathBuf::from(std::env::var_os("VOCAL_MORE_COMPAT_OUTPUT").unwrap())
                    .join("phase");
            std::fs::write(&phase, "inserting")?;
            worker.insert(sink.paste_epoch(), sink.generation(), text.into())?;
            let end = Instant::now() + Duration::from_secs(8);
            while Instant::now() < end {
                if let Some(Event::Completed { result, .. }) = worker.poll() {
                    std::fs::write(&phase, "delivered")?;
                    return result;
                }
                std::thread::sleep(Duration::from_millis(10));
            }
            anyhow::bail!("Chromium insertion timed out")
        }
        let scenario = std::env::var("VOCAL_MORE_COMPAT_SCENARIO").unwrap_or_default();
        let clipboard_mime = match scenario.as_str() {
            "terminal" => "image/bmp",
            "rich" => "text/html",
            "image" => "image/png",
            _ => "text/plain;charset=utf-8",
        };
        let clipboard_before = if scenario == "terminal" {
            let (width, height) = (2048u32, 1024u32);
            let size = 54 + width * height * 4;
            let mut bmp = vec![0u8; size as usize];
            bmp[..2].copy_from_slice(b"BM");
            bmp[2..6].copy_from_slice(&size.to_le_bytes());
            bmp[10..14].copy_from_slice(&54u32.to_le_bytes());
            bmp[14..18].copy_from_slice(&40u32.to_le_bytes());
            bmp[18..22].copy_from_slice(&width.to_le_bytes());
            bmp[22..26].copy_from_slice(&height.to_le_bytes());
            bmp[26..28].copy_from_slice(&1u16.to_le_bytes());
            bmp[28..30].copy_from_slice(&32u16.to_le_bytes());
            bmp
        } else if scenario == "rich" {
            b"<b>owned rich clipboard</b>".to_vec()
        } else if scenario == "image" {
            include_bytes!("../../../../../scripts/fixtures/clipboard-pixel.png").to_vec()
        } else {
            b"owned clipboard before compatibility test".to_vec()
        };
        if matches!(scenario.as_str(), "rich" | "image" | "terminal") {
            super::super::platform::run(
                "wl-copy",
                &["--type", clipboard_mime],
                Some(&clipboard_before),
            )?;
        } else if scenario != "mixed" {
            super::super::platform::copy(
                super::super::platform::Session::Wayland,
                std::str::from_utf8(&clipboard_before)?,
            )?;
        }
        // Cold startup must find a deep composer without walking 1,800 history rows.
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()?;
        runtime.block_on(async {
            let (_bus, _signals, focused) = connect().await?;
            ensure!(
                focused.is_some(),
                "cold metadata lookup missed the deep Chromium editor"
            );
            Ok::<_, anyhow::Error>(())
        })?;
        ensure!(
            super::super::input_compat::shell_clipboard().is_some(),
            "native GNOME clipboard service unavailable"
        );
        let original = capture(&worker, sink.paste_epoch())?;
        let (expected_before, expected_after, inserted) = match scenario.as_str() {
            "long" => (
                format!("{}旧值后缀", "前文".repeat(6000)),
                format!("{}兼容🦀输入后缀", "前文".repeat(6000)),
                "兼容🦀输入",
            ),
            "multiline" => (
                "前缀：旧\n值后缀".into(),
                "前缀：兼容🦀\n输入后缀".into(),
                "兼容🦀\n输入",
            ),
            _ => (
                "前缀：旧值后缀".into(),
                "前缀：兼容🦀输入后缀".into(),
                "兼容🦀输入",
            ),
        };
        if scenario == "terminal" {
            assert_eq!(
                original["terminal"], true,
                "terminal metadata was not captured"
            );
            insert(&worker, &sink, "终端🦀输入")?;
            std::fs::write(output.join("phase"), "delivered")?;
            let end = Instant::now() + Duration::from_secs(3);
            let received = output.join("terminal-input.json");
            while !received.exists() {
                ensure!(Instant::now() < end, "terminal did not receive paste");
                std::thread::sleep(Duration::from_millis(20));
            }
            let value: Value = serde_json::from_slice(&std::fs::read(received)?)?;
            assert_eq!(value["received"], "终端🦀输入");
            assert_eq!(
                super::super::platform::clipboard(super::super::platform::Session::Wayland)?,
                clipboard_before
            );
        } else if scenario == "mixed" {
            let plain = super::super::platform::run(
                "wl-paste",
                &["--no-newline", "--type", "text/plain"],
                None,
            )?;
            let html = super::super::platform::run(
                "wl-paste",
                &["--no-newline", "--type", "text/html"],
                None,
            )?;
            assert_eq!(plain, b"owned plain");
            assert_eq!(html, b"<b>owned rich</b>");
            assert!(insert(&worker, &sink, inserted).is_err());
            std::fs::write(output.join("phase"), "delivered")?;
            assert_eq!(
                capture(&worker, sink.paste_epoch())?["value"],
                original["value"]
            );
            assert_eq!(
                super::super::platform::run(
                    "wl-paste",
                    &["--no-newline", "--type", "text/plain"],
                    None
                )?,
                plain
            );
            assert_eq!(
                super::super::platform::run(
                    "wl-paste",
                    &["--no-newline", "--type", "text/html"],
                    None
                )?,
                html
            );
        } else {
            ensure!(
                original["value"] == expected_before,
                "owned Chromium field not available"
            );
            let after = insert(&worker, &sink, inserted)?;
            std::fs::write(output.join("phase"), "delivered")?;
            assert_eq!(after["value"], expected_after);
            assert_eq!(
                super::super::platform::clipboard(super::super::platform::Session::Wayland)?,
                clipboard_before
            );
            if matches!(scenario.as_str(), "rich" | "image") {
                assert_eq!(
                    super::super::platform::run(
                        "wl-paste",
                        &["--type", clipboard_mime, "--no-newline"],
                        None
                    )?,
                    clipboard_before
                );
            }
        }
        std::fs::write(output.join("phase"), "paste-completed")?;
        while !output.join("focus-changed").exists() {
            ensure!(Instant::now() < end, "owned focus switch timed out");
            std::thread::sleep(Duration::from_millis(20));
        }
        assert!(insert(&worker, &sink, "NEVER").is_err());
        sink.request("cancel", json!({}));
        assert!(insert(&worker, &sink, "CANCELLED").is_err());
        if scenario == "terminal" {
            let value: Value =
                serde_json::from_slice(&std::fs::read(output.join("terminal-input.json"))?)?;
            assert_eq!(
                value["received"], "终端🦀输入",
                "late output reached old terminal"
            );
        }
        worker.close();
        let end = Instant::now() + Duration::from_secs(8);
        while !worker.finished() {
            ensure!(Instant::now() < end, "first input owner did not close");
            std::thread::sleep(Duration::from_millis(20));
        }
        if matches!(scenario.as_str(), "rich" | "image" | "terminal") {
            assert_eq!(
                super::super::platform::clipboard_format(clipboard_mime)?,
                clipboard_before,
                "rich clipboard lost when input owner closed"
            );
        }
        let recovered = Worker::new(sink.clone(), profile.path().into())?;
        let end = Instant::now() + Duration::from_secs(10);
        loop {
            if let Some(Event::Compatibility { result, .. }) = recovered.poll()
                && result?["ready"] == true
            {
                break;
            }
            ensure!(
                Instant::now() < end,
                "saved keyboard permission did not restore"
            );
            std::thread::sleep(Duration::from_millis(20));
        }
        recovered.close();
        driver.close();
        let end = Instant::now() + Duration::from_secs(8);
        while !worker.finished() || !recovered.finished() || !driver.finished() {
            ensure!(Instant::now() < end, "input owner did not shut down");
            std::thread::sleep(Duration::from_millis(20));
        }
        // The compositor source identity must protect an intervening user copy,
        // even when the two sources contain exactly the same text.
        let text = "same text from a different clipboard owner";
        let mut pending = super::super::input_compat::Clipboard::prepare(text.into(), true)?;
        assert!(pending.owned()?);
        let connection = super::super::input_compat::shell_clipboard().unwrap();
        connection.call_method(
            Some(super::super::shell::NAME),
            super::super::shell::PATH,
            Some(super::super::shell::NAME),
            "ClipboardSet",
            &("text/plain;charset=utf-8", text.as_bytes()),
        )?;
        assert!(
            !pending.owned()?,
            "same-text replacement retained old ownership"
        );
        pending.finish()?;
        let preserved: Vec<u8> = connection
            .call_method(
                Some(super::super::shell::NAME),
                super::super::shell::PATH,
                Some(super::super::shell::NAME),
                "ClipboardRead",
                &("text/plain;charset=utf-8",),
            )?
            .body()
            .deserialize()?;
        assert_eq!(preserved, text.as_bytes());
        std::fs::write(output.join("rust-compatibility.json"),json!({"scenario":scenario,"terminal_input":scenario=="terminal","unicode_selection":scenario!="mixed","mixed_formats_refused":scenario=="mixed","clipboard_restored":true,"focus_switch_refused":true,"cancel_refused":true,"clean_shutdown":true,"permission_restored":true}).to_string())?;
        std::fs::write(output.join("phase"), "complete")?;
        Ok(())
    }
}

fn trace_stage(stage: &str) {
    if std::env::var_os("VOCAL_MORE_TRACE_UI").is_some() {
        eprintln!("input {stage}");
    }
}
