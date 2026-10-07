// SPDX-License-Identifier: GPL-3.0-only
//! Main-thread GPUI Kit acceptance using the production Settings view, real
//! Metal pixels, native pointer/keyboard dispatch and the real BackendDriver.
fn main() {
    #[cfg(target_os = "macos")]
    macos::run().unwrap();
    #[cfg(not(target_os = "macos"))]
    panic!("settings rendering acceptance requires GPUI Kit's real macOS Metal renderer");
}

#[cfg(target_os = "macos")]
mod macos {
    use anyhow::{Context, Result, bail, ensure};
    use base64::{Engine, engine::general_purpose::STANDARD};
    use gpui_kit::{
        AnyWindowHandle, App, ElementId, Entity, HeadlessAppContext, ScrollDelta, Window,
        component::{Theme, ThemeMode},
        point, px, size,
        test::{ElementSnapshot, TestWindowExt},
    };
    use serde_json::{Value, json};
    use std::{
        collections::HashMap,
        fs,
        path::{Path, PathBuf},
        sync::Arc,
        thread,
        time::{Duration, Instant},
    };
    use vocal_more_backend::{application::Options, config::ConfigRepository, history::History};
    use vocal_more_core::recording::RecordingStore;
    use vocal_more_desktop::{
        assets::Assets,
        bridge::{BackendDriver, CommandSink, UI_QUEUE_CAPACITY, UiEvent},
        settings::{
            self, Settings,
            schema::{self, Field, Kind, Tab},
        },
    };

