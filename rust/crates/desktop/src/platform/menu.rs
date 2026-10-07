// SPDX-License-Identifier: GPL-3.0-only
use super::{class, ns};
use crate::bridge::CommandSink;
use objc2::{
    DefinedClass, MainThreadMarker, MainThreadOnly, define_class, msg_send, rc::Retained,
    runtime::AnyObject, sel,
};
use objc2_foundation::{NSObject, NSObjectProtocol, NSString};
use serde_json::{Value, json};

struct TargetIvars {
    commands: CommandSink,
}
define_class!(
    #[unsafe(super=NSObject)] #[name="VMRustMenuTarget"] #[thread_kind=MainThreadOnly] #[ivars=TargetIvars]
    struct Target;
    unsafe impl NSObjectProtocol for Target {}
    impl Target {
        #[unsafe(method(invoke:))]
        fn invoke(&self,sender:&AnyObject) {
            // Menu tracking can retain old items while live config replaces
            // the displayed menu. Keep each action on its own native item;
            // reusing global numeric tags could invoke a different new action.
            let encoded:Option<Retained<NSString>>=unsafe {msg_send![sender,representedObject]};
            if let Some(encoded)=encoded.and_then(|v|serde_json::from_str::<Value>(&v.to_string()).ok()) && let Some(method)=encoded["method"].as_str() {
                self.ivars().commands.request(method,encoded["params"].clone());
            }
        }
        #[unsafe(method(userNotificationCenter:shouldPresentNotification:))]
        fn should_present(&self,_center:&AnyObject,_notification:&AnyObject)->bool {true}
    }
);
impl Target {
    fn new(mtm: MainThreadMarker, commands: CommandSink) -> Retained<Self> {
        let this = Self::alloc(mtm).set_ivars(TargetIvars { commands });
        unsafe { msg_send![super(this), init] }
    }
}
pub struct Menu {
    status: Option<Retained<AnyObject>>,
    target: Retained<Target>,
    state_item: Option<Retained<AnyObject>>,
    copy_item: Option<Retained<AnyObject>>,
    button: Option<Retained<AnyObject>>,
    language: String,
    /// Name of the first active trigger key, shown in the idle status line;
    /// `None` when no key starts a recording (all off, or `--no-hotkeys`).
    trigger: Option<String>,
    hotkeys_enabled: bool,
    /// Whether there is a last result for "Copy Last Result" to copy.
    has_result: bool,
}
fn menu(title: &str) -> Retained<AnyObject> {
    unsafe {
        let allocated: objc2::rc::Allocated<AnyObject> = msg_send![class(c"NSMenu"), alloc];
        let result: Retained<AnyObject> = msg_send![allocated,initWithTitle:&*ns(title)];
        let _: () = msg_send![&*result,setAutoenablesItems:false];
        result
    }
}
impl Menu {
    pub fn new(mtm: MainThreadMarker, commands: CommandSink, hotkeys_enabled: bool) -> Self {
        let bar: Retained<AnyObject> = unsafe { msg_send![class(c"NSStatusBar"), systemStatusBar] };
        let status: Retained<AnyObject> = unsafe { msg_send![&*bar,statusItemWithLength:-1.0_f64] };
        let button: Option<Retained<AnyObject>> = unsafe { msg_send![&*status, button] };
        let target = Target::new(mtm, commands);
        if let Some(center) = objc2::runtime::AnyClass::get(c"NSUserNotificationCenter") {
            unsafe {
                let center: Option<Retained<AnyObject>> =
                    msg_send![center, defaultUserNotificationCenter];
                if let Some(center) = center {
                    let _: () = msg_send![&*center,setDelegate:&*target];
                }
            }
        }
        Self {
            status: Some(status),
            target,
            state_item: None,
            copy_item: None,
            button,
            language: "zh".into(),
            trigger: Some("Fn".into()),
            hotkeys_enabled,
            has_result: false,
        }
    }
    fn item(
        &self,
        title: &str,
        method: Option<&str>,
        params: Value,
        checked: bool,
    ) -> Retained<AnyObject> {
        unsafe {
            let allocated: objc2::rc::Allocated<AnyObject> = msg_send![class(c"NSMenuItem"), alloc];
            let action = method.map(|_| sel!(invoke:));
            let item: Retained<AnyObject> =
                msg_send![allocated,initWithTitle:&*ns(title),action:action,keyEquivalent:&*ns("")];
            if let Some(method) = method {
                let action = json!({"method":method,"params":params}).to_string();
                let _: () = msg_send![&*item,setRepresentedObject:&*ns(&action)];
                let _: () = msg_send![&*item,setTarget:&*self.target];
            }
            let _: () = msg_send![&*item,setEnabled:method.is_some()];
            let _: () = msg_send![&*item,setState:if checked {1isize}else{0}];
            item
        }
    }
    fn add(
        &self,
        menu: &AnyObject,
        title: &str,
        method: Option<&str>,
        params: Value,
        checked: bool,
    ) -> Retained<AnyObject> {
        let item = self.item(title, method, params, checked);
        unsafe {
            let _: () = msg_send![menu,addItem:&*item];
        }
        item
    }
    fn shortcut(item: &AnyObject, key: &str) {
        unsafe {
            let _: () = msg_send![item,setKeyEquivalent:&*ns(key)];
        }
    }
    /// Small gray group title (macOS 14+ `sectionHeaderWithTitle:`).
    fn header(menu: &AnyObject, title: &str) {
        unsafe {
            let item: Retained<AnyObject> =
                msg_send![class(c"NSMenuItem"),sectionHeaderWithTitle:&*ns(title)];
            let _: () = msg_send![menu,addItem:&*item];
        }
    }
    fn sub(&self, menu: &AnyObject, title: &str) -> Retained<AnyObject> {
        let item = self.add(menu, title, None, Value::Null, false);
        let submenu = super::menu::menu(title);
        unsafe {
            let _: () = msg_send![&*item,setEnabled:true];
            let _: () = msg_send![&*item,setSubmenu:&*submenu];
        }
        submenu
    }
    fn separator(menu: &AnyObject) {
        unsafe {
            let item: Retained<AnyObject> = msg_send![class(c"NSMenuItem"), separatorItem];
            let _: () = msg_send![menu,addItem:&*item];
        }
    }
    pub fn update(&mut self, snapshot: &Value) {
        self.language = snapshot
            .pointer("/config/ui/language")
            .and_then(Value::as_str)
            .unwrap_or("zh")
            .into();
        let config = &snapshot["config"];
        let fn_active = config["hotkey"]["active_hotkeys"]
            .as_array()
            .is_none_or(|keys| keys.iter().any(|key| key == "fn"));
        self.trigger = if !self.hotkeys_enabled {
            None
        } else if fn_active {
            Some("Fn".into())
        } else {
            config["hotkey"]["custom_keys"]
                .as_array()
                .and_then(|keys| keys.first())
                .and_then(|key| key["display_name"].as_str())
                .map(str::to_owned)
        };
        let menu = menu("Vocal More");
        // Status first: what is happening and how to start, in one line.
        self.state_item = Some(self.add(
            &menu,
            &self.status_title(snapshot["state"].as_str().unwrap_or("idle")),
            None,
            Value::Null,
            false,
        ));
        let copy = self.add(
            &menu,
            self.t("复制最近结果", "Copy Last Result"),
            Some("platform_copy_last"),
            json!({}),
            false,
        );
        unsafe {
            let _: () = msg_send![&*copy,setEnabled:self.has_result];
        }
        self.copy_item = Some(copy);

        Self::separator(&menu);
        Self::header(&menu, self.t("录音", "Recording"));
        let modes = self.sub(&menu, self.t("录音模式", "Recording Mode"));
        // Same wording as the settings window.
        for (id, zh, en) in [
            ("walkie_talkie", "按住说话", "Push to Talk"),
            ("realtime_long", "免提长录音", "Hands Free"),
        ] {
            self.add(
                &modes,
                self.t(zh, en),
                Some("set_mode"),
                json!({"mode":id}),
                config["default_mode"] == id,
            );
        }
        let devices = self.sub(&menu, self.t("麦克风", "Microphone"));
        self.add(
            &devices,
            self.t("系统默认", "System Default"),
            Some("set_device"),
            json!({"device":null}),
            config["audio"]["input_device"].is_null(),
        );
        if let Some(values) = snapshot["devices"].as_array() {
            if !values.is_empty() {
                Self::separator(&devices);
            }
            for device in values {
                if let Some(name) = device["name"].as_str() {
                    self.add(
                        &devices,
                        name,
                        Some("set_device"),
                        json!({"device":name}),
                        config["audio"]["input_device"] == name
                            || config["audio"]["input_device"] == device["uid"],
                    );
                }
            }
        }
        Self::separator(&devices);
        self.add(
            &devices,
            self.t("刷新设备列表", "Refresh Device List"),
            Some("refresh_devices"),
            json!({}),
            false,
        );
        if config["ui"]["advanced_settings"] == true {
            let models = self.sub(&menu, self.t("识别模型", "Recognition Model"));
            if let Some(values) = snapshot["asr_models"].as_array() {
                for model in values {
                    if let Some(id) = model["id"].as_str() {
                        self.add(
                            &models,
                            model["display_name"].as_str().unwrap_or(id),
                            Some("set_asr_model"),
                            json!({"model":id}),
                            config["asr"]["model"] == id,
                        );
                    }
                }
            }
        }

        Self::separator(&menu);
        Self::header(&menu, self.t("文字", "Text"));
        let polish = config["enable_polish"].as_bool().unwrap_or(false);
        self.add(
            &menu,
            self.t("润色", "Polish"),
            Some("set_config"),
            json!({"key":"enable_polish","value":!polish}),
            polish,
        );
        if polish {
            let levels = self.sub(&menu, self.t("润色程度", "Polish Level"));
            for (id, zh, en) in [
                ("minimal", "轻度", "Minimal"),
                ("balanced", "适中", "Balanced"),
                ("strong", "深度", "Strong"),
            ] {
                self.add(
                    &levels,
                    self.t(zh, en),
                    Some("set_config"),
                    json!({"key":"llm.level","value":id}),
                    config["llm"]["level"] == id,
                );
            }
        }
        let screen = config["screen_context_enabled"].as_bool().unwrap_or(false);
        self.add(
            &menu,
            self.t("屏幕上下文", "Screen Context"),
            Some("set_config"),
            json!({"key":"screen_context_enabled","value":!screen}),
            screen,
        );

        Self::separator(&menu);
        let item = self.add(
            &menu,
            self.t("设置…", "Settings…"),
            Some("platform_show_settings"),
            json!({}),
            false,
        );
        Self::shortcut(&item, ",");
        self.add(
            &menu,
            self.t("检查更新…", "Check for Updates…"),
            Some("platform_check_updates"),
            json!({}),
            false,
        );
        let help = self.sub(&menu, self.t("帮助", "Help"));
        for (zh, en, method, params) in [
            (
                "环境检查…",
                "Environment Check…",
                "platform_show_settings",
                json!({"tab":"general"}),
            ),
            (
                "导出诊断包…",
                "Export Diagnostics…",
                "platform_export_diagnostics",
                json!({}),
            ),
        ] {
            self.add(&help, self.t(zh, en), Some(method), params, false);
        }
        Self::separator(&help);
        self.add(
            &help,
            &format!(
                "{} {}",
                self.t("版本", "Version"),
                snapshot["version"]
                    .as_str()
                    .unwrap_or(vocal_more_backend::PRODUCT_VERSION)
            ),
            None,
            Value::Null,
            false,
        );

        Self::separator(&menu);
        let item = self.add(
            &menu,
            self.t("退出 Vocal More", "Quit Vocal More"),
            Some("platform_quit"),
            json!({}),
            false,
        );
        Self::shortcut(&item, "q");
        if let Some(status) = &self.status {
            unsafe {
                let _: () = msg_send![&**status,setMenu:&*menu];
            }
        }
        self.set_status(snapshot["state"].as_str().unwrap_or("idle"));
    }
    fn t<'a>(&self, zh: &'a str, en: &'a str) -> &'a str {
        if self.language == "en" { en } else { zh }
    }
    fn status_title(&self, state: &str) -> String {
        if state == "idle" {
            return match (&self.trigger, self.language == "en") {
                (Some(key), true) => format!("Ready · Hold {key} to talk"),
                (Some(key), false) => format!("就绪 · 按住 {key} 说话"),
                (None, true) => "Ready · Shortcuts are off".into(),
                (None, false) => "就绪 · 快捷键已关闭".into(),
            };
        }
        let (zh, en) = match state {
            "starting" => ("启动中…", "Starting…"),
            "recording" => ("录音中…", "Recording…"),
            "stopping" => ("停止中…", "Stopping…"),
            "processing" => ("处理中…", "Processing…"),
            "cancelling" => ("取消中…", "Cancelling…"),
            "failed" => ("出错了", "Something went wrong"),
            _ => ("未知", "Unknown"),
        };
        self.t(zh, en).to_owned()
    }
    pub fn set_has_result(&mut self, has_result: bool) {
        self.has_result = has_result;
        if let Some(item) = &self.copy_item {
            unsafe {
                let _: () = msg_send![&**item,setEnabled:has_result];
            }
        }
    }
    pub fn set_status(&mut self, state: &str) {
        let title = self.status_title(state);
        if let Some(item) = &self.state_item {
            unsafe {
                let _: () = msg_send![&**item,setTitle:&*ns(&title)];
            }
        }
        if let Some(button) = &self.button {
            unsafe {
                let _: () = msg_send![&**button,setToolTip:&*ns(&title)];
                let name = if matches!(state, "starting" | "recording") {
                    "icon_recording"
                } else {
                    "icon_idle"
                };
                let main: Retained<AnyObject> = msg_send![class(c"NSBundle"), mainBundle];
                let root: Option<Retained<NSString>> = msg_send![&*main, resourcePath];
                let mut paths = Vec::new();
                if let Some(root) = root {
                    paths.push(
                        std::path::PathBuf::from(root.to_string())
                            .join("resources/icons")
                            .join(format!("{name}.png")),
                    );
                    paths.push(
                        std::path::PathBuf::from(root.to_string())
                            .join("icons")
                            .join(format!("{name}.png")),
                    );
                }
                if let Some(root) = std::env::var_os("VOCAL_MORE_RESOURCE_ROOT") {
                    paths.push(
                        std::path::PathBuf::from(root)
                            .join("resources/icons")
                            .join(format!("{name}.png")),
                    );
                }
                paths.push(
                    std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                        .join("../../../resources/icons")
                        .join(format!("{name}.png")),
                );
                let image:Option<Retained<AnyObject>>=paths.iter().find(|p|p.is_file()).and_then(|path|path.to_str()).and_then(|path| {let allocated:objc2::rc::Allocated<AnyObject>=msg_send![class(c"NSImage"),alloc];msg_send![allocated,initWithContentsOfFile:&*ns(path)]}).or_else(||msg_send![class(c"NSImage"),imageWithSystemSymbolName:&*ns(if matches!(state,"starting"|"recording"){"mic.fill"}else{"waveform"}),accessibilityDescription:&*ns("Vocal More")]);
                if let Some(image) = image {
                    let _: () = msg_send![&*image,setTemplate:true];
                    let _: () = msg_send![&**button,setImage:&*image];
                }
            }
        }
    }
    pub fn notify(&self, message: &str) {
        let Some(class) = objc2::runtime::AnyClass::get(c"NSUserNotification") else {
            return;
        };
        unsafe {
            let allocated: objc2::rc::Allocated<AnyObject> = msg_send![class, alloc];
            let notification: Retained<AnyObject> = msg_send![allocated, init];
            let _: () = msg_send![&*notification,setTitle:&*ns("Vocal More")];
            let _: () = msg_send![&*notification,setInformativeText:&*ns(message)];
            let center: Option<Retained<AnyObject>> = msg_send![
                super::class(c"NSUserNotificationCenter"),
                defaultUserNotificationCenter
            ];
            if let Some(center) = center {
                let _: () = msg_send![&*center,deliverNotification:&*notification];
            }
        }
    }
    pub fn close(&mut self) {
        if let Some(status) = self.status.take() {
            unsafe {
                let bar: Retained<AnyObject> = msg_send![class(c"NSStatusBar"), systemStatusBar];
                let _: () = msg_send![&*bar,removeStatusItem:&*status];
            }
        }
        self.button = None;
        self.state_item = None;
        self.copy_item = None;
        if let Some(class) = objc2::runtime::AnyClass::get(c"NSUserNotificationCenter") {
            unsafe {
                let center: Option<Retained<AnyObject>> =
                    msg_send![class, defaultUserNotificationCenter];
                if let Some(center) = center {
                    let _: () = msg_send![&*center,setDelegate:std::ptr::null::<AnyObject>()];
                }
            }
        }
    }
}
impl Drop for Menu {
    fn drop(&mut self) {
        self.close();
    }
}
