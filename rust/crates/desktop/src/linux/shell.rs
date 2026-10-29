// SPDX-License-Identifier: GPL-3.0-only
//! Companion GNOME Shell actor transport. The extension also offers bounded native clipboard transfer; it owns no audio.
use super::capsule::Model;
use crate::bridge::CommandSink;
use anyhow::Result;
use futures_lite::StreamExt;
use serde_json::json;
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, Ordering},
};
use std::time::Duration;
use tokio::sync::Notify;
pub const NAME: &str = "org.gnome.Shell.Extensions.VocalMore";
pub const PATH: &str = "/org/gnome/Shell/Extensions/VocalMore";
pub struct ShellCapsule {
    model: Arc<Mutex<Model>>,
    changed: Arc<Notify>,
    closed: Arc<AtomicBool>,
    finished: Arc<AtomicBool>,
    available: Arc<AtomicBool>,
    locked: Arc<AtomicBool>,
}
impl ShellCapsule {
    pub fn new(commands: CommandSink) -> Result<Self> {
        let model = Arc::new(Mutex::new(Model::default()));
        let changed = Arc::new(Notify::new());
        let wake = changed.clone();
        let closed = Arc::new(AtomicBool::new(false));
        let finished = Arc::new(AtomicBool::new(false));
        let available = Arc::new(AtomicBool::new(false));
        let locked = Arc::new(AtomicBool::new(false));
        let lock_state = locked.clone();
        let (data, stop, done, connected) = (
            model.clone(),
            closed.clone(),
            finished.clone(),
            available.clone(),
        );
        std::thread::Builder::new().name("vocal-more-gnome-capsule".into()).spawn(move|| {
            if let Ok(runtime)=tokio::runtime::Builder::new_current_thread().enable_all().build() {
                runtime.block_on(async {
                    let Ok(connection)=zbus::Connection::session().await else{return;};
                    let Ok(proxy)=zbus::Proxy::new(&connection,NAME,PATH,NAME).await else{return;};
                    let Ok(mut actions)=proxy.receive_signal("Action").await else{return;};
                    let screen_saver = zbus::Proxy::new(&connection, "org.gnome.ScreenSaver", "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver").await.ok();
                    let mut tick=tokio::time::interval(Duration::from_millis(100));
                    while !stop.load(Ordering::Acquire) {
                        let send = tokio::select! {
                            biased;
                            _=wake.notified()=>true,
                            _=tick.tick()=> {
                                if let Some(saver) = &screen_saver
                                    && let Ok(Ok(active)) = tokio::time::timeout(Duration::from_millis(50), saver.call::<_,_,bool>("GetActive", &())).await {
                                        let was_locked = lock_state.swap(active, Ordering::AcqRel);
                                        if active && !was_locked { commands.request("cancel", json!({})); }
                                }
                                true
                            },
                            signal=actions.next()=> {
                                let Some(signal)=signal else {break;};
                                if let Ok((action,))=signal.body().deserialize::<(String,)>()
                                    && matches!(action.as_str(),"cancel"|"stop") {
                                    commands.request(&action,json!({}));
                                }
                                false
                            },
                        };
                        if send && !stop.load(Ordering::Acquire) {
                            let value=data.lock().unwrap().clone();
                            let text=serde_json::to_string(&value).unwrap_or_default();
                            let sent=tokio::time::timeout(Duration::from_millis(250),proxy.call::<_,_,()>("Update",&(text,))).await;
                            connected.store(matches!(sent,Ok(Ok(()))),Ordering::Release);
                        }
                    }
                    let mut value=data.lock().unwrap().clone();value.active=false;
                    let _=tokio::time::timeout(Duration::from_millis(250),proxy.call::<_,_,()>("Update",&(serde_json::to_string(&value).unwrap_or_default(),))).await;
                });
            }
            done.store(true,Ordering::Release);
        })?;
        Ok(Self {
            model,
            changed,
            closed,
            finished,
            available,
            locked,
        })
    }
    pub fn update(&self, mut value: Model) {
        value.bound();
        let mut model = self.model.lock().unwrap();
        let changed = model.active != value.active || model.state != value.state;
        *model = value;
        drop(model);
        if changed {
            self.changed.notify_one();
        }
    }
    pub fn locked(&self) -> bool {
        self.locked.load(Ordering::Acquire)
    }
    pub fn available(&self) -> bool {
        self.available.load(Ordering::Acquire)
    }
    pub fn close(&self) {
        self.closed.store(true, Ordering::Release);
        self.changed.notify_one();
    }
    pub fn finished(&self) -> bool {
        self.finished.load(Ordering::Acquire)
    }
}
impl Drop for ShellCapsule {
    fn drop(&mut self) {
        self.close();
    }
}

#[cfg(test)]
#[path = "shell_live_test.rs"]
mod live_test;