    struct Harness {
        window: AnyWindowHandle,
        view: Entity<Settings>,
        driver: BackendDriver,
        sink: CommandSink,
        events: async_channel::Receiver<UiEvent>,
        terminal: HashMap<u64, (String, Value)>,
        intents: Vec<(String, Value)>,
        data: tempfile::TempDir,
        output: PathBuf,
        shots: Vec<Value>,
        configuration_coverage: Value,
        preview_events: Vec<Value>,
        // Entity handles must drop before HeadlessAppContext's leak detector.
        cx: HeadlessAppContext,
    }
    impl Harness {
        fn start() -> Result<Self> {
            let data = tempfile::tempdir()?;
            let mut repository = ConfigRepository::open(&data.path().join("config.yaml"))?;
            repository.update("ui.onboarding_completed", &json!(true))?;
            repository.update("ui.advanced_settings", &json!(true))?;
            repository.update("ui.language", &json!("en"))?;
            seed_recording(data.path())?;
            let mut options = Options::new(data.path().into());
            options.allow_test_sources = true;
            let (driver, sink, events) = BackendDriver::start(options)?;
            let deadline = Instant::now() + Duration::from_secs(5);
            let snapshot = loop {
                if let Ok(UiEvent::Backend { method, params }) = events.try_recv()
                    && method == "initialized"
                {
                    break params;
                }
                ensure!(Instant::now() < deadline, "backend did not initialize");
                thread::sleep(Duration::from_millis(2));
            };
            assert_eq!(snapshot["config"]["api_key"], "");
            let mut cx = HeadlessAppContext::with_platform(
                gpui_kit::platform::current_platform(true).text_system(),
                Arc::new(Assets),
                gpui_kit::platform::current_headless_renderer,
            );
            cx.update(gpui_kit::init);
            cx.update(vocal_more_desktop::theme::install)?;
            let (window, view) = cx.update(|cx| settings::open(snapshot, sink.clone(), cx))?;
            let output = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("../../../.build/settings-rendering");
            fs::create_dir_all(&output)?;
            let mut this = Self {
                cx,
                window,
                view,
                driver,
                sink,
                events,
                terminal: HashMap::new(),
                intents: vec![],
                data,
                output,
                shots: vec![],
                configuration_coverage: Value::Null,
                preview_events: vec![],
            };
            // GPUI emits Focus/Blur only for the active platform window;
            // headless construction alone does not activate TestPlatform.
            this.window(|window, _| window.activate_window())?;
            this.pump()?;
            Ok(this)
        }
        fn window<R>(&mut self, operation: impl FnOnce(&mut Window, &mut App) -> R) -> Result<R> {
            self.cx
                .update_window(self.window, |_, window, cx| operation(window, cx))
        }
        fn pump(&mut self) -> Result<()> {
            self.pump_backend()?;
            self.window(|window, cx| window.render_frame(cx))?;
            Ok(())
        }
        fn pump_backend(&mut self) -> Result<()> {
            self.cx.run_until_parked();
            let view = self.view.clone();
            while let Ok(event) = self.events.try_recv() {
                match event {
                    UiEvent::Request(mut request) => {
                        self.intents
                            .push((request.method.clone(), request.params.clone()));
                        // Hardware-free source injection is test harness ownership;
                        // the production UI still emits its real startMicTest intent.
                        if request.method == "ui_action"
                            && request.params["action"] == "startMicTest"
                        {
                            request.params["source"] = json!({"kind":"stream"});
                        }
                        // These belong to Host's platform executor, never the
                        // Application RPC dispatcher. Coverage injects a
                        // synthetic key event through the real UI event route.
                        if matches!(
                            request.method.as_str(),
                            "begin_hotkey_capture" | "end_hotkey_capture"
                        ) {
                            continue;
                        }
                        self.driver.send(request);
                    }
                    UiEvent::Backend { method, params } => {
                        if method.starts_with("mic_test_") || method == "state_changed" {
                            self.preview_events
                                .push(json!({"event":method,"params":params}));
                            if self.preview_events.len() > 24 {
                                self.preview_events.remove(0);
                            }
                        }
                        if method == "backend_disconnected" {
                            bail!("backend disconnected: {}", params["message"]);
                        }
                        if method == "state_changed" {
                            self.sink.update_session(
                                params["state"].as_str().unwrap_or("idle"),
                                params["generation"].as_u64().unwrap_or_default(),
                            );
                        }
                        if matches!(method.as_str(), "rpc_response" | "rpc_error") {
                            self.terminal.insert(
                                params["request_id"].as_u64().unwrap(),
                                (method.clone(), params.clone()),
                            );
                        }
                        self.cx.update_window(self.window, |_, window, cx| {
                            view.update(cx, |view, cx| view.on_event(&method, &params, window, cx))
                        })?;
                    }
                }
            }
            self.cx.run_until_parked();
            Ok(())
        }
        fn wait(&mut self, predicate: impl Fn(&Self) -> bool) -> Result<()> {
            let deadline = Instant::now() + Duration::from_secs(5);
            while !predicate(self) {
                self.pump()?;
                ensure!(
                    Instant::now() < deadline,
                    "UI/backend condition did not settle"
                );
                thread::sleep(Duration::from_millis(2));
            }
            self.pump()
        }
        fn call(&mut self, method: &str, params: Value) -> Result<Value> {
            let id = self.sink.request(method, params);
            self.wait(|this| this.terminal.contains_key(&id))
                .with_context(|| format!("waiting for {method} request {id}"))?;
            self.response(id)
        }
        fn wait_preview(
            &mut self,
            label: &str,
            predicate: impl Fn(&mut Self) -> bool,
        ) -> Result<()> {
            // Backend has a real five-second preview safety timer. Metal
            // rendering on the hosted runner must not consume that deadline
            // once per polling iteration or PCM append. UI tasks still run.
            let deadline = Instant::now() + Duration::from_secs(5);
            while !predicate(self) {
                self.pump_backend()?;
                ensure!(
                    Instant::now() < deadline,
                    "{label} did not settle; preview events: {:?}; recent intents: {:?}",
                    self.preview_events,
                    self.intents
                        .iter()
                        .rev()
                        .take(8)
                        .map(|(method, params)| (method, &params["action"]))
                        .collect::<Vec<_>>()
                );
                thread::sleep(Duration::from_millis(2));
            }
            self.pump_backend()
        }
        fn call_preview(&mut self, method: &str, params: Value) -> Result<Value> {
            let id = self.sink.request(method, params);
            self.wait_preview(&format!("preview {method} request {id}"), |this| {
                this.terminal.contains_key(&id)
            })?;
            self.response(id)
        }
        fn response(&mut self, id: u64) -> Result<Value> {
            let (method, params) = self.terminal.remove(&id).unwrap();
            ensure!(
                method == "rpc_response",
                "backend rejected: {}",
                params["message"]
            );
            Ok(params["result"].clone())
        }
        fn set(&mut self, key: &str, value: Value) -> Result<()> {
            self.call("set_config", json!({"key":key,"value":value}))?;
            Ok(())
        }
        fn settle(&mut self) -> Result<()> {
            // A status response is a barrier after earlier GUI intents, while
            // leaving a revealed key and focused editor contents untouched.
            self.call("status", json!({}))?;
            Ok(())
        }
        fn click(&mut self, id: impl Into<ElementId>) -> Result<()> {
            let id = id.into();
            self.window(|window, cx| window.click(id, cx))?;
            self.pump()
        }
        fn edit(&mut self, id: &'static str, value: &str) -> Result<()> {
            self.click(id)?;
            self.window(|window, cx| {
                assert_eq!(window.find(id).focused(), Some(true));
                window.press("cmd-a", cx);
                if value.is_empty() {
                    window.press("backspace", cx);
                } else {
                    window.input(value, cx);
                }
                window.press("enter", cx);
            })?;
            self.settle()
        }
        fn find(&mut self, id: impl Into<ElementId>) -> Result<ElementSnapshot> {
            let id = id.into();
            self.window(|window, _| window.find(id))
        }
        fn control_id(&mut self, key: &str) -> Result<ElementId> {
            let view = self.view.clone();
            self.cx
                .update(|cx| view.read(cx).testing_control_id(key))
                .with_context(|| format!("no production control identity for {key}"))
        }
        fn select_index(&mut self, key: &'static str, target: usize) -> Result<()> {
            self.scroll_to(key)?;
            self.window(|window, cx| window.within(key).click("input", cx))?;
            self.pump()?;
            ensure!(
                self.find(key)?.expanded() == Some(true),
                "{key} did not open"
            );
            let view = self.view.clone();
            for _ in 0..30 {
                let cursor = self
                    .cx
                    .update(|cx| view.read(cx).testing_select_cursor(key, cx));
                if cursor == Some(target) {
                    self.window(|window, cx| window.press("enter", cx))?;
                    return self.settle();
                }
                let key = if cursor.is_some_and(|cursor| cursor > target) {
                    "up"
                } else {
                    "down"
                };
                self.window(|window, cx| window.press(key, cx))?;
                self.pump()?;
            }
            bail!("native dropdown cursor did not reach {key} option {target}")
        }
        fn drag_slider(&mut self, id: ElementId, fraction: f32) -> Result<()> {
            self.scroll_to(id.clone())?;
            self.window(|window, cx| {
                let scope = window.within(id);
                let thumb = scope.find(("slider-thumb", 0u32)).bounds();
                let track = scope.find("slider-bar-container").bounds();
                window.drag(
                    thumb.center(),
                    point(track.left() + track.size.width * fraction, track.center().y),
                    cx,
                );
            })?;
            self.settle()
        }
        fn ensure_toggle(&mut self, key: &'static str, value: bool) -> Result<()> {
            self.scroll_to(key)?;
            if self.find(key)?.checked() != Some(value) {
                self.click(key)?;
                self.settle()?;
            }
            ensure!(
                self.find(key)?.checked() == Some(value),
                "toggle {key} did not become {value}"
            );
            Ok(())
        }
        fn resize(&mut self, width: f32, height: f32) -> Result<()> {
            self.window(|window, cx| {
                let expected = size(px(width), px(height));
                window.resize(expected);
                // TestPlatform::resize changes the render target without the
                // native resize callback. Use GPUI's public callback hook so
                // the layout viewport changes along with the Metal texture.
                window.bounds_changed(cx);
                assert_eq!(window.viewport_size(), expected);
            })?;
            self.pump()
        }
        fn scroll_to(&mut self, id: impl Into<ElementId>) -> Result<()> {
            let id = id.into();
            let page = self.window(|window, _| {
                if window.try_find("settings-page").is_some() {
                    "settings-page"
                } else {
                    "onboarding-page"
                }
            })?;
            for _ in 0..30 {
                let target = self.find(id.clone())?;
                let viewport = self.find(page)?.bounds();
                if target.visible() && viewport.contains(&target.bounds().center()) {
                    return Ok(());
                }
                let delta = if target.bounds().center().y < viewport.top() {
                    220.
                } else {
                    -220.
                };
                self.window(|window, cx| {
                    window.scroll(page, ScrollDelta::Pixels(point(px(0.), px(delta))), cx)
                })?;
            }
            bail!("target remained outside scroll viewport: {id:?}")
        }
        fn top(&mut self) -> Result<()> {
            self.window(|window, cx| {
                window.scroll(
                    "settings-page",
                    ScrollDelta::Pixels(point(px(0.), px(10000.))),
                    cx,
                )
            })
        }
        fn shot(&mut self, name: &str) -> Result<()> {
            self.pump()?;
            let image = self
                .cx
                .capture_screenshot(self.window)
                .context("real Metal renderer unavailable")?;
            let first = &image.as_raw()[0..4];
            let differing = image
                .as_raw()
                .as_chunks::<4>()
                .0
                .iter()
                .filter(|pixel| pixel.as_slice() != first)
                .count();
            ensure!(differing > 500, "image contains no rendered UI");
            let (width, height) = image.dimensions();
            let surface = image.get_pixel(2, height / 2).0;
            let brightness = surface[0] as u16 + surface[1] as u16 + surface[2] as u16;
            if name.contains("-light-") {
                ensure!(
                    brightness > 500,
                    "light theme did not change actual surface pixels"
                );
            }
            if name.contains("-dark-") {
                ensure!(
                    brightness < 400,
                    "dark theme did not change actual surface pixels"
                );
            }
            image.save(self.output.join(format!("{name}.png")))?;
            let observations =
                self.window(|window, _| gpui_kit::base::test_support::snapshots(window))?;
            let viewport = self.window(|window, _| window.viewport_size())?;
            let scale = self.window(|window, _| window.scale_factor())?;
            ensure!(
                width == (f32::from(viewport.width) * scale).round() as u32
                    && height == (f32::from(viewport.height) * scale).round() as u32,
                "Metal texture and layout viewport disagree in {name}"
            );
            for item in &observations {
                if matches!(
                    item.role(),
                    Some(
                        gpui_kit::Role::Button
                            | gpui_kit::Role::TextInput
                            | gpui_kit::Role::PasswordInput
                            | gpui_kit::Role::Switch
                            | gpui_kit::Role::ComboBox
                            | gpui_kit::Role::Slider
                    )
                ) {
                    let bounds = item.bounds();
                    ensure!(
                        bounds.origin.x >= px(-1.) && bounds.right() <= viewport.width + px(1.),
                        "horizontal layout overflow in {name}: {item:?}"
                    );
                }
            }
            self.shots.push(json!({"name":name,"width":width,"height":height,"logical_width":f32::from(viewport.width),"logical_height":f32::from(viewport.height),"surface_rgba":surface,"non_background_pixels":differing,"native_elements":observations.len()}));
            Ok(())
        }
        fn control_pixels(&mut self, id: &'static str) -> Result<Vec<u8>> {
            self.pump()?;
            let bounds = self.find(id)?.bounds();
            let viewport = self.window(|window, _| window.viewport_size())?;
            let image = self.cx.capture_screenshot(self.window)?;
            let scale = image.width() as f32 / f32::from(viewport.width);
            let x0 = (f32::from(bounds.left()) * scale).round().max(0.) as u32;
            let y0 = (f32::from(bounds.top()) * scale).round().max(0.) as u32;
            let x1 = (f32::from(bounds.right()) * scale)
                .round()
                .min(image.width() as f32) as u32;
            let y1 = (f32::from(bounds.bottom()) * scale)
                .round()
                .min(image.height() as f32) as u32;
            let mut pixels = Vec::new();
            for y in y0..y1 {
                for x in x0..x1 {
                    pixels.extend_from_slice(&image.get_pixel(x, y).0);
                }
            }
            Ok(pixels)
        }
        fn close(&mut self) -> Result<()> {
            let view = self.view.clone();
            self.cx.update(|cx| {
                view.update(cx, |view, cx| {
                    let flushed = view.flush(cx);
                    view.close();
                    flushed
                })
            })?;
            self.pump()?;
            self.driver.close();
            let deadline = Instant::now() + Duration::from_secs(5);
            while !self.driver.finished() {
                ensure!(
                    Instant::now() < deadline,
                    "driver failed to stop after rendering acceptance"
                );
                thread::sleep(Duration::from_millis(2));
            }
            Ok(())
        }
    }
    impl Drop for Harness {
        fn drop(&mut self) {
            self.driver.close();
        }
    }

