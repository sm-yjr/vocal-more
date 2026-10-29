// SPDX-License-Identifier: GPL-3.0-only
//! GNOME owns the global grab. One bounded worker owns GSettings edits; the UI
//! captures local key events and never stores macOS virtual key codes on Linux.
use anyhow::{Context, Result, bail, ensure};
use gio::{Settings, SettingsSchemaSource, prelude::*};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    hash::{DefaultHasher, Hash, Hasher},
    io::Write,
    path::{Path, PathBuf},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
        mpsc::{Receiver, RecvTimeoutError, SyncSender, sync_channel},
    },
};

const PARENT: &str = "org.gnome.settings-daemon.plugins.media-keys";
const CHILD: &str = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding";
const BASE: &str = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/";
const LIMIT: usize = 8;

/// Canonical, deliberately small GTK accelerator vocabulary. Bare modifiers and
/// typing keys are not portable global triggers; function keys work unmodified.
pub(crate) fn canonical(raw: &str) -> Result<String> {
    ensure!(raw.len() <= 128, "快捷键无效");
    let mut rest = raw.trim();
    let (mut control, mut alt, mut shift, mut super_key) = (false, false, false, false);
    while rest.starts_with('<') {
        let end = rest.find('>').context("快捷键无效")?;
        match rest[1..end].to_ascii_lowercase().as_str() {
            "control" | "ctrl" | "primary" => control = true,
            "alt" | "mod1" => alt = true,
            "shift" => shift = true,
            "super" | "meta" | "mod4" => super_key = true,
            _ => bail!("不支持这个修饰键"),
        }
        rest = &rest[end + 1..];
    }
    let key = rest.to_ascii_lowercase();
    let function = key
        .strip_prefix('f')
        .and_then(|s| s.parse::<u8>().ok())
        .filter(|n| (1..=35).contains(n));
    ensure!(
        function.is_some() || control || alt || super_key,
        "请选择 F1–F35，或搭配 Ctrl、Alt、Super 的组合键"
    );
    let key = if let Some(number) = function {
        format!("F{number}")
    } else {
        match key.as_str() {
            "return" | "enter" => "Return".into(),
            "space" => "space".into(),
            "tab" => "Tab".into(),
            "backspace" => "BackSpace".into(),
            "delete" => "Delete".into(),
            "home" => "Home".into(),
            "end" => "End".into(),
            "pageup" | "page_up" | "prior" => "Page_Up".into(),
            "pagedown" | "page_down" | "next" => "Page_Down".into(),
            "left" => "Left".into(),
            "right" => "Right".into(),
            "up" => "Up".into(),
            "down" => "Down".into(),
            value if value.len() == 1 && value.bytes().all(|b| b.is_ascii_alphanumeric()) => {
                value.into()
            }
            _ => bail!("这个按键暂不支持，请选择功能键或字母、数字组合键"),
        }
    };
    Ok(format!(
        "{}{}{}{}{key}",
        if control { "<Control>" } else { "" },
        if alt { "<Alt>" } else { "" },
        if shift { "<Shift>" } else { "" },
        if super_key { "<Super>" } else { "" }
    ))
}
pub(crate) fn label(raw: &str) -> String {
    let mut display = raw
        .replace("<Control>", "Ctrl + ")
        .replace("<Alt>", "Alt + ")
        .replace("<Shift>", "Shift + ")
        .replace("<Super>", "Super + ")
        .replace("Page_Up", "Page Up")
        .replace("Page_Down", "Page Down");
    if let Some(key) = raw
        .rsplit('>')
        .next()
        .filter(|key| key.len() == 1 && key.is_ascii())
    {
        display.pop();
        display.push_str(&key.to_ascii_uppercase());
    }
    display
}

pub(crate) fn valid_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 48
        && id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}

