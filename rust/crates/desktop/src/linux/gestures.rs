// SPDX-License-Identifier: GPL-3.0-only
//! Portal global grabs provide real press/release events without a key logger.
use crate::bridge::CommandSink;
use anyhow::{Context, Result, ensure};
use ashpd::desktop::global_shortcuts::{GlobalShortcuts, NewShortcut};
use futures_lite::StreamExt;
use serde_json::{Value, json};
use std::{
    collections::HashSet,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
    time::Duration,
};
use tokio::sync::Notify;
#[derive(Clone, Default, Debug, PartialEq, Eq)]
struct Config {
    enabled: bool,
    bindings: Vec<(String, String)>,
}
struct Shared {
    config: Mutex<Config>,
    revision: AtomicU64,
    changed: Notify,
    closed: AtomicBool,
    finished: AtomicBool,
}
pub struct Event {
    revision: u64,
    pub active: bool,
    pub message: String,
    pub labels: Vec<(String, String)>,
}
pub struct Worker {
    shared: Arc<Shared>,
    events: async_channel::Receiver<Event>,
}
impl Worker {
    pub fn new(commands: CommandSink, app_id: String) -> Result<Self> {
        let shared = Arc::new(Shared {
            config: Mutex::new(Config::default()),
            revision: AtomicU64::new(0),
            changed: Notify::new(),
            closed: AtomicBool::new(false),
            finished: AtomicBool::new(false),
        });
        let state = shared.clone();
        let (output, events) = async_channel::bounded(8);
        std::thread::Builder::new()
            .name("vocal-more-portal-shortcuts".into())
            .spawn(move || {
                if let Ok(runtime) = tokio::runtime::Builder::new_current_thread()
                    .enable_all()
                    .build()
                {
                    runtime.block_on(async {
                        let mut completed = 0;
                        loop {
                            if state.closed.load(Ordering::Acquire) {
                                break;
                            }
                            let revision = state.revision.load(Ordering::Acquire);
                            if revision == completed {
                                state.changed.notified().await;
                                continue;
                            }
                            completed = revision;
                            let config = state.config.lock().unwrap().clone();
                            if !config.enabled {
                                continue;
                            }
                            let result =
                                session(&state, revision, &config, &commands, &output, &app_id)
                                    .await;
                            if let Err(error) = result
                                && current(&state, revision)
                            {
                                commands.request("platform_shortcut_cancel", json!({}));
                                let _ = output.try_send(Event {
                                    revision,
                                    active: false,
                                    message: error.to_string(),
                                    labels: vec![],
                                });
                            }
                        }
                    });
                }
                state.finished.store(true, Ordering::Release);
            })?;
        Ok(Self { shared, events })
    }
    pub fn configure(&self, status: &Value) -> bool {
        let config = Config {
            enabled: status["gesture_enabled"] == true,
            bindings: status["items"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(|item| {
                    Some((
                        item["id"].as_str()?.into(),
                        item["accelerator"].as_str()?.into(),
                    ))
                })
                .collect(),
        };
        let mut old = self.shared.config.lock().unwrap();
        if *old != config {
            *old = config;
            self.shared.revision.fetch_add(1, Ordering::AcqRel);
            self.shared.changed.notify_one();
            true
        } else {
            false
        }
    }
    pub fn poll(&self) -> Option<Event> {
        while let Ok(event) = self.events.try_recv() {
            if event.revision == self.shared.revision.load(Ordering::Acquire) {
                return Some(event);
            }
        }
        None
    }
    pub fn close(&self) {
        self.shared.closed.store(true, Ordering::Release);
        self.shared.changed.notify_one();
    }
    pub fn finished(&self) -> bool {
        self.shared.finished.load(Ordering::Acquire)
    }
}
impl Drop for Worker {
    fn drop(&mut self) {
        self.close();
    }
}
fn current(shared: &Shared, revision: u64) -> bool {
    !shared.closed.load(Ordering::Acquire) && shared.revision.load(Ordering::Acquire) == revision
}
async fn cancelled(shared: &Shared, revision: u64) {
    while current(shared, revision) {
        shared.changed.notified().await;
    }
}
fn trigger(key: &str) -> String {
    key.replace("<Control>", "CTRL+")
        .replace("<Alt>", "ALT+")
        .replace("<Shift>", "SHIFT+")
        .replace("<Super>", "LOGO+")
}
async fn session(
    state: &Shared,
    revision: u64,
    config: &Config,
    commands: &CommandSink,
    output: &async_channel::Sender<Event>,
    app_id: &str,
) -> Result<()> {
    ensure!(!config.bindings.is_empty(), "请先添加快捷键");
    let startup = async {
        let connection = zbus::Connection::session().await?;
        ashpd::register_host_app_with_connection(connection.clone(), app_id.parse()?)
            .await
            .context("无法向系统注册 Vocal More 快捷键身份")?;
        let portal = GlobalShortcuts::with_connection(connection.clone())
            .await
            .context("桌面不支持按下/松开快捷键；已保留一键切换")?;
        Ok::<_, anyhow::Error>((connection, portal))
    };
    let (connection, portal) = tokio::select! {
        _=cancelled(state,revision)=>return Ok(()),
        result=tokio::time::timeout(Duration::from_secs(5),startup)=>result.context("系统快捷键服务无响应")??,
    };
    let session = tokio::select! {
        _=cancelled(state,revision)=>return Ok(()),
        session=tokio::time::timeout(Duration::from_secs(3),portal.create_session(Default::default()))=>session.context("快捷键服务无响应")??,
    };
    let path = serde_json::to_value(&session)?
        .as_str()
        .context("系统快捷键会话无效")?
        .to_owned();
    let result=async {
        // One ordered signal stream keeps a fast release/press pair in wire
        // order. Separate Activated/Deactivated streams can reorder a backlog.
        let rule=zbus::MatchRule::builder().msg_type(zbus::message::Type::Signal)
            .sender("org.freedesktop.portal.Desktop")?
            .path("/org/freedesktop/portal/desktop")?
            .interface("org.freedesktop.portal.GlobalShortcuts")?.build();
        let mut signals=zbus::MessageStream::for_match_rule(rule,&connection,Some(64)).await?;
        let mut closed=session.receive_closed().await?;
        let preferred:Vec<_>=config.bindings.iter().map(|(id,key)|NewShortcut::new(id.clone(),"Vocal More：短按切换／按住说话").preferred_trigger(Some(trigger(key).as_str()))).collect();
        let reply=tokio::select!{
            _=cancelled(state,revision)=>return Ok(()),
            request=tokio::time::timeout(Duration::from_secs(120),portal.bind_shortcuts(&session,&preferred,None,Default::default()))=>request.context("系统快捷键授权超时")??.response()?,
        };
        let allowed:HashSet<_>=config.bindings.iter().map(|(id,_)|id.as_str()).collect();
        ensure!(reply.shortcuts().len()==allowed.len() && reply.shortcuts().iter().all(|s|allowed.contains(s.id()) && !s.trigger_description().trim().is_empty()),"快捷键未全部获得系统授权；已恢复原有一键切换");
        ensure!(current(state,revision),"快捷键配置已变化");
        let labels=reply.shortcuts().iter().map(|s|(s.id().into(),s.trigger_description().into())).collect();
        let _=output.try_send(Event{revision,active:true,message:String::new(),labels});
        let mut down=HashSet::new();
        loop {
            tokio::select!{
                biased;
                _=cancelled(state,revision)=>break,
                _=closed.next()=>anyhow::bail!("系统快捷键会话已关闭，正在恢复一键切换"),
                message=signals.next()=>{
                    let message=message.context("快捷键连接已断开")??;
                    let member=message.header().member().map(|s|s.as_str().to_owned()).unwrap_or_default();
                    if !matches!(member.as_str(),"Activated"|"Deactivated") {continue;}
                    let (session,id,_timestamp,_options):(zbus::zvariant::OwnedObjectPath,String,u64,std::collections::HashMap<String,zbus::zvariant::OwnedValue>)=message.body().deserialize()?;
                    if session.as_str()!=path || !allowed.contains(id.as_str()) || !current(state,revision) {continue;}
                    if member=="Activated" && down.insert(id.clone()) {
                        commands.request("platform_shortcut_pressed",json!({"shortcut_id":id}));
                    } else if member=="Deactivated" && down.remove(&id) {
                        commands.request("platform_shortcut_released",json!({"shortcut_id":id}));
                    }
                },
            }
        }
        commands.request("platform_shortcut_cancel",json!({}));
        Ok::<_,anyhow::Error>(())
    }.await;
    let _ = tokio::time::timeout(Duration::from_millis(300), session.close()).await;
    result
}
#[cfg(test)]
mod tests {
    #[test]
    fn portal_triggers_use_xdg_modifier_names() {
        assert_eq!(super::trigger("<Control><Alt>r"), "CTRL+ALT+r");
        assert_eq!(super::trigger("F1"), "F1");
    }
    #[test]
    #[ignore = "requires the private GNOME portal runner"]
    fn linux_real_portal_gestures() -> anyhow::Result<()> {
        use super::*;
        use crate::bridge::{BackendDriver, UiEvent};
        let output = std::path::PathBuf::from(
            std::env::var_os("VOCAL_MORE_GESTURE_TEST_OUTPUT")
                .context("private runner required")?,
        );
        ensure!(
            std::env::var("VOCAL_MORE_PRIVATE_GESTURE_TEST").as_deref() == Ok("1"),
            "private runner required"
        );
        let data = tempfile::tempdir()?;
        let (driver, sink, events) = BackendDriver::start(
            vocal_more_backend::application::Options::new(data.path().into()),
        )?;
        let worker = Worker::new(sink, "io.github.sm_yjr.VocalMore.Test".into())?;
        worker.configure(
            &json!({"gesture_enabled":true,"items":[{"id":"fixture-hold","accelerator":"F8"}]}),
        );
        std::fs::write(output.join("phase"), "waiting")?;
        let end = std::time::Instant::now() + Duration::from_secs(80);
        loop {
            if let Some(event) = worker.poll() {
                ensure!(event.active, "portal binding failed: {}", event.message);
                break;
            }
            ensure!(std::time::Instant::now() < end, "portal approval timed out");
            std::thread::sleep(Duration::from_millis(20));
        }
        std::fs::write(output.join("phase"), "bound")?;
        fn next(events: &async_channel::Receiver<UiEvent>) -> String {
            let end = std::time::Instant::now() + Duration::from_secs(10);
            while std::time::Instant::now() < end {
                if let Ok(UiEvent::Request(request)) = events.try_recv()
                    && request.method.starts_with("platform_shortcut_")
                {
                    return request.method;
                }
                std::thread::sleep(Duration::from_millis(5));
            }
            panic!("native key event timed out")
        }
        assert_eq!(next(&events), "platform_shortcut_pressed");
        let held = std::time::Instant::now();
        assert_eq!(next(&events), "platform_shortcut_released");
        ensure!(
            held.elapsed() >= Duration::from_millis(600),
            "hold interval was lost"
        );
        std::fs::write(output.join("phase"), "tap")?;
        assert_eq!(next(&events), "platform_shortcut_pressed");
        let tap = std::time::Instant::now();
        assert_eq!(next(&events), "platform_shortcut_released");
        ensure!(
            tap.elapsed() < Duration::from_millis(350),
            "tap interval was lost"
        );
        std::fs::write(output.join("phase"), "close-held")?;
        assert_eq!(next(&events), "platform_shortcut_pressed");
        worker.close();
        assert_eq!(next(&events), "platform_shortcut_cancel");
        driver.close();
        let end = std::time::Instant::now() + Duration::from_secs(5);
        while !worker.finished() || !driver.finished() {
            ensure!(
                std::time::Instant::now() < end,
                "gesture owner did not shut down"
            );
            std::thread::sleep(Duration::from_millis(10));
        }
        std::fs::write(
            output.join("rust-gestures.json"),
            serde_json::to_vec(
                &json!({"hold_release":true,"short_tap":true,"repeat_suppressed":true,"held_key_close_cancelled":true}),
            )?,
        )?;
        Ok(())
    }
}
