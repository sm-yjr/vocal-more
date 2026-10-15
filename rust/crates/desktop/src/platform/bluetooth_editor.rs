// SPDX-License-Identifier: GPL-3.0-only
//! Main-thread owner for the development app's dedicated test window.
use anyhow::{Context, Result, ensure};
use libloading::Library;
use serde_json::{Value, json};
use std::{
    cell::Cell,
    ffi::{CString, c_char, c_void},
    path::PathBuf,
    time::{Duration, Instant},
};
type Create = unsafe extern "C" fn(*const c_char) -> *mut c_void;
type Close = unsafe extern "C" fn(*mut c_void);
type Show = unsafe extern "C" fn(*mut c_void) -> i32;
type Status =
    unsafe extern "C" fn(*mut c_void, *mut u64, *mut u32, *mut u32, *mut u32, *mut u32) -> i32;
type Insert = unsafe extern "C" fn(*mut c_void, *const u8, u32, u64) -> i32;
#[derive(Clone, Copy)]
enum Constructor {
    Owned(Create),
    External(unsafe extern "C" fn() -> *mut c_void),
}
struct Api {
    create: Constructor,
    close: Close,
    show: Show,
    status: Status,
    insert: Insert,
    _library: Library,
}
impl Api {
    fn load(external: bool) -> Result<Self> {
        let path = std::env::current_exe()
            .ok()
            .and_then(|p| {
                p.parent()?
                    .parent()
                    .map(|p| p.join("Frameworks/libvocal_more_audio.dylib"))
            })
            .filter(|p| p.is_file())
            .unwrap_or_else(|| {
                PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                    .join("../../../.build/native/libvocal_more_audio.dylib")
            });
        unsafe {
            let library = Library::new(path)?;
            if external {
                let allowed =
                    library.get::<unsafe extern "C" fn() -> u32>(b"vm_bt_external_authorized\0")?;
                ensure!(
                    allowed() == 1,
                    "请在系统设置中为当前 Vocal More Bluetooth Dev 允许辅助功能；无需重新配对"
                );
                Ok(Self {
                    create: Constructor::External(*library.get(b"vm_bt_external_create\0")?),
                    close: *library.get(b"vm_bt_external_close\0")?,
                    show: *library.get(b"vm_bt_external_show\0")?,
                    status: *library.get(b"vm_bt_external_status\0")?,
                    insert: *library.get(b"vm_bt_external_insert\0")?,
                    _library: library,
                })
            } else {
                Ok(Self {
                    create: Constructor::Owned(*library.get(b"vm_bt_editor_create\0")?),
                    close: *library.get(b"vm_bt_editor_close\0")?,
                    show: *library.get(b"vm_bt_editor_show\0")?,
                    status: *library.get(b"vm_bt_editor_status\0")?,
                    insert: *library.get(b"vm_bt_editor_insert\0")?,
                    _library: library,
                })
            }
        }
    }
}
#[derive(Clone, Copy, Default)]
pub struct Target {
    pub revision: u64,
    pub ready: bool,
    pub f1: u32,
    pub bytes: u32,
    pub markers: u32,
}
impl Target {
    pub fn status(self) -> Value {
        json!({"revision":self.revision,"ready":self.ready,"f1_count":self.f1,"utf8_bytes":self.bytes,"fixed_marker_count":self.markers})
    }
}
#[derive(Default)]
pub struct Editor {
    api: Option<Api>,
    handle: *mut c_void,
    external: bool,
    last_probe: Cell<Option<Instant>>,
    cached_target: Cell<Target>,
}
impl Editor {
    pub fn is_external(&self) -> bool {
        self.external
    }
    pub fn scope(&self) -> &'static str {
        if self.external {
            "explicit separate input fixture; AX role/selection only; no text read, clipboard or key posting"
        } else {
            "app-owned test editor; no global event injection"
        }
    }
    pub fn select_external_fixture(&mut self) -> Result<()> {
        self.select_external_with(|| Api::load(true))
    }
    fn select_external_with(&mut self, load: impl FnOnce() -> Result<Api>) -> Result<()> {
        // A failed destination change invalidates the previous target rather
        // than silently continuing to insert into the app-owned editor.
        self.close();
        self.external = true;
        let api = load()?;
        let Constructor::External(create) = api.create else {
            unreachable!()
        };
        self.handle = unsafe { create() };
        ensure!(
            !self.handle.is_null(),
            "请打开单独的 Vocal More Bluetooth Input Fixture 空白窗口并保持登录；此模式不会选择 Typora 或其他应用"
        );
        self.api = Some(api);
        Ok(())
    }
    pub fn show(&mut self, title: &str) -> Result<()> {
        if self.external {
            self.close();
        }
        if self.handle.is_null() {
            let title = CString::new(title)?;
            let api = Api::load(false)?;
            let Constructor::Owned(create) = api.create else {
                unreachable!()
            };
            self.handle = unsafe { create(title.as_ptr()) };
            ensure!(
                !self.handle.is_null(),
                "请先解锁并登录本机，再打开专用测试编辑器"
            );
            self.api = Some(api);
        }
        let api = self.api.as_ref().context("测试编辑器未打开")?;
        ensure!(
            unsafe { (api.show)(self.handle) } == 1,
            "专用测试窗口已关闭或会话不可用"
        );
        Ok(())
    }
    pub fn target(&self) -> Target {
        // AX inspects only the explicit fixture's role and numeric caret. Poll
        // at most five times/second; native insertion performs a fresh guard.
        if self.external
            && self
                .last_probe
                .get()
                .is_some_and(|t| t.elapsed() < Duration::from_millis(200))
        {
            return self.cached_target.get();
        }
        let mut target = Target::default();
        let mut ready = 0;
        if let Some(api) = &self.api
            && !self.handle.is_null()
            && unsafe {
                (api.status)(
                    self.handle,
                    &mut target.revision,
                    &mut ready,
                    &mut target.f1,
                    &mut target.bytes,
                    &mut target.markers,
                )
            } == 1
        {
            target.ready = ready == 1;
        }
        self.last_probe.set(Some(Instant::now()));
        self.cached_target.set(target);
        target
    }
    pub fn insert(&mut self, text: &str, revision: u64) -> bool {
        let done = self.api.as_ref().is_some_and(|api| {
            !self.handle.is_null()
                && unsafe { (api.insert)(self.handle, text.as_ptr(), text.len() as u32, revision) }
                    == 1
        });
        self.last_probe.set(None);
        done
    }
    pub fn close(&mut self) {
        if let Some(api) = &self.api
            && !self.handle.is_null()
        {
            unsafe { (api.close)(self.handle) };
        }
        self.handle = std::ptr::null_mut();
        self.api = None;
        self.external = false;
        self.last_probe.set(None);
        self.cached_target.set(Target::default());
    }
}
impl Drop for Editor {
    fn drop(&mut self) {
        self.close();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU32, Ordering};
    static CLOSES: AtomicU32 = AtomicU32::new(0);
    static INSERTS: AtomicU32 = AtomicU32::new(0);
    unsafe extern "C" fn create(_: *const c_char) -> *mut c_void {
        std::ptr::dangling_mut::<c_void>()
    }
    unsafe extern "C" fn close(_: *mut c_void) {
        CLOSES.fetch_add(1, Ordering::Relaxed);
    }
    unsafe extern "C" fn show(_: *mut c_void) -> i32 {
        1
    }
    unsafe extern "C" fn status(
        _: *mut c_void,
        epoch: *mut u64,
        ready: *mut u32,
        _: *mut u32,
        _: *mut u32,
        _: *mut u32,
    ) -> i32 {
        unsafe {
            *epoch = 9;
            *ready = 1;
        }
        1
    }
    unsafe extern "C" fn insert(_: *mut c_void, _: *const u8, _: u32, _: u64) -> i32 {
        INSERTS.fetch_add(1, Ordering::Relaxed);
        1
    }
    #[test]
    fn denied_external_selection_invalidates_the_old_destination_without_fallback() {
        CLOSES.store(0, Ordering::Relaxed);
        INSERTS.store(0, Ordering::Relaxed);
        let mut editor = Editor {
            handle: std::ptr::dangling_mut::<c_void>(),
            api: Some(Api {
                create: Constructor::Owned(create),
                close,
                show,
                status,
                insert,
                _library: libloading::os::unix::Library::this().into(),
            }),
            external: false,
            last_probe: Cell::new(None),
            cached_target: Cell::new(Target::default()),
        };
        assert!(editor.target().ready);
        assert!(
            editor
                .select_external_with(|| anyhow::bail!("synthetic permission denied"))
                .is_err()
        );
        assert_eq!(CLOSES.load(Ordering::Relaxed), 1);
        assert!(editor.is_external());
        assert!(!editor.target().ready);
        assert!(!editor.insert("fixed fixture", 9));
        assert_eq!(INSERTS.load(Ordering::Relaxed), 0);
    }
}