    fn seed_recording(path: &Path) -> Result<()> {
        tokio::runtime::Runtime::new()?.block_on(async {
            let history =
                History::open(RecordingStore::open(path.join("recordings")).await?).await?;
            let mut writer = history
                .store()
                .create(1, "qwen3.5-omni-plus-realtime")
                .await?;
            writer.append(&[1, 0].repeat(3200)).await?;
            let record = writer
                .finish("completed", "Synthetic acceptance transcript".into(), None)
                .await?;
            history.register(&record, "realtime_long", "en")?;
            history.update(
                &record.id.to_string(),
                "success",
                Some("Synthetic acceptance transcript"),
                None,
                None,
            )?;
            Ok(())
        })
    }

    fn matrix(this: &mut Harness) -> Result<()> {
        for (prefix, width, height) in [("", 900., 740.), ("minimum-", 640., 480.)] {
            this.resize(width, height)?;
            for (language, english) in [("en", true), ("zh", false)] {
                this.set("ui.language", json!(language))?;
                for (theme, mode) in [("light", ThemeMode::Light), ("dark", ThemeMode::Dark)] {
                    this.cx.update(|cx| Theme::change(mode, None, cx));
                    for tab in Tab::ALL {
                        this.click(tab.id())?;
                        this.settle()?;
                        this.top()?;
                        let button = this.find(tab.id())?;
                        assert_eq!(button.label(), Some(tab.title(english)));
                        let anchor = match tab {
                            Tab::General => "api_key",
                            Tab::Audio => "calibrate-whisper",
                            Tab::Recognition => "asr.model",
                            Tab::Polish => "enable_polish",
                            Tab::Shortcuts => "fn-hotkey",
                            Tab::Dictionary => "dictionary-term",
                            Tab::History => "history-filter",
                        };
                        assert!(
                            this.find(anchor)?.role().is_some(),
                            "selected tab {} did not render its native control",
                            tab.id()
                        );
                        this.shot(&format!("{prefix}{language}-{theme}-{}", tab.id()))?;
                    }
                    this.set("ui.onboarding_completed", json!(false))?;
                    this.shot(&format!("{prefix}{language}-{theme}-onboarding"))?;
                    this.set("ui.onboarding_completed", json!(true))?;
                }
            }
        }
        this.set("ui.language", json!("en"))?;
        this.cx
            .update(|cx| Theme::change(ThemeMode::Light, None, cx));
        this.resize(900., 740.)?;
        Ok(())
    }

    fn saved_value(this: &Harness, key: &str) -> Result<Value> {
        Ok(
            ConfigRepository::open(&this.data.path().join("config.yaml"))?
                .config
                .get(key)
                .clone(),
        )
    }

    fn writes_key(method: &str, params: &Value, key: &str) -> bool {
        (method == "set_config" || method == "preview_config") && params["key"] == key
            || method == "ui_action"
                && matches!(
                    (params["action"].as_str(), key),
                    (Some("setAsrModel"), "asr.model")
                        | (Some("setDevice"), "audio.input_device")
                        | (Some("setActiveHotkeys"), "hotkey.active_hotkeys")
                )
    }

    fn disabled_control(this: &mut Harness, field: Field) -> Result<Value> {
        let id = this.control_id(field.key)?;
        this.scroll_to(id.clone())?;
        let observed = this.find(id.clone())?;
        ensure!(
            observed.disabled() != Some(false),
            "{} advertised an enabled state while its dependency was off",
            field.key
        );
        let before = saved_value(this, field.key)?;
        let mark = this.intents.len();
        if matches!(field.kind, Kind::Slider { .. }) {
            this.drag_slider(id, 0.35)?;
        } else if matches!(field.kind, Kind::Choice(_) | Kind::Model | Kind::Device) {
            this.window(|window, cx| window.within(id.clone()).click("input", cx))?;
            this.pump()?;
            ensure!(
                this.find(id)?.expanded() == Some(false),
                "disabled select opened"
            );
        } else {
            this.click(id)?;
        }
        this.settle()?;
        ensure!(
            saved_value(this, field.key)? == before,
            "disabled {} changed disk config",
            field.key
        );
        ensure!(
            !this.intents[mark..]
                .iter()
                .any(|(method, params)| writes_key(method, params, field.key)),
            "disabled {} dispatched a write",
            field.key
        );
        Ok(
            json!({"key":field.key,"native_disabled_flag":observed.disabled(),"disabled_behavior_verified":true,"pointer_attempt":"ignored","write_intents":0,"disk_unchanged":true}),
        )
    }

    fn configuration_gates(this: &mut Harness) -> Result<Vec<Value>> {
        let mut gates = vec![];
        this.click("general")?;
        this.ensure_toggle("ui.advanced_settings", false)?;
        ensure!(
            this.window(|window, _| window.try_find("api_key").is_none())?,
            "advanced key remained rendered after switching off advanced settings"
        );
        this.ensure_toggle("ui.advanced_settings", true)?;
        this.scroll_to("api_key")?;
        ensure!(this.find("api_key")?.role() == Some(gpui_kit::Role::PasswordInput));
        gates.push(json!({"dependency":"ui.advanced_settings","pointer_off_hides_advanced_control":true,"pointer_on_restores_native_control":true}));

        this.ensure_toggle("auto_paste", false)?;
        for key in ["native_fast_paste", "restore_clipboard"] {
            let field = *schema::FIELDS
                .iter()
                .find(|field| field.key == key)
                .unwrap();
            let mut gate = disabled_control(this, field)?;
            this.ensure_toggle("auto_paste", true)?;
            this.scroll_to(key)?;
            ensure!(this.find(key)?.disabled() != Some(true));
            gate["dependency"] = json!("auto_paste");
            gate["disabled_flag_after_dependency"] = json!(this.find(key)?.disabled());
            gate["enabled_interaction_verified_by_field"] = json!(key);
            gates.push(gate);
            this.ensure_toggle("auto_paste", false)?;
        }
        this.ensure_toggle("auto_paste", true)?;

        this.click("audio")?;
        this.ensure_toggle("audio.highpass_filter", false)?;
        let frequency = *schema::FIELDS
            .iter()
            .find(|field| field.key == "audio.highpass_freq")
            .unwrap();
        let mut gate = disabled_control(this, frequency)?;
        this.ensure_toggle("audio.highpass_filter", true)?;
        let frequency_id = this.control_id(frequency.key)?;
        this.scroll_to(frequency_id.clone())?;
        ensure!(this.find(frequency_id.clone())?.disabled() != Some(true));
        gate["dependency"] = json!("audio.highpass_filter");
        gate["disabled_flag_after_dependency"] = json!(this.find(frequency_id)?.disabled());
        gate["enabled_interaction_verified_by_field"] = json!(frequency.key);
        gates.push(gate);

        this.click("recognition")?;
        let snapshot = this.call("snapshot", json!({}))?;
        let models = snapshot["asr_models"]
            .as_array()
            .context("missing ASR catalog")?;
        let native = models
            .iter()
            .position(|model| model["pipeline"] == "native_asr")
            .context("catalog has no native ASR for the dependency test")?;
        let inline = models
            .iter()
            .position(|model| model["pipeline"] != "native_asr" && model["id"].is_string())
            .context("catalog has no polish-capable ASR")?;
        this.select_index("asr.model", native)?;
        this.click("polish")?;
        let field = *schema::FIELDS
            .iter()
            .find(|field| field.key == "enable_polish")
            .unwrap();
        let mut gate = disabled_control(this, field)?;
        this.click("recognition")?;
        this.select_index("asr.model", inline)?;
        this.click("polish")?;
        this.scroll_to("enable_polish")?;
        ensure!(this.find("enable_polish")?.disabled() != Some(true));
        gate["dependency"] = json!("asr.model: native_asr -> non-native pipeline");
        gate["disabled_flag_after_dependency"] = json!(this.find("enable_polish")?.disabled());
        gate["enabled_interaction_verified_by_field"] = json!("enable_polish");
        gates.push(gate);

        this.ensure_toggle("enable_polish", false)?;
        for field in schema::FIELDS
            .iter()
            .filter(|field| field.key.starts_with("llm."))
        {
            gates.push(disabled_control(this, *field)?);
        }
        this.ensure_toggle("enable_polish", true)?;
        for gate in &mut gates {
            if gate["key"]
                .as_str()
                .is_some_and(|key| key.starts_with("llm."))
            {
                let key = gate["key"].as_str().unwrap().to_owned();
                let id = this.control_id(&key)?;
                this.scroll_to(id.clone())?;
                ensure!(
                    this.find(id.clone())?.disabled() != Some(true),
                    "polish dependency did not enable {key}"
                );
                gate["dependency"] = json!("enable_polish");
                gate["disabled_flag_after_dependency"] = json!(this.find(id)?.disabled());
                gate["enabled_interaction_verified_by_field"] = json!(key);
            }
        }
        Ok(gates)
    }

