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
    button: Option<Retained<AnyObject>>,
    language: String,
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
    pub fn new(mtm: MainThreadMarker, commands: CommandSink) -> Self {
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
            button,
            language: "zh".into(),
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
        let menu = menu("Vocal More");
        self.add(
            &menu,
            &format!(
                "Vocal More {}",
                snapshot["version"]
                    .as_str()
                    .unwrap_or(vocal_more_backend::PRODUCT_VERSION)
            ),
            None,
            Value::Null,
            false,
        );
        self.state_item = Some(self.add(
            &menu,
            &self.status_title(snapshot["state"].as_str().unwrap_or("idle")),
            None,
            Value::Null,
            false,
        ));
        let modes = self.sub(&menu, self.t("录音模式", "Recording Mode"));
        for (id, zh, en) in [
            ("walkie_talkie", "对讲模式（按住）", "Walkie-Talkie (Hold)"),
            (
                "realtime_long",
                "免提模式（按一下开始）",
                "Hands-Free (Tap)",
            ),
        ] {
            self.add(
                &modes,
                self.t(zh, en),
                Some("set_mode"),
                json!({"mode":id}),
                config["default_mode"] == id,
            );
        }
        let models = self.sub(&menu, self.t("识别模型", "ASR Model"));
        if let Some(values) = snapshot["asr_models"].as_array() {
            for model in values {
                if let Some(id) = model["id"].as_str() {
                    self.add(
                        &models,
                        model["name"].as_str().unwrap_or(id),
                        Some("set_asr_model"),
                        json!({"model":id}),
                        config["asr"]["model"] == id,
                    );
                }
            }
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
        self.add(
            &devices,
            self.t("重新检查", "Run Check Again"),
            Some("refresh_devices"),
            json!({}),
            false,
        );
        for (key, zh, en) in [
            ("enable_polish", "启用润色", "Enable Polishing"),
            ("screen_context_enabled", "看屏幕说话", "Talk About Screen"),
        ] {
            let on = config[key].as_bool().unwrap_or(false);
            self.add(
                &menu,
                self.t(zh, en),
                Some("set_config"),
                json!({"key":key,"value":!on}),
                on,
            );
        }
        let levels = self.sub(&menu, self.t("润色强度", "Polish Strength"));
        for (id, zh, en) in [
            ("minimal", "轻度", "Minimal"),
            ("balanced", "均衡", "Balanced"),
            ("strong", "强力", "Strong"),
        ] {
            self.add(
                &levels,
                self.t(zh, en),
                Some("set_config"),
                json!({"key":"llm.level","value":id}),
                config["llm"]["level"] == id,
            );
        }
        Self::separator(&menu);
        for (zh, en, method, params) in [
            (
                "复制最近结果",
                "Copy Last Result",
                "platform_copy_last",
                json!({}),
            ),
            ("设置…", "Settings…", "platform_show_settings", json!({})),
            (
                "环境检查",
                "Environment Check",
                "platform_show_settings",
                json!({"tab":"general"}),
            ),
            (
                "导出诊断包…",
                "Export Diagnostics…",
                "platform_export_diagnostics",
                json!({}),
            ),
            (
                "检查更新…",
                "Check for Updates…",
                "platform_check_updates",
                json!({}),
            ),
        ] {
            self.add(&menu, self.t(zh, en), Some(method), params, false);
        }
        Self::separator(&menu);
        self.add(
            &menu,
            self.t("退出 Vocal More", "Quit Vocal More"),
            Some("platform_quit"),
            json!({}),
            false,
        );
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
        let (zh, en) = match state {
            "idle" => ("空闲", "Idle"),
            "starting" => ("启动中…", "Starting…"),
            "recording" => ("录音中…", "Recording…"),
            "stopping" => ("停止中…", "Stopping…"),
            "processing" => ("处理中…", "Processing…"),
            "cancelling" => ("取消中…", "Cancelling…"),
            "failed" => ("出错", "Error"),
            _ => ("未知", "Unknown"),
        };
        format!("{}{}", self.t("状态：", "Status: "), self.t(zh, en))
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