pub struct Reply {
    pub request: u64,
    pub durable: bool,
    pub result: Result<Value>,
}
struct Job {
    request: u64,
    method: String,
    params: Value,
}
pub struct Worker {
    sender: Option<SyncSender<Job>>,
    replies: Receiver<Reply>,
    finished: Arc<AtomicBool>,
}
impl Worker {
    pub fn new(data: PathBuf) -> Result<Self> {
        let (sender, jobs) = sync_channel::<Job>(8);
        let (output, replies) = sync_channel(16);
        let finished = Arc::new(AtomicBool::new(false));
        let done = finished.clone();
        std::thread::Builder::new()
            .name("vocal-more-shortcuts".into())
            .spawn(move || {
                let context = gio::glib::MainContext::new();
                context
                    .with_thread_default(|| {
                        let service = Service::new(data);
                        loop {
                            for _ in 0..64 {
                                if !context.iteration(false) {
                                    break;
                                }
                            }
                            let job = match jobs.recv_timeout(std::time::Duration::from_millis(100))
                            {
                                Ok(job) => job,
                                Err(RecvTimeoutError::Timeout) => continue,
                                Err(RecvTimeoutError::Disconnected) => break,
                            };
                            let result = match &service {
                                Ok(service) => service.run(&job.method, &job.params),
                                Err(error) => Err(anyhow::anyhow!("{error}")),
                            };
                            // Eight pending jobs plus one in flight fit in the reserved
                            // completion channel. Never silently drop a durable save reply.
                            if output
                                .send(Reply {
                                    request: job.request,
                                    durable: matches!(
                                        job.method.as_str(),
                                        "linux_shortcuts_save"
                                            | "linux_shortcuts_remove"
                                            | "linux_shortcuts_enable_gestures"
                                            | "linux_shortcuts_disable_gestures"
                                    ),
                                    result,
                                })
                                .is_err()
                            {
                                break;
                            }
                        }
                    })
                    .expect("private GSettings context ownership");
                done.store(true, Ordering::Release);
            })?;
        Ok(Self {
            sender: Some(sender),
            replies,
            finished,
        })
    }
    pub fn request(&self, request: u64, method: &str, params: Value) -> Result<()> {
        self.sender
            .as_ref()
            .context("快捷键设置已关闭")?
            .try_send(Job {
                request,
                method: method.into(),
                params,
            })
            .map_err(|_| anyhow::anyhow!("快捷键正在保存，请稍后重试"))
    }
    pub fn poll(&self) -> Option<Reply> {
        self.replies.try_recv().ok()
    }
    pub fn close(&mut self) {
        self.sender.take();
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

#[derive(Default, Serialize, Deserialize)]
struct GestureConfig {
    enabled: bool,
    bindings: HashMap<String, String>,
}
struct Service {
    parent: Settings,
    source: SettingsSchemaSource,
    data: PathBuf,
    executable: PathBuf,
}
impl Service {
    fn new(data: PathBuf) -> Result<Self> {
        ensure!(
            std::env::var("XDG_CURRENT_DESKTOP")
                .unwrap_or_default()
                .to_ascii_lowercase()
                .contains("gnome"),
            "当前桌面暂不支持应用内全局快捷键配置"
        );
        let source = SettingsSchemaSource::default().context("系统未安装快捷键配置服务")?;
        let schema = source
            .lookup(PARENT, true)
            .context("系统未安装快捷键配置服务")?;
        ensure!(
            source.lookup(CHILD, true).is_some(),
            "系统未安装自定义快捷键配置服务"
        );
        let service = Self {
            parent: Settings::new_full(&schema, None::<&gio::SettingsBackend>, None),
            source,
            data: std::fs::canonicalize(data)?,
            executable: std::env::current_exe()?,
        };
        service.restore_legacy()?;
        // Adopt the previously configured F1 toggle, preserving its accelerator.
        for path in service.paths() {
            let settings = service.child(&path);
            if service.owned(&path, &settings)
                && settings.string("command").ends_with("--command toggle")
            {
                settings.set_string("command", &service.command(&service.id(&path)))?;
            }
        }
        Settings::sync();
        Ok(service)
    }
    fn gestures(&self) -> Result<GestureConfig> {
        let path = self.data.join("linux-gestures.json");
        if !path.exists() {
            return Ok(GestureConfig::default());
        }
        ensure!(std::fs::metadata(&path)?.len() <= 8192, "手势设置文件过大");
        let config: GestureConfig = serde_json::from_slice(&std::fs::read(path)?)?;
        ensure!(
            config.bindings.len() <= LIMIT
                && config
                    .bindings
                    .iter()
                    .all(|(id, key)| valid_id(id) && canonical(key).is_ok()),
            "手势配置无效"
        );
        Ok(config)
    }
    fn write_gestures(&self, config: &GestureConfig) -> Result<()> {
        let mut file = tempfile::NamedTempFile::new_in(&self.data)?;
        file.write_all(&serde_json::to_vec(config)?)?;
        file.as_file().sync_all()?;
        file.persist(self.data.join("linux-gestures.json"))?;
        std::fs::File::open(&self.data)?.sync_all()?;
        Ok(())
    }
    fn restore_legacy(&self) -> Result<()> {
        let config = self.gestures()?;
        for path in self.paths() {
            let child = self.child(&path);
            if self.owned(&path, &child)
                && child.string("binding").is_empty()
                && let Some(binding) = config.bindings.get(&self.id(&path))
            {
                child.set_string("binding", binding)?;
            }
        }
        Settings::sync();
        Ok(())
    }
    fn set_gestures(&self, enabled: bool) -> Result<()> {
        let mut config = self.gestures()?;
        if enabled {
            config.bindings = self
                .paths()
                .into_iter()
                .filter_map(|path| {
                    let child = self.child(&path);
                    if !self.owned(&path, &child) {
                        return None;
                    }
                    let id = self.id(&path);
                    let key = if child.string("binding").is_empty() {
                        config.bindings.get(&id)?.clone()
                    } else {
                        child.string("binding").into()
                    };
                    Some((id, key))
                })
                .collect();
            ensure!(!config.bindings.is_empty(), "请先添加至少一个快捷键");
            for path in self.paths() {
                let child = self.child(&path);
                if self.owned(&path, &child) && config.bindings.contains_key(&self.id(&path)) {
                    ensure!(child.is_writable("binding"), "系统策略禁止启用快捷键手势");
                }
            }
            config.enabled = true;
            self.write_gestures(&config)?;
            let release = (|| -> Result<()> {
                for path in self.paths() {
                    let child = self.child(&path);
                    if self.owned(&path, &child) && config.bindings.contains_key(&self.id(&path)) {
                        child.set_string("binding", "")?;
                    }
                }
                Settings::sync();
                Ok(())
            })();
            if let Err(error) = release {
                let _ = self.restore_legacy();
                config.enabled = false;
                let _ = self.write_gestures(&config);
                return Err(error);
            }
        } else {
            self.restore_legacy()?;
            config.enabled = false;
            self.write_gestures(&config)?;
        }
        Settings::sync();
        Ok(())
    }
    fn paths(&self) -> Vec<String> {
        self.parent
            .strv("custom-keybindings")
            .iter()
            .map(ToString::to_string)
            .collect()
    }
    fn child(&self, path: &str) -> Settings {
        Settings::with_path(CHILD, path)
    }
    fn id(&self, path: &str) -> String {
        path.trim_end_matches('/')
            .rsplit('/')
            .next()
            .unwrap_or("")
            .into()
    }
    fn owned(&self, path: &str, settings: &Settings) -> bool {
        if !path.starts_with(BASE)
            || !valid_id(&self.id(path))
            || !self.id(path).starts_with("vocal-more-")
        {
            return false;
        }
        let command = settings.string("command");
        let Ok(argv) = gio::glib::shell_parse_argv(command.as_str()) else {
            return false;
        };
        let args: Vec<_> = argv
            .iter()
            .map(|s| s.to_string_lossy().into_owned())
            .collect();
        let Some(binary) = args.first() else {
            return false;
        };
        if Path::new(binary) != self.executable && binary != "/usr/bin/vocal-more" {
            return false;
        }
        if args.len() == 3 && args[1] == "--command" && args[2] == "toggle" {
            return super::control::default_data()
                .ok()
                .and_then(|p| std::fs::canonicalize(p).ok())
                == Some(self.data.clone());
        }
        args.len() == 5
            && args[1] == "--data-dir"
            && Path::new(&args[2]) == self.data
            && args[3] == "--command"
            && args[4] == format!("shortcut:{}", self.id(path))
    }
    fn command(&self, id: &str) -> String {
        let executable = if self.executable.starts_with("/usr/lib/vocal-more/") {
            Path::new("/usr/bin/vocal-more")
        } else {
            &self.executable
        };
        format!(
            "{} --data-dir {} --command shortcut:{id}",
            gio::glib::shell_quote(executable).to_string_lossy(),
            gio::glib::shell_quote(&self.data).to_string_lossy()
        )
    }
    fn snapshot(&self) -> Value {
        let config = self.gestures().unwrap_or_default();
        let items: Vec<_> = self
            .paths()
            .iter()
            .filter_map(|path| {
                let child = self.child(path);
                if !self.owned(path, &child) {
                    return None;
                }
                let id = self.id(path);
                let accelerator = if child.string("binding").is_empty() {
                    config.bindings.get(&id).cloned().unwrap_or_default()
                } else {
                    child.string("binding").to_string()
                };
                Some(json!({"id":id,"accelerator":accelerator,"display_name":label(&accelerator)}))
            })
            .collect();
        json!({"available":true,"items":items,"gesture_enabled":config.enabled})
    }
    fn conflict(&self, accelerator: &str, except: Option<&str>) -> Option<String> {
        let same = |value: &str| canonical(value).is_ok_and(|value| value == accelerator);
        let config = self.gestures().unwrap_or_default();
        for (id, key) in &config.bindings {
            if except != Some(id.as_str()) && same(key) {
                return Some("Vocal More".into());
            }
        }
        for path in self.paths() {
            if except == Some(self.id(&path).as_str()) {
                continue;
            }
            let child = self.child(&path);
            if same(child.string("binding").as_str()) {
                return Some(child.string("name").to_string());
            }
        }
        let (schemas, _) = self.source.list_schemas(true);
        for id in schemas {
            if !id.starts_with("org.gnome.")
                || !(id.contains("keybindings")
                    || id.as_str() == PARENT
                    || id.starts_with("org.gnome.shell.extensions."))
            {
                continue;
            }
            let Some(schema) = self.source.lookup(&id, true) else {
                continue;
            };
            if schema.path().is_none() {
                continue;
            }
            let settings = Settings::new_full(&schema, None::<&gio::SettingsBackend>, None);
            for key in schema.list_keys() {
                let value = settings.value(&key);
                if value.get::<String>().is_some_and(|value| same(&value))
                    || value
                        .get::<Vec<String>>()
                        .is_some_and(|values| values.iter().any(|value| same(value)))
                {
                    return Some("系统快捷键".into());
                }
            }
        }
        None
    }
    fn run(&self, method: &str, params: &Value) -> Result<Value> {
        match method {
            "linux_shortcuts_refresh" => {
                if self.gestures()?.enabled {
                    self.set_gestures(true)?;
                }
            }
            "linux_shortcuts_enable_gestures" => self.set_gestures(true)?,
            "linux_shortcuts_disable_gestures" => self.set_gestures(false)?,
            "linux_shortcuts_save" => {
                let accelerator =
                    canonical(params["accelerator"].as_str().context("请选择快捷键")?)?;
                let editing = params["id"].as_str().filter(|id| !id.is_empty());
                let mut paths = self.paths();
                let id = if let Some(id) = editing {
                    ensure!(
                        paths
                            .iter()
                            .any(|p| self.id(p) == id && self.owned(p, &self.child(p))),
                        "快捷键已被移除，请刷新后重试"
                    );
                    id.into()
                } else {
                    ensure!(
                        self.snapshot()["items"].as_array().unwrap().len() < LIMIT,
                        "最多添加 8 个快捷键"
                    );
                    let mut hash = DefaultHasher::new();
                    self.data.hash(&mut hash);
                    (0..LIMIT)
                        .map(|n| format!("vocal-more-{:016x}-{n}", hash.finish()))
                        .find(|id| !paths.iter().any(|p| self.id(p) == *id))
                        .context("没有可用的快捷键位置")?
                };
                if let Some(owner) = self.conflict(&accelerator, editing) {
                    bail!(
                        "快捷键“{}”已被“{owner}”使用，请选择其他按键",
                        label(&accelerator)
                    );
                }
                let path = if editing.is_some() {
                    paths.iter().find(|p| self.id(p) == id).unwrap().clone()
                } else {
                    format!("{BASE}{id}/")
                };
                let child = self.child(&path);
                ensure!(
                    self.parent.is_writable("custom-keybindings")
                        && ["name", "command", "binding"]
                            .iter()
                            .all(|key| child.is_writable(key)),
                    "系统策略禁止修改快捷键"
                );
                child.delay();
                child.set_string("name", "Vocal More：开始／结束录音")?;
                child.set_string("command", &self.command(&id))?;
                child.set_string("binding", &accelerator)?;
                child.apply();
                if !paths.contains(&path) {
                    paths.push(path.clone());
                    self.parent
                        .set_strv("custom-keybindings", paths.as_slice())?;
                }
                Settings::sync();
                ensure!(
                    child.string("binding") == accelerator,
                    "系统未保存快捷键，请重试"
                );
            }
            "linux_shortcuts_remove" => {
                let id = params["id"].as_str().context("快捷键不存在")?;
                let paths = self.paths();
                let path = paths
                    .iter()
                    .find(|p| self.id(p) == id && self.owned(p, &self.child(p)))
                    .context("快捷键已被移除")?;
                let remaining: Vec<_> = paths
                    .iter()
                    .filter(|p| *p != path)
                    .map(String::as_str)
                    .collect();
                self.parent
                    .set_strv("custom-keybindings", remaining.as_slice())?;
                for key in ["name", "command", "binding"] {
                    self.child(path).reset(key);
                }
                let mut config = self.gestures()?;
                config.bindings.remove(id);
                if self.data.join("linux-gestures.json").exists() {
                    self.write_gestures(&config)?;
                }
                Settings::sync();
            }
            _ => bail!("不支持的快捷键操作"),
        }
        if matches!(method, "linux_shortcuts_save" | "linux_shortcuts_remove")
            && self.gestures()?.enabled
        {
            if self.snapshot()["items"]
                .as_array()
                .is_none_or(Vec::is_empty)
            {
                self.set_gestures(false)?;
            } else {
                self.set_gestures(true)?;
            }
        }
        Ok(self.snapshot())
    }
}
impl Drop for Service {
    fn drop(&mut self) {
        let _ = self.restore_legacy();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn accelerators_are_canonical_and_typing_keys_need_modifiers() {
        assert_eq!(canonical("<Alt><Primary>t").unwrap(), "<Control><Alt>t");
        assert_eq!(canonical("f1").unwrap(), "F1");
        assert_eq!(canonical("<Shift>F12").unwrap(), "<Shift>F12");
        for key in [
            "a",
            "<Shift>a",
            "Control_L",
            "<Super>",
            "F36",
            "<Alt>semicolon",
            "F1;sh",
        ] {
            assert!(canonical(key).is_err(), "{key}");
        }
        assert!(!valid_id("bad;command"));
        assert!(!valid_id("../other"));
    }
    /// Private GSettings backend only; never touch user shortcuts from cargo test.
    #[test]
    #[ignore = "requires the isolated settings runner"]
    fn linux_real_gsettings_shortcuts() -> Result<()> {
        ensure!(
            std::env::var("VOCAL_MORE_PRIVATE_SHORTCUT_TEST").as_deref() == Ok("1"),
            "use the private runner"
        );
        ensure!(
            std::env::var("GSETTINGS_BACKEND").as_deref() == Ok("keyfile"),
            "private backend required"
        );
        let data = tempfile::tempdir()?;
        let service = Service::new(data.path().into())?;
        let first = service.run("linux_shortcuts_save", &json!({"accelerator":"F1"}))?;
        let id = first["items"][0]["id"].as_str().unwrap();
        let path = format!("{BASE}{id}/");
        let command = service.child(&path).string("command");
        let argv = gio::glib::shell_parse_argv(command.as_str())?;
        assert!(
            argv.iter()
                .any(|arg| arg.to_string_lossy() == format!("shortcut:{id}"))
        );
        assert!(
            service
                .run("linux_shortcuts_save", &json!({"accelerator":"F1"}))
                .is_err()
        );
        let foreign = format!("{BASE}existing-editor/");
        let foreign_settings = service.child(&foreign);
        foreign_settings.set_string("name", "Existing editor")?;
        foreign_settings.set_string("command", "/usr/bin/true")?;
        foreign_settings.set_string("binding", "F3")?;
        service
            .parent
            .set_strv("custom-keybindings", [path.as_str(), foreign.as_str()])?;
        assert!(
            service
                .run("linux_shortcuts_save", &json!({"id":id,"accelerator":"F3"}))
                .is_err()
        );
        assert_eq!(service.child(&path).string("binding"), "F1");
        let wm = Settings::new("org.gnome.desktop.wm.keybindings");
        wm.set_strv("panel-main-menu", ["F4"])?;
        assert!(
            service
                .run("linux_shortcuts_save", &json!({"id":id,"accelerator":"F4"}))
                .is_err()
        );
        let updated = service.run(
            "linux_shortcuts_save",
            &json!({"id":id,"accelerator":"<Alt><Primary>r"}),
        )?;
        assert_eq!(updated["items"][0]["accelerator"], "<Control><Alt>r");
        assert_eq!(updated["items"].as_array().unwrap().len(), 1);
        let enabled = service.run("linux_shortcuts_enable_gestures", &json!({}))?;
        assert_eq!(enabled["gesture_enabled"], true);
        assert_eq!(service.child(&path).string("binding"), "");
        assert_eq!(enabled["items"][0]["accelerator"], "<Control><Alt>r");
        assert_eq!(foreign_settings.string("binding"), "F3");
        assert!(
            service
                .run(
                    "linux_shortcuts_save",
                    &json!({"accelerator":"<Control><Alt>r"})
                )
                .is_err()
        );
        // A fresh owner recovers a crashed adapter's saved F1-equivalent binding.
        service.restore_legacy()?;
        assert_eq!(service.child(&path).string("binding"), "<Control><Alt>r");
        service.set_gestures(true)?;
        service.run("linux_shortcuts_disable_gestures", &json!({}))?;
        assert_eq!(service.child(&path).string("binding"), "<Control><Alt>r");
        assert!(!service.gestures()?.enabled);
        assert_eq!(foreign_settings.string("binding"), "F3");
        assert!(
            service
                .run("linux_shortcuts_remove", &json!({"id":"existing-editor"}))
                .is_err()
        );
        service.run("linux_shortcuts_remove", &json!({"id":id}))?;
        assert_eq!(service.paths(), vec![foreign]);
        assert_eq!(foreign_settings.string("binding"), "F3");
        // Accepted saves drain on close, without an active UI or audio owner.
        let mut worker = Worker::new(data.path().into())?;
        worker.request(1, "linux_shortcuts_save", json!({"accelerator":"F8"}))?;
        worker.request(2, "linux_shortcuts_save", json!({"accelerator":"F9"}))?;
        worker.close();
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
        let mut replies = 0;
        while !worker.finished() || replies < 2 {
            if let Some(reply) = worker.poll() {
                reply.result?;
                replies += 1;
            }
            ensure!(
                std::time::Instant::now() < deadline,
                "shortcut worker did not drain"
            );
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        let reopened = Service::new(data.path().into())?;
        assert_eq!(reopened.snapshot()["items"].as_array().unwrap().len(), 2);
        Ok(())
    }
}