    fn configuration_field(this: &mut Harness, field: Field) -> Result<Value> {
        this.click(field.tab.id())?;
        this.settle()?;
        if field.advanced || field.key == "api_key" {
            this.click("general")?;
            this.ensure_toggle("ui.advanced_settings", true)?;
            this.click(field.tab.id())?;
        }
        match field.key {
            "native_fast_paste" | "restore_clipboard" => this.ensure_toggle("auto_paste", true)?,
            "audio.gain" | "audio.soft_limiter" => this.select_index("audio.gain_mode", 1)?,
            "audio.highpass_freq" => this.ensure_toggle("audio.highpass_filter", true)?,
            key if key.starts_with("llm.") => this.ensure_toggle("enable_polish", true)?,
            _ => {}
        }
        let id = this.control_id(field.key)?;
        let initial = this
            .window(|window, _| window.try_find(id.clone()))?
            .with_context(|| format!("{} has no rendered production control", field.key))?;
        let initially_in_view = this
            .find("settings-page")?
            .bounds()
            .contains(&initial.bounds().center());
        this.scroll_to(id.clone())?;
        let observed = this.find(id.clone())?;
        let viewport = this.find("settings-page")?.bounds();
        ensure!(
            observed.visible() && viewport.contains(&observed.bounds().center()),
            "{} is unreachable at 640x480",
            field.key
        );
        ensure!(
            observed.disabled() != Some(true),
            "{} stayed disabled after dependency controls",
            field.key
        );
        let expected_role = match field.kind {
            Kind::Toggle => gpui_kit::Role::Switch,
            Kind::Secret => gpui_kit::Role::PasswordInput,
            Kind::Text | Kind::List => gpui_kit::Role::TextInput,
            Kind::Slider { .. } => gpui_kit::Role::Slider,
            Kind::Choice(_) | Kind::Model | Kind::Device => gpui_kit::Role::ComboBox,
        };
        ensure!(
            observed.role() == Some(expected_role),
            "{} did not render its native {:?} control: {:?}",
            field.key,
            expected_role,
            observed.role()
        );
        let before = saved_value(this, field.key)?;
        let mark = this.intents.len();
        let response_mark = this.terminal.keys().copied().max().unwrap_or_default();
        let mut desired = Value::Null;
        let operation = match field.kind {
            Kind::Toggle => {
                desired = json!(!before.as_bool().context("toggle config is not boolean")?);
                this.click(id.clone())?;
                this.settle()?;
                "pointer-toggle"
            }
            Kind::Secret | Kind::Text | Kind::List => {
                let text = match field.key {
                    "api_key" => "synthetic-configuration-coverage-key",
                    "network.proxy_url" => "http://127.0.0.1:8137",
                    "asr.realtime_url" => {
                        "wss://configuration-fixture.maas.aliyuncs.com/api-ws/v1/realtime"
                    }
                    "dictionary_learning.excluded_bundle_ids" => {
                        "com.example.coverage, org.example.notes"
                    }
                    _ => bail!("no safe native typing fixture for {}", field.key),
                };
                desired = if matches!(field.kind, Kind::List) {
                    json!(["com.example.coverage", "org.example.notes"])
                } else {
                    json!(text)
                };
                this.edit(field.key, text)?;
                "pointer-focus-cmd-a-type-enter"
            }
            Kind::Choice(options) => {
                let target = options
                    .iter()
                    .position(|(value, _, _)| json!(value) != before)
                    .context("choice has no alternate value")?;
                desired = json!(options[target].0);
                this.select_index(field.key, target)?;
                "pointer-open-keyboard-arrow-enter"
            }
            Kind::Model => {
                let snapshot = this.call("snapshot", json!({}))?;
                let models = snapshot[if field.key == "asr.model" {
                    "asr_models"
                } else {
                    "llm_models"
                }]
                .as_array()
                .context("model catalog missing")?;
                let target = models
                    .iter()
                    .position(|model| {
                        model["id"].is_string()
                            && model["id"] != before
                            && (field.key != "asr.model" || model["pipeline"] != "native_asr")
                    })
                    .context("model catalog has no alternate enabled model")?;
                desired = models[target]["id"].clone();
                this.select_index(field.key, target)?;
                "pointer-open-keyboard-model-selection"
            }
            Kind::Device => {
                this.select_index(field.key, 0)?;
                "pointer-open-keyboard-confirm-system-default"
            }
            Kind::Slider { .. } => {
                this.drag_slider(id.clone(), 0.35)?;
                if saved_value(this, field.key)? == before {
                    this.drag_slider(id.clone(), 0.45)?;
                }
                "native-pointer-thumb-drag-release"
            }
        };
        let submitted = this.intents[mark..]
            .iter()
            .rev()
            .find(|(method, params)| {
                method != "preview_config" && writes_key(method, params, field.key)
            })
            .cloned()
            .with_context(|| format!("{} pointer/key operation emitted no save", field.key))?;
        if matches!(field.kind, Kind::Slider { .. }) {
            desired = submitted.1["value"].clone();
            ensure!(
                this.intents[mark..].iter().any(
                    |(method, params)| method == "preview_config" && params["key"] == field.key
                ),
                "{} drag emitted no preview",
                field.key
            );
        }
        ensure!(
            this.terminal
                .iter()
                .any(|(id, (method, params))| *id > response_mark
                    && method == "rpc_response"
                    && params["method"] == submitted.0
                    && params["params"] == submitted.1),
            "{} has no matching successful backend response",
            field.key
        );
        let persisted = saved_value(this, field.key)?;
        if let (Some(actual), Some(expected)) = (persisted.as_f64(), desired.as_f64()) {
            ensure!(
                (actual - expected).abs() < 0.00001,
                "{} did not persist requested numeric value",
                field.key
            );
        } else {
            ensure!(
                persisted == desired,
                "{} persisted a different value: requested {desired}, got {persisted}",
                field.key
            );
        }
        let snapshot = this.call("snapshot", json!({}))?;
        if field.key == "api_key" {
            ensure!(snapshot["api_key_set"] == true && snapshot["config"]["api_key"] == "");
        } else {
            ensure!(
                schema::get(&snapshot["config"], field.key) == &persisted,
                "{} snapshot and disk disagree",
                field.key
            );
        }
        ensure!(
            matches!(field.kind, Kind::Device) || persisted != before,
            "{} was not changed by its native control",
            field.key
        );
        this.shot(&format!("configuration-{}", field.key.replace('.', "-")))?;
        let bounds = observed.bounds();
        let record = json!({"key":field.key,"tab":field.tab.id(),"role":format!("{expected_role:?}"),"native_path":format!("{:?}",observed.path()),"viewport":[640,480],"initially_in_scroll_viewport":initially_in_view,"reachable_after_pointer_scroll":true,"bounds":{"x":f32::from(bounds.left()),"y":f32::from(bounds.top()),"width":f32::from(bounds.size.width),"height":f32::from(bounds.size.height)},"native_disabled":observed.disabled(),"operation":operation,"save_method":submitted.0,"backend_response":"rpc_response","disk_readback_matches":true,"snapshot_readback_matches":true,"changed":persisted!=before,"device_limitation":if matches!(field.kind,Kind::Device){Some("native=None fixture enumerates no physical devices; system-default confirmation persisted")}else{None},"sensitive_value_exported":false,"passed":true});
        if field.key == "ui.language" {
            this.select_index("ui.language", 1)?;
        } else if field.key == "network.proxy_url" {
            this.edit("network.proxy_url", "")?;
        } else if field.key == "asr.realtime_url" {
            this.scroll_to("public-endpoint")?;
            this.click("public-endpoint")?;
            this.settle()?;
            ensure!(saved_value(this, field.key)? == "");
        }
        Ok(record)
    }

