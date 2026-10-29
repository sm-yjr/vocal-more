// SPDX-License-Identifier: GPL-3.0-only
//! Session-bus tray/menu owner. Callbacks only enqueue intents; no audio or IO on UI.
use crate::bridge::CommandSink;
use anyhow::Result;
use serde_json::json;
use std::{
    collections::HashMap,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicU32, Ordering},
    },
    time::Duration,
};
use zbus::zvariant::{OwnedObjectPath, OwnedValue, Structure, Value};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Model {
    pub state: String,
    pub notice: String,
    pub english: bool,
}
impl Default for Model {
    fn default() -> Self {
        Self {
            state: "initializing".into(),
            notice: String::new(),
            english: false,
        }
    }
}
type IconPixmap = Vec<(i32, i32, Vec<u8>)>;
type ToolTip = (String, IconPixmap, String, String);
type Properties = HashMap<String, OwnedValue>;
type Layout = (i32, Properties, Vec<OwnedValue>);
struct Menu {
    model: Arc<Mutex<Model>>,
    commands: CommandSink,
    revision: Arc<AtomicU32>,
}
impl Menu {
    fn rows(&self) -> Vec<(i32, String, bool)> {
        let model = self.model.lock().unwrap();
        let en = model.english;
        let idle = model.state == "idle";
        let ready = !matches!(model.state.as_str(), "initializing" | "saving");
        let status = match (model.state.as_str(), en) {
            ("idle", false) => "就绪",
            ("idle", true) => "Ready",
            ("recording", false) => "正在聆听",
            ("recording", true) => "Listening",
            ("starting", false) => "正在连接",
            ("starting", true) => "Connecting",
            ("saving", false) => "正在保存并退出",
            ("saving", true) => "Saving and quitting",
            (_, false) => "正在处理",
            (_, true) => "Processing",
        };
        vec![
            (
                1,
                if model.notice.is_empty() {
                    status.into()
                } else {
                    model.notice.chars().take(160).collect()
                },
                false,
            ),
            (
                2,
                match (idle, en) {
                    (true, false) => "开始录音",
                    (true, true) => "Start recording",
                    (false, false) => "结束录音",
                    (false, true) => "Finish recording",
                }
                .into(),
                ready,
            ),
            (
                3,
                if en { "Cancel" } else { "取消录音" }.into(),
                ready && !idle,
            ),
            (
                4,
                if en {
                    "Copy last result"
                } else {
                    "复制上次结果"
                }
                .into(),
                ready,
            ),
            (5, if en { "Settings…" } else { "设置…" }.into(), ready),
            (
                6,
                if en {
                    "Quit Vocal More"
                } else {
                    "退出 Vocal More"
                }
                .into(),
                ready,
            ),
        ]
    }
    fn properties(&self, id: i32, names: &[String]) -> Properties {
        let Some((_, label, enabled)) = self.rows().into_iter().find(|row| row.0 == id) else {
            return HashMap::new();
        };
        let mut props = HashMap::from([
            (
                "label".into(),
                OwnedValue::from(zbus::zvariant::Str::from(label)),
            ),
            ("enabled".into(), OwnedValue::from(enabled)),
            ("visible".into(), OwnedValue::from(true)),
        ]);
        if !names.is_empty() {
            props.retain(|key, _| names.contains(key));
        }
        props
    }
    fn click(&self, id: i32, event: &str) {
        if event != "clicked" || !self.rows().iter().any(|row| row.0 == id && row.2) {
            return;
        }
        let method = match id {
            2 => "toggle_recording",
            3 => "cancel",
            4 => "platform_copy_last",
            5 => "platform_show_settings",
            6 => "platform_quit",
            _ => return,
        };
        self.commands.request(method, json!({}));
    }
}
#[zbus::interface(name = "com.canonical.dbusmenu")]
impl Menu {
    #[zbus(property)]
    fn version(&self) -> u32 {
        3
    }
    #[zbus(property)]
    fn text_direction(&self) -> &str {
        "ltr"
    }
    #[zbus(property)]
    fn status(&self) -> &str {
        "normal"
    }
    #[zbus(property)]
    fn icon_theme_path(&self) -> Vec<String> {
        vec![]
    }
    #[zbus(out_args("revision", "layout"))]
    fn get_layout(
        &self,
        parent_id: i32,
        recursion_depth: i32,
        property_names: Vec<String>,
    ) -> (u32, Layout) {
        let children = if parent_id == 0 && recursion_depth != 0 {
            self.rows()
                .into_iter()
                .map(|(id, _, _)| {
                    let row = Structure::from((
                        id,
                        self.properties(id, &property_names),
                        Vec::<OwnedValue>::new(),
                    ));
                    OwnedValue::try_from(Value::from(row)).expect("owned menu node")
                })
                .collect()
        } else {
            vec![]
        };
        (
            self.revision.load(Ordering::Acquire),
            (
                parent_id,
                self.properties(parent_id, &property_names),
                children,
            ),
        )
    }
    fn get_group_properties(
        &self,
        ids: Vec<i32>,
        property_names: Vec<String>,
    ) -> Vec<(i32, Properties)> {
        let ids = if ids.is_empty() {
            self.rows().iter().map(|r| r.0).collect()
        } else {
            ids
        };
        ids.into_iter()
            .map(|id| (id, self.properties(id, &property_names)))
            .collect()
    }
    fn get_property(&self, id: i32, name: String) -> zbus::fdo::Result<OwnedValue> {
        self.properties(id, &[])
            .remove(&name)
            .ok_or_else(|| zbus::fdo::Error::InvalidArgs("Unknown menu property".into()))
    }
    fn event(&self, id: i32, event_id: String, _data: OwnedValue, _timestamp: u32) {
        self.click(id, &event_id);
    }
    fn event_group(&self, events: Vec<(i32, String, OwnedValue, u32)>) -> Vec<i32> {
        for (id, event, _, _) in events {
            self.click(id, &event);
        }
        vec![]
    }
    fn about_to_show(&self, _id: i32) -> bool {
        false
    }
    #[zbus(out_args("updates_needed", "id_errors"))]
    fn about_to_show_group(&self, _ids: Vec<i32>) -> (Vec<i32>, Vec<i32>) {
        (vec![], vec![])
    }
    #[zbus(signal)]
    async fn layout_updated(
        emitter: &zbus::object_server::SignalEmitter<'_>,
        revision: u32,
        parent: i32,
    ) -> zbus::Result<()>;
}
struct Item {
    commands: CommandSink,
}
#[zbus::interface(name = "org.kde.StatusNotifierItem")]
impl Item {
    #[zbus(property)]
    fn category(&self) -> &str {
        "ApplicationStatus"
    }
    #[zbus(property)]
    fn id(&self) -> &str {
        "vocal-more"
    }
    #[zbus(property)]
    fn title(&self) -> &str {
        "Vocal More"
    }
    #[zbus(property)]
    fn status(&self) -> &str {
        "Active"
    }
    #[zbus(property)]
    fn window_id(&self) -> u32 {
        0
    }
    #[zbus(property)]
    fn icon_name(&self) -> &str {
        "io.github.sm_yjr.VocalMore"
    }
    #[zbus(property)]
    fn icon_pixmap(&self) -> Vec<(i32, i32, Vec<u8>)> {
        vec![]
    }
    #[zbus(property)]
    fn tool_tip(&self) -> ToolTip {
        (String::new(), vec![], "Vocal More".into(), String::new())
    }
    #[zbus(property)]
    fn overlay_icon_name(&self) -> &str {
        ""
    }
    #[zbus(property)]
    fn attention_icon_name(&self) -> &str {
        ""
    }
    #[zbus(property)]
    fn attention_movie_name(&self) -> &str {
        ""
    }
    #[zbus(property)]
    fn item_is_menu(&self) -> bool {
        true
    }
    #[zbus(property)]
    fn menu(&self) -> OwnedObjectPath {
        OwnedObjectPath::try_from("/MenuBar").unwrap()
    }
    fn activate(&self, _x: i32, _y: i32) {
        self.commands.request("platform_show_settings", json!({}));
    }
    fn secondary_activate(&self, _x: i32, _y: i32) {
        self.commands.request("toggle_recording", json!({}));
    }
    fn context_menu(&self, _x: i32, _y: i32) {}
    fn scroll(&self, _delta: i32, _orientation: String) {}
}
pub struct Tray {
    model: Arc<Mutex<Model>>,
    closed: Arc<AtomicBool>,
    finished: Arc<AtomicBool>,
    available: Arc<AtomicBool>,
}
impl Tray {
    pub fn new(commands: CommandSink) -> Result<Self> {
        let model = Arc::new(Mutex::new(Model::default()));
        let closed = Arc::new(AtomicBool::new(false));
        let finished = Arc::new(AtomicBool::new(false));
        let available = Arc::new(AtomicBool::new(false));
        let (data, stop, done, connected) = (
            model.clone(),
            closed.clone(),
            finished.clone(),
            available.clone(),
        );
        std::thread::Builder::new()
            .name("vocal-more-tray".into())
            .spawn(move || {
                if let Ok(runtime) = tokio::runtime::Builder::new_current_thread()
                    .enable_all()
                    .build()
                {
                    let result: Result<()> = runtime.block_on(async {
                        let name = format!("org.kde.StatusNotifierItem-{}-1", std::process::id());
                        let revision = Arc::new(AtomicU32::new(1));
                        let connection = zbus::connection::Builder::session()?
                            .name(name.clone())?
                            .serve_at(
                                "/StatusNotifierItem",
                                Item {
                                    commands: commands.clone(),
                                },
                            )?
                            .serve_at(
                                "/MenuBar",
                                Menu {
                                    model: data.clone(),
                                    commands,
                                    revision: revision.clone(),
                                },
                            )?
                            .build()
                            .await?;
                        let watcher = zbus::Proxy::new(
                            &connection,
                            "org.kde.StatusNotifierWatcher",
                            "/StatusNotifierWatcher",
                            "org.kde.StatusNotifierWatcher",
                        )
                        .await?;
                        let bus = zbus::fdo::DBusProxy::new(&connection).await?;
                        let mut owner = String::new();
                        let mut previous = Model::default();

                        let mut tick = tokio::time::interval(Duration::from_millis(200));
                        while !stop.load(Ordering::Acquire) {
                            tick.tick().await;
                            let current = tokio::time::timeout(
                                Duration::from_millis(300),
                                bus.get_name_owner(zbus::names::BusName::try_from(
                                    "org.kde.StatusNotifierWatcher",
                                )?),
                            )
                            .await;
                            let current = match current {
                                Ok(Ok(value)) => value.to_string(),
                                _ => String::new(),
                            };
                            if current.is_empty() {
                                owner.clear();
                                connected.store(false, Ordering::Release);
                            } else if current != owner {
                                let registered = tokio::time::timeout(
                                    Duration::from_millis(300),
                                    watcher.call::<_, _, ()>(
                                        "RegisterStatusNotifierItem",
                                        &(name.as_str(),),
                                    ),
                                )
                                .await;
                                if matches!(registered, Ok(Ok(()))) {
                                    owner = current;
                                    connected.store(true, Ordering::Release);
                                }
                            }
                            let value = data.lock().unwrap().clone();
                            if value != previous {
                                previous = value;
                                let next_revision =
                                    revision.fetch_add(1, Ordering::AcqRel).wrapping_add(1);
                                let emitter = zbus::object_server::SignalEmitter::new(
                                    &connection,
                                    "/MenuBar",
                                )?;
                                let _ = Menu::layout_updated(&emitter, next_revision, 0).await;
                            }
                        }
                        Ok(())
                    });
                    if result.is_err() {
                        eprintln!("Vocal More: system tray unavailable");
                    }
                }
                connected.store(false, Ordering::Release);
                done.store(true, Ordering::Release);
            })?;
        Ok(Self {
            model,
            closed,
            finished,
            available,
        })
    }
    pub fn update(&self, model: Model) {
        *self.model.lock().unwrap() = model;
    }
    pub fn available(&self) -> bool {
        self.available.load(Ordering::Acquire)
    }
    pub fn close(&self) {
        self.closed.store(true, Ordering::Release);
    }
    pub fn finished(&self) -> bool {
        self.finished.load(Ordering::Acquire)
    }
}
impl Drop for Tray {
    fn drop(&mut self) {
        self.close();
    }
}