    fn configuration_composites(this: &mut Harness) -> Result<Vec<Value>> {
        let mut records = vec![];
        this.click("polish")?;
        this.ensure_toggle("enable_polish", true)?;
        let mut prompt_categories = vec![];
        for &(category, _, _) in schema::PROMPT_CATEGORIES {
            this.scroll_to(category)?;
            ensure!(this.find(category)?.role() == Some(gpui_kit::Role::Button));
            this.click(category)?;
            this.ensure_toggle("custom-prompt-enabled", true)?;
            let view = this.view.clone();
            let prompt = this
                .cx
                .update(|cx| view.read(cx).testing_prompt_id(category))
                .with_context(|| {
                    format!("no real textarea identity for prompt category {category}")
                })?;
            this.scroll_to(prompt.clone())?;
            this.click(prompt.clone())?;
            ensure!(this.find(prompt.clone())?.focused() == Some(true));
            let text =
                format!("Synthetic coverage for {category}\nKeep the dictated content literal.");
            this.window(|window, cx| {
                window.press("cmd-a", cx);
                window.input(&text, cx);
                window.blur(cx);
            })?;
            this.settle()?;
            let overrides = saved_value(this, "llm.prompt_overrides")?;
            ensure!(
                overrides[category]["enabled"] == true && overrides[category]["prompt"] == text,
                "prompt {category} typing/blur did not persist"
            );
            this.scroll_to("reload-prompt-preset")?;
            this.click("reload-prompt-preset")?;
            this.settle()?;
            let preset = saved_value(this, "llm.prompt_overrides")?;
            ensure!(
                preset[category]["prompt"]
                    .as_str()
                    .is_some_and(|preset| !preset.is_empty() && preset != text),
                "prompt {category} did not load the actual system preset"
            );
            prompt_categories.push(json!({"category":category,"native_textarea":true,"pointer_toggle_and_type_blur_persisted":true,"reload_preset_persisted":true}));
        }
        records.push(json!({"key":"llm.prompt_overrides","entry":"5 real category buttons, custom switch, textarea and reload button","categories":prompt_categories,"passed":true}));

        this.click("shortcuts")?;
        this.scroll_to("fn-hotkey")?;
        this.ensure_toggle("fn-hotkey", false)?;
        ensure!(saved_value(this, "hotkey.active_hotkeys")? == json!([]));
        this.ensure_toggle("fn-hotkey", true)?;
        ensure!(saved_value(this, "hotkey.active_hotkeys")? == json!(["fn"]));
        records.push(json!({"key":"hotkey.active_hotkeys","entry":"native Fn switch off/on","disk_readback_matches":true,"passed":true}));
        this.scroll_to("capture-key")?;
        let mark = this.intents.len();
        this.click("capture-key")?;
        this.settle()?;
        ensure!(
            this.intents[mark..]
                .iter()
                .any(|(method, _)| method == "begin_hotkey_capture"),
            "capture button did not request platform capture"
        );
        // A hardware-free event enters the same platform-to-Settings route.
        // This verifies capture UI/persistence, not the global Quartz tap.
        this.sink.emit("hotkey_capture", json!({"key_code":105,"display_name":"F13","is_modifier":false,"flag_mask":0,"repeat":false}));
        this.pump()?;
        this.settle()?;
        let keys = saved_value(this, "hotkey.custom_keys")?;
        let legacy = saved_value(this, "hotkey.custom_key")?;
        ensure!(keys[0]["key_code"] == 105 && legacy == keys[0]);
        this.scroll_to(("remove-key", 105usize))?;
        this.click(("remove-key", 105usize))?;
        this.settle()?;
        ensure!(saved_value(this, "hotkey.custom_keys")? == json!([]));
        ensure!(saved_value(this, "hotkey.custom_key")?.is_null());
        for key in ["hotkey.custom_keys", "hotkey.custom_key"] {
            records.push(json!({"key":key,"entry":"native Add trigger key, synthetic F13 platform event, native Remove","capture_and_delete_disk_readback_matches":true,"global_key_capture_verified":false,"passed":true}));
        }

        let snapshot = this.call("snapshot", json!({}))?;
        let selected = snapshot["asr_models"]
            .as_array()
            .context("missing actual ASR catalog")?
            .iter()
            .find(|model| model["id"] == snapshot["config"]["asr"]["model"])
            .context("selected model missing from catalog")?;
        ensure!(saved_value(this, "asr.backend")? == selected["transport"]);
        records.push(json!({"key":"asr.backend","entry":"native ASR model selection persists its catalog transport","transport_verified":selected["transport"],"all_transports_verified":false,"passed":true}));

        this.click("general")?;
        this.scroll_to("rerun-setup")?;
        this.click("rerun-setup")?;
        this.scroll_to("rerun-confirm")?;
        this.click("rerun-confirm")?;
        this.settle()?;
        ensure!(saved_value(this, "ui.onboarding_completed")? == false);
        ensure!(saved_value(this, "ui.onboarding_skipped")? == false);
        this.scroll_to("finish-onboarding")?;
        let mark = this.intents.len();
        this.click("finish-onboarding")?;
        this.settle()?;
        ensure!(
            saved_value(this, "ui.onboarding_completed")? == false,
            "Finish bypassed unavailable real permission prerequisites"
        );
        ensure!(
            !this.intents[mark..]
                .iter()
                .any(|(method, params)| method == "set_config"
                    && params["key"] == "ui.onboarding_completed")
        );
        this.scroll_to("skip-onboarding")?;
        this.click("skip-onboarding")?;
        this.settle()?;
        ensure!(saved_value(this, "ui.onboarding_completed")? == true);
        ensure!(saved_value(this, "ui.onboarding_skipped")? == true);
        for key in ["ui.onboarding_completed", "ui.onboarding_skipped"] {
            records.push(json!({"key":key,"entry":"native Run setup again / Confirm / disabled Finish / Skip","disk_readback_matches":true,"successful_permission_gated_finish_verified":false,"passed":true}));
        }
        this.shot("configuration-onboarding-native-entries")?;
        Ok(records)
    }

    fn configuration_coverage(this: &mut Harness) -> Result<()> {
        this.resize(640., 480.)?;
        let mut fields = vec![];
        let mut composites = vec![];
        let mut gates = vec![];
        let outcome = (|| -> Result<()> {
            gates = configuration_gates(this)?;
            for field in schema::FIELDS {
                match configuration_field(this, *field) {
                    Ok(record) => {
                        println!(
                            "configuration coverage: {} native interaction + backend + disk passed",
                            field.key
                        );
                        fields.push(record);
                    }
                    Err(error) => {
                        eprintln!("configuration coverage failed for {}: {error:#}", field.key);
                        fields.push(json!({"key":field.key,"tab":field.tab.id(),"passed":false,"error":format!("{error:#}")}));
                        this.window(|window, cx| window.press("escape", cx))?;
                        this.pump()?;
                        if this.window(|window, _| {
                            window.try_find("dismiss-settings-error").is_some()
                        })? {
                            this.click("dismiss-settings-error")?;
                        }
                    }
                }
            }
            composites = configuration_composites(this)?;
            Ok(())
        })();
        let missing_fields = schema::FIELDS
            .iter()
            .filter(|field| {
                !fields
                    .iter()
                    .any(|record| record["key"] == field.key && record["passed"] == true)
            })
            .map(|field| field.key)
            .collect::<Vec<_>>();
        for gate in &mut gates {
            if let Some(key) = gate["enabled_interaction_verified_by_field"].as_str() {
                let verified = fields
                    .iter()
                    .any(|field| field["key"] == key && field["passed"] == true);
                gate["enabled_interaction_verified"] = json!(verified);
            }
        }
        let missing_composites = schema::COMPOSITE_KEYS
            .iter()
            .copied()
            .filter(|key| {
                !composites
                    .iter()
                    .any(|record| record["key"] == *key && record["passed"] == true)
            })
            .collect::<Vec<_>>();
        this.configuration_coverage = json!({
            "case":"configuration-coverage","viewport":[640,480],
            "renderer":"real Metal","controls":"production Settings",
            "backend":"real BackendDriver + Application","persistence":"ConfigRepository config.yaml readback after matching successful rpc_response",
            "harness_set_used":false,"native_microphone":false,"provider_requests":false,"system_permission_pages_opened":false,
            "field_count":schema::FIELDS.len(),"fields":fields,"missing_fields":missing_fields,
            "composite_count":schema::COMPOSITE_KEYS.len(),"composites":composites,"missing_composites":missing_composites,
            "conditional_controls":gates,
            "not_verified":["physical audio device switching (native=None fixture has only system default)","Apple AGC / actual audio-busy runtime gates","LLM catalog without thinking support (current authoritative catalog has only thinking-capable models)","global Quartz hotkey capture / left-right modifier hardware","permission-gated onboarding Finish with real TCC grants","ASR transports beyond the actual selectable realtime catalog","native disabled accessibility flags / VoiceOver (GPUI Kit omits the observed flags; inert pointer behavior is verified instead)"],
            "failure":outcome.as_ref().err().map(|error|format!("{error:#}")),
            "passed":outcome.is_ok()&&missing_fields.is_empty()&&missing_composites.is_empty()
        });
        fs::write(
            this.output.join("configuration-coverage.json"),
            serde_json::to_vec_pretty(&this.configuration_coverage)?,
        )?;
        outcome?;
        ensure!(
            missing_fields.is_empty(),
            "independent configuration fields not verified: {missing_fields:?}"
        );
        ensure!(
            missing_composites.is_empty(),
            "composite configuration entries not verified: {missing_composites:?}"
        );
        this.click("general")?;
        this.scroll_to("clear-api-key")?;
        this.click("clear-api-key")?;
        this.settle()?;
        this.resize(900., 740.)?;
        Ok(())
    }

    fn key_and_rejection(this: &mut Harness) -> Result<()> {
        this.click("general")?;
        this.scroll_to("api_key")?;
        this.edit("api_key", "metal-fixture-not-a-credential")?;
        let snapshot = this.call("snapshot", json!({}))?;
        assert_eq!(snapshot["api_key_set"], true);
        assert_eq!(snapshot["config"]["api_key"], "");
        this.click("show-api-key")?;
        this.settle()?;
        let view = this.view.clone();
        assert_eq!(
            this.cx
                .update(|cx| view.read(cx).testing_input_value("api_key", cx))
                .as_deref(),
            Some("metal-fixture-not-a-credential")
        );
        assert_eq!(
            this.cx
                .update(|cx| view.read(cx).testing_input_masked("api_key", cx)),
            Some(false)
        );
        assert_eq!(
            this.find("api_key")?.value(),
            None,
            "Password input must remain private to accessibility clients"
        );
        this.shot("key-revealed-synthetic-only")?;
        let revealed_pixels = this.control_pixels("api_key")?;
        this.click("show-api-key")?;
        this.settle()?;
        assert_eq!(
            this.cx
                .update(|cx| view.read(cx).testing_input_value("api_key", cx))
                .as_deref(),
            Some("")
        );
        assert_eq!(
            this.cx
                .update(|cx| view.read(cx).testing_input_masked("api_key", cx)),
            Some(true)
        );
        let hidden_pixels = this.control_pixels("api_key")?;
        assert_eq!(revealed_pixels.len(), hidden_pixels.len());
        ensure!(
            revealed_pixels
                .iter()
                .zip(&hidden_pixels)
                .filter(|(before, after)| before != after)
                .count()
                > 200,
            "show/hide did not change actual Metal pixels"
        );
        this.shot("key-hidden-with-saved-status")?;
        this.click("clear-api-key")?;
        this.settle()?;
        assert_eq!(this.call("snapshot", json!({}))?["api_key_set"], false);

        // A real persistence failure restores the optimistic UI from backend
        // readback; no forged rpc_error and no provider or actual credential.
        this.scroll_to("auto_paste")?;
        let before = this.find("auto_paste")?.checked();
        let config_path = this.data.path().join("config.yaml");
        let backup = fs::read(&config_path)?;
        fs::remove_file(&config_path)?;
        fs::create_dir(&config_path)?;
        this.scroll_to("api_key")?;
        this.edit("api_key", "rejected-synthetic-key")?;
        let public = this.call("snapshot", json!({}))?;
        assert_eq!(public["api_key_set"], false);
        assert_eq!(public["config"]["api_key"], "");
        assert_eq!(
            this.cx
                .update(|cx| view.read(cx).testing_input_value("api_key", cx))
                .as_deref(),
            Some("")
        );
        assert_eq!(
            this.cx
                .update(|cx| view.read(cx).testing_input_masked("api_key", cx)),
            Some(true)
        );
        this.scroll_to("auto_paste")?;
        this.click("auto_paste")?;
        this.settle()?;
        ensure!(
            this.terminal
                .values()
                .any(|(method, params)| method == "rpc_error" && params["method"] == "set_config"),
            "GUI save did not hit backend rejection"
        );
        assert_eq!(this.find("auto_paste")?.checked(), before);
        assert!(this.find("dismiss-settings-error")?.visible());
        this.shot("save-rejected-and-readback-restored")?;
        fs::remove_dir(&config_path)?;
        fs::write(&config_path, backup)?;
        this.click("dismiss-settings-error")?;
        Ok(())
    }

    fn controls_and_history(this: &mut Harness) -> Result<()> {
        this.click("general")?;
        this.top()?;
        let original = this.call("snapshot", json!({}))?["config"]["default_mode"].clone();
        this.scroll_to("default_mode")?;
        this.window(|window, cx| {
            window.within("default_mode").click("input", cx);
            assert_eq!(window.find("default_mode").expanded(), Some(true));
            window.press("down", cx);
            window.press("enter", cx);
        })?;
        this.settle()?;
        let changed = this.call("snapshot", json!({}))?["config"]["default_mode"].clone();
        assert_ne!(
            original, changed,
            "dropdown interaction did not change backend mode"
        );
        this.scroll_to("network.proxy_url")?;
        this.edit("network.proxy_url", "http://127.0.0.1:7890")?;
        assert_eq!(
            this.call("snapshot", json!({}))?["config"]["network"]["proxy_url"],
            "http://127.0.0.1:7890"
        );
        this.edit("network.proxy_url", "https://invalid:80")?;
        assert!(this.find("dismiss-settings-error")?.visible());
        assert_eq!(
            this.call("snapshot", json!({}))?["config"]["network"]["proxy_url"],
            "http://127.0.0.1:7890"
        );
        this.click("dismiss-settings-error")?;
        this.edit("network.proxy_url", "")?;
        assert_eq!(
            this.call("snapshot", json!({}))?["config"]["network"]["proxy_url"],
            ""
        );

        this.click("audio")?;
        this.top()?;
        this.set("audio.gain_mode", json!("manual"))?;
        let before = this.call("snapshot", json!({}))?["config"]["audio"]["gain"]
            .as_f64()
            .unwrap();
        let view = this.view.clone();
        let slider = this
            .cx
            .update(|cx| view.read(cx).testing_control_id("audio.gain"))
            .unwrap();
        this.scroll_to(slider.clone())?;
        let mark = this.intents.len();
        this.window(|window, cx| {
            let scope = window.within(slider.clone());
            let thumb = scope.find(("slider-thumb", 0u32)).bounds();
            let track = scope.find("slider-bar-container").bounds();
            // Dispatch a real pointer drag from the existing native thumb.
            window.drag(
                thumb.center(),
                point(track.left() + track.size.width * 0.8, track.center().y),
                cx,
            );
        })?;
        this.settle()?;
        let gain = this.call("snapshot", json!({}))?["config"]["audio"]["gain"]
            .as_f64()
            .unwrap();
        assert_ne!(before, gain, "slider release did not persist");
        assert!(
            this.intents[mark..]
                .iter()
                .any(|(method, _)| method == "preview_config")
        );
        assert!(
            this.intents[mark..]
                .iter()
                .any(|(method, params)| method == "set_config" && params["key"] == "audio.gain")
        );
        this.shot("slider-native-drag-readback")?;

        this.click("polish")?;
        this.top()?;
        this.set("enable_polish", json!(true))?;
        this.scroll_to("custom-prompt-enabled")?;
        if this.find("custom-prompt-enabled")?.checked() != Some(true) {
            this.click("custom-prompt-enabled")?;
            this.settle()?;
        }
        let prompt = this
            .cx
            .update(|cx| view.read(cx).testing_prompt_id("output_type"))
            .unwrap();
        this.scroll_to(prompt.clone())?;
        this.click(prompt.clone())?;
        this.window(|window, cx| {
            assert_eq!(window.find(prompt.clone()).focused(), Some(true));
            window.press("cmd-a", cx);
            window.input("Synthetic custom prompt\nKeep content literal.", cx);
            assert_eq!(
                window.find(prompt.clone()).value(),
                Some("Synthetic custom prompt\nKeep content literal."),
                "textarea rejected actual native typing"
            );
            window.blur(cx);
            window.render_frame(cx);
        })?;
        this.settle()?;
        assert_eq!(
            this.call("snapshot", json!({}))?["config"]["llm"]["prompt_overrides"]["output_type"]["prompt"],
            "Synthetic custom prompt\nKeep content literal."
        );
        this.shot("textarea-focus-edit-blur-save")?;

        this.click("dictionary")?;
        this.top()?;
        this.scroll_to("dictionary-term")?;
        this.edit("dictionary-term", "Metal term")?;
        this.edit("dictionary-aliases", "metal alias, native alias")?;
        this.click("add-term")?;
        this.settle()?;
        assert_eq!(
            this.call("get_dictionary", json!({}))?[0]["term"],
            "Metal term"
        );
        this.scroll_to(("remove-term", 0usize))?;
        this.click(("remove-term", 0usize))?;
        this.settle()?;
        assert!(
            this.call("get_dictionary", json!({}))?
                .as_array()
                .unwrap()
                .is_empty()
        );

        this.click("history")?;
        this.settle()?;
        this.top()?;
        assert_eq!(
            this.call("get_recordings", json!({}))?
                .as_array()
                .unwrap()
                .len(),
            1
        );
        this.scroll_to("history-filter")?;
        this.edit("history-filter", "no matching transcript")?;
        assert!(this.window(|window, _| window.try_find(("delete-recording", 0usize)).is_none())?);
        this.edit("history-filter", "")?;
        this.scroll_to(("delete-recording", 0usize))?;
        let mark = this.intents.len();
        this.click(("delete-recording", 0usize))?;
        this.settle()?;
        this.top()?;
        this.scroll_to("undo-deletion")?;
        this.click("undo-deletion")?;
        this.cx.advance_clock(Duration::from_millis(5100));
        this.pump()?;
        this.settle()?;
        assert_eq!(
            this.call("get_recordings", json!({}))?
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert!(
            !this.intents[mark..]
                .iter()
                .any(|(_, params)| params["action"] == "deleteRecording"),
            "undo still dispatched durable deletion"
        );
        this.scroll_to(("delete-recording", 0usize))?;
        this.click(("delete-recording", 0usize))?;
        this.pump()?;
        this.cx.advance_clock(Duration::from_millis(5100));
        this.pump()?;
        this.settle()?;
        assert!(
            this.call("get_recordings", json!({}))?
                .as_array()
                .unwrap()
                .is_empty()
        );
        this.shot("history-delete-after-undo-window")?;
        Ok(())
    }

    fn onboarding(this: &mut Harness) -> Result<()> {
        this.set("ui.onboarding_completed", json!(false))?;
        this.scroll_to("finish-onboarding")?;
        let mark = this.intents.len();
        this.click("finish-onboarding")?;
        this.settle()?;
        assert_eq!(
            this.call("snapshot", json!({}))?["config"]["ui"]["onboarding_completed"],
            false,
            "missing credential and permissions must block Finish"
        );
        assert!(
            !this.intents[mark..]
                .iter()
                .any(|(method, params)| method == "set_config"
                    && params["key"] == "ui.onboarding_completed"),
            "disabled Finish emitted a durable operation"
        );
        this.scroll_to("skip-onboarding")?;
        this.click("skip-onboarding")?;
        this.settle()?;
        let snapshot = this.call("snapshot", json!({}))?;
        assert_eq!(snapshot["config"]["ui"]["onboarding_completed"], true);
        assert_eq!(snapshot["config"]["ui"]["onboarding_skipped"], true);
        assert!(this.find("general")?.visible());
        this.shot("onboarding-skip-native-readback")?;
        this.set("ui.onboarding_skipped", json!(false))?;
        Ok(())
    }

    fn admission_rejection(this: &mut Harness) -> Result<()> {
        this.click("general")?;
        this.top()?;
        this.scroll_to("auto_paste")?;
        let before = this.find("auto_paste")?.checked();
        let view = this.view.clone();
        let baseline = this.cx.update(|cx| view.read(cx).testing_pending_count());
        let mark = this.intents.len();
        for _ in 0..UI_QUEUE_CAPACITY {
            this.sink.emit("acceptance_queue_filler", json!({}));
        }
        assert_eq!(this.events.len(), UI_QUEUE_CAPACITY);
        this.click("auto_paste")?;
        assert_eq!(this.find("auto_paste")?.checked(), before);
        assert_eq!(
            this.cx.update(|cx| view.read(cx).testing_pending_count()),
            baseline
        );
        assert!(this.find("dismiss-settings-error")?.visible());
        assert!(
            !this.intents[mark..]
                .iter()
                .any(|(method, _)| method == "set_config")
        );
        this.shot("full-ui-queue-rejects-toggle-and-restores")?;
        this.click("dismiss-settings-error")?;

        this.scroll_to("api_key")?;
        this.click("api_key")?;
        this.window(|window, cx| {
            window.press("cmd-a", cx);
            window.input("rejected-admission-fixture", cx);
        })?;
        for _ in 0..UI_QUEUE_CAPACITY {
            this.sink.emit("acceptance_queue_filler", json!({}));
        }
        this.window(|window, cx| window.press("enter", cx))?;
        this.pump()?;
        assert_eq!(
            this.cx.update(|cx| view.read(cx).testing_pending_count()),
            baseline
        );
        assert_eq!(
            this.cx
                .update(|cx| view.read(cx).testing_input_value("api_key", cx))
                .as_deref(),
            Some("")
        );
        assert_eq!(this.call("snapshot", json!({}))?["api_key_set"], false);
        this.click("dismiss-settings-error")?;

        // Closing while an editor still owns focus must report an unadmitted
        // draft. The failure message names the field without exposing its value.
        let previous_proxy =
            this.call("snapshot", json!({}))?["config"]["network"]["proxy_url"].clone();
        this.top()?;
        this.scroll_to("network.proxy_url")?;
        this.click("network.proxy_url")?;
        this.window(|window, cx| {
            window.press("cmd-a", cx);
            window.input("http://127.0.0.1:8123", cx);
        })?;
        for _ in 0..UI_QUEUE_CAPACITY {
            this.sink.emit("acceptance_queue_filler", json!({}));
        }
        let flushed = this
            .cx
            .update(|cx| view.update(cx, |settings, cx| settings.flush(cx)));
        let message = flushed
            .expect_err("full queue falsely reported focused draft as saved")
            .to_string();
        assert!(message.contains("Network proxy"));
        assert!(!message.contains("8123"));
        this.pump()?;
        assert_eq!(
            this.call("snapshot", json!({}))?["config"]["network"]["proxy_url"],
            previous_proxy
        );
        this.click("dismiss-settings-error")?;

        this.click("audio")?;
        this.top()?;
        this.scroll_to("start-mic-test")?;
        let mark = this.intents.len();
        for _ in 0..UI_QUEUE_CAPACITY {
            this.sink.emit("acceptance_queue_filler", json!({}));
        }
        this.click("start-mic-test")?;
        assert_eq!(
            this.cx.update(|cx| view.read(cx).testing_pending_count()),
            baseline
        );
        assert_eq!(
            this.cx.update(|cx| view.read(cx).testing_mic_state()),
            "error"
        );
        assert!(
            !this.intents[mark..]
                .iter()
                .any(|(_, params)| params["action"] == "startMicTest")
        );
        assert_eq!(this.call("status", json!({}))?["state"], "idle");
        this.shot("full-ui-queue-rejects-preview-start")?;
        this.click("dismiss-settings-error")?;
        Ok(())
    }

    fn calibration(this: &mut Harness) -> Result<()> {
        this.click("audio")?;
        this.top()?;
        this.set("audio.gain_mode", json!("manual"))?;
        this.set("audio.gain", json!(4.0))?;
        this.scroll_to("calibrate-whisper")?;
        this.click("calibrate-whisper")?;
        this.shot("calibration-ready")?;
        let stop_count = this
            .terminal
            .values()
            .filter(|(_, params)| {
                params["method"] == "ui_action" && params["params"]["action"] == "stopMicTest"
            })
            .count();
        this.click("start-calibration")?;
        let start_count = this
            .intents
            .iter()
            .filter(|(_, params)| params["action"] == "startMicTest")
            .count();
        for (phase, sample, duration) in [("quiet", 32i16, 3000u64), ("whisper", 655i16, 4500u64)] {
            let count = if phase == "quiet" {
                start_count
            } else {
                start_count + 1
            };
            this.wait_preview(&format!("calibration {phase} start intent"), |this| {
                this.intents
                    .iter()
                    .filter(|(_, params)| params["action"] == "startMicTest")
                    .count()
                    >= count
            })?;
            // The RPC status barrier alone does not prove mic_test_started was
            // delivered or that the virtual phase timer was registered.
            this.wait_preview(&format!("calibration {phase} recording"), |this| {
                this.cx.update(|cx| this.view.read(cx).testing_mic_state()) == "recording"
            })?;
            let status = this.call_preview("status", json!({}))?;
            ensure!(
                status["state"] == "recording",
                "calibration {phase} preview not recording: {status}"
            );
            let generation = status["generation"]
                .as_u64()
                .context("preview generation missing")?;
            for _ in 0..12 {
                let status = this.call_preview("status", json!({}))?;
                ensure!(
                    status["state"] == "recording" && status["generation"] == generation,
                    "calibration {phase} generation {generation} ended before PCM (real preview deadline): {status}; events: {:?}",
                    this.preview_events
                );
                this.call_preview("append", json!({"generation":generation,"pcm_base64":STANDARD.encode(sample.to_le_bytes().repeat(640))}))
                    .with_context(|| format!("calibration {phase} append generation {generation}"))?;
                // Allow the actual preview worker to publish a new RMS sample.
                thread::sleep(Duration::from_millis(45));
                this.pump_backend()?;
            }
            // Capturing a Metal image can block beyond the backend's real
            // five-second safety timer on hosted runners. Capture the dialog
            // before sampling and the recommendation after both previews;
            // keep active PCM/phase checks independent of screenshot latency.
            let status = this.call_preview("status", json!({}))?;
            ensure!(
                status["state"] == "recording" && status["generation"] == generation,
                "calibration {phase} generation {generation} ended before virtual phase deadline: {status}; events: {:?}",
                this.preview_events
            );
            this.cx.advance_clock(Duration::from_millis(duration));
            this.pump_backend()?;
            this.call_preview("status", json!({}))?;
        }
        this.wait_preview("calibration two UI phase stops", |this| {
            this.terminal
                .values()
                .filter(|(_, params)| {
                    params["method"] == "ui_action" && params["params"]["action"] == "stopMicTest"
                })
                .count()
                >= stop_count + 2
        })?;
        this.pump()?;
        ensure!(
            this.find("apply-calibration")?.visible(),
            "valid hardware-free preview did not produce a recommendation"
        );
        this.shot("calibration-recommendation")?;
        this.click("apply-calibration")?;
        this.settle()?;
        let config = this.call("snapshot", json!({}))?["config"].clone();
        assert_eq!(config["audio"]["gain_mode"], "manual");
        ensure!(
            (config["audio"]["gain"].as_f64().unwrap() - 39.93).abs() < 0.2,
            "calibration gain did not preserve post-gain semantics: {}",
            config["audio"]["gain"]
        );
        assert_eq!(config["audio"]["highpass_filter"], true);
        assert_eq!(config["audio"]["soft_limiter"], true);
        assert_eq!(config["audio"]["waveform_ceiling_dbfs"], -12.0);
        assert!(this.window(|window, _| window.try_find("apply-calibration").is_none())?);
        this.scroll_to("calibrate-whisper")?;
        this.click("calibrate-whisper")?;
        this.click("start-calibration")?;
        this.settle()?;
        this.click("close-calibration")?;
        this.settle()?;
        this.cx.advance_clock(Duration::from_secs(8));
        this.pump()?;
        this.settle()?;
        assert_eq!(this.call("status", json!({}))?["state"], "idle");
        this.shot("calibration-cancelled")?;
        Ok(())
    }

    pub fn run() -> Result<()> {
        let mut this = Harness::start()?;
        let case = std::env::var("VOCAL_MORE_UI_CASE").unwrap_or_else(|_| "all".into());
        ensure!(
            [
                "all",
                "matrix",
                "key",
                "controls",
                "calibration",
                "onboarding",
                "admission",
                "configuration-coverage"
            ]
            .contains(&case.as_str()),
            "unknown UI acceptance case"
        );
        if case == "all" || case == "matrix" {
            matrix(&mut this)?;
            println!("Metal matrix: 7 tabs + onboarding, light/dark, en/zh, minimum window passed");
        }
        if case == "all" || case == "key" {
            key_and_rejection(&mut this)?;
            println!(
                "Native key focus/edit/save/show/hide/clear + real backend rejection/readback passed"
            );
        }
        if case == "all" || case == "controls" {
            controls_and_history(&mut this)?;
            println!(
                "Native dropdown, slider drag, text/textarea focus, dictionary add/remove, history filter/delete/undo passed"
            );
        }
        if case == "all" || case == "calibration" {
            calibration(&mut this)?;
            println!(
                "Native calibration quiet/whisper/apply/cancel with actual PCM preview backend passed"
            );
        }
        if case == "all" || case == "onboarding" {
            onboarding(&mut this)?;
            println!("Native onboarding Finish admission/Skip persistence passed");
        }
        if case == "all" || case == "admission" {
            admission_rejection(&mut this)?;
            println!(
                "Native full-queue admission rejection restores controls and leaves no pending operation passed"
            );
        }
        if case == "all" || case == "configuration-coverage" {
            configuration_coverage(&mut this)?;
            println!(
                "Configuration coverage: every independent field and composite entry used native controls + real backend disk readback at 640x480"
            );
        }
        this.close()?;
        if !this.configuration_coverage.is_null() {
            this.configuration_coverage["driver_finished"] = json!(this.driver.finished());
            let failures = this.driver.shutdown_failures();
            this.configuration_coverage["shutdown_failures"] = json!({"failed_durable_requests":failures.failed_durable_requests,"cleanup_failed":failures.cleanup_failed});
            ensure!(
                failures.failed_durable_requests == 0 && !failures.cleanup_failed,
                "configuration acceptance shutdown reported a failure"
            );
            fs::write(
                this.output.join("configuration-coverage.json"),
                serde_json::to_vec_pretty(&this.configuration_coverage)?,
            )?;
        }
        let report = json!({"case":case,"renderer":"real GPUI Kit 0.7.0 Metal","ui":"production Settings + native GPUI controls","backend":"real BackendDriver + Application","native_microphone":false,"provider_requests":false,"real_credentials":false,"driver_finished":this.driver.finished(),"screenshots":this.shots,"accepted_ui_intents":this.intents.len(),"configuration_coverage":this.configuration_coverage});
        let report_data = serde_json::to_vec_pretty(&report)?;
        fs::write(
            this.output.join(format!("report-{case}.json")),
            &report_data,
        )?;
        if case == "all" {
            fs::write(this.output.join("report.json"), report_data)?;
        }
        println!(
            "Settings rendering acceptance passed: {} screenshots",
            this.shots.len()
        );
        Ok(())
    }
}
