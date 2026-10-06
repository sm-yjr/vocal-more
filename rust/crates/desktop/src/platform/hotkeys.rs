// SPDX-License-Identifier: GPL-3.0-only
use super::{class, ffi, ns};
use crate::bridge::CommandSink;
use objc2::{
    msg_send,
    rc::{Retained, autoreleasepool},
    runtime::AnyObject,
};
use serde_json::{Value, json};
use std::{
    collections::BTreeSet,
    ptr,
    sync::{Arc, Mutex, mpsc},
    thread::{self, JoinHandle},
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Binding {
    code: u16,
    modifier: bool,
    flags: u64,
}
#[derive(Default)]
struct State {
    bindings: Vec<Binding>,
    modifiers: BTreeSet<u16>,
    regular: BTreeSet<u16>,
    capture: bool,
    capture_modifiers: BTreeSet<u16>,
    double_cmd: bool,
    double_tap_threshold: std::time::Duration,
    cmd_down: BTreeSet<u16>,
    cmd_interrupted: bool,
    last_cmd_up: Option<std::time::Instant>,
    tap: usize,
    run_loop: usize,
    running: bool,
    disabled_count: u64,
}
impl State {
    fn pressed(&self) -> bool {
        !self.modifiers.is_empty() || !self.regular.is_empty()
    }
    fn configure(&mut self, config: &Value) {
        self.bindings.clear();
        let active = config
            .pointer("/hotkey/active_hotkeys")
            .and_then(Value::as_array);
        let uses_fn = active.map(|a| a.iter().any(|v| v == "fn")).unwrap_or(true);
        if uses_fn {
            self.bindings.push(Binding {
                code: 63,
                modifier: true,
                flags: 0x800000,
            });
        }
        self.double_cmd = active.is_some_and(|a| a.iter().any(|v| v == "double_cmd"))
            || config
                .pointer("/hotkey/double_cmd")
                .and_then(Value::as_bool)
                .unwrap_or(false);
        self.double_tap_threshold = std::time::Duration::from_secs_f64(
            config
                .pointer("/hotkey/double_tap_threshold")
                .and_then(Value::as_f64)
                .filter(|value| value.is_finite())
                .unwrap_or(0.3)
                .clamp(0.15, 0.5),
        );
        self.cmd_down.clear();
        self.cmd_interrupted = false;
        self.last_cmd_up = None;
        let keys = config
            .pointer("/hotkey/custom_keys")
            .and_then(Value::as_array);
        let legacy = config.pointer("/hotkey/custom_key");
        for value in keys
            .filter(|a| !a.is_empty())
            .map(|a| a.iter().collect::<Vec<_>>())
            .unwrap_or_else(|| legacy.filter(|v| v.is_object()).into_iter().collect())
        {
            let Some(code) = value["key_code"]
                .as_u64()
                .and_then(|v| u16::try_from(v).ok())
            else {
                continue;
            };
            if let Some((_, modifier, flags)) = key_definition(code)
                && value["is_modifier"].as_bool() == Some(modifier)
                && value["flag_mask"].as_u64() == Some(flags)
            {
                self.bindings.push(Binding {
                    code,
                    modifier,
                    flags,
                });
            }
        }
        self.modifiers
            .retain(|key| self.bindings.iter().any(|b| b.modifier && b.code == *key));
        self.regular
            .retain(|key| self.bindings.iter().any(|b| !b.modifier && b.code == *key));
    }
    fn event(
        &mut self,
        kind: u32,
        code: u16,
        flags: u64,
        repeat: bool,
    ) -> (bool, Vec<(&'static str, Value)>) {
        self.event_at(kind, code, flags, repeat, std::time::Instant::now())
    }
    fn event_at(
        &mut self,
        kind: u32,
        code: u16,
        flags: u64,
        repeat: bool,
        now: std::time::Instant,
    ) -> (bool, Vec<(&'static str, Value)>) {
        let mut output = Vec::new();
        if self.capture {
            if let Some((name, modifier, mask)) = key_definition(code) {
                let down = if modifier && kind == 12 {
                    if self.capture_modifiers.remove(&code) {
                        false
                    } else if flags & mask != 0 {
                        self.capture_modifiers.insert(code);
                        true
                    } else {
                        false
                    }
                } else {
                    !modifier && kind == 10
                };
                if down && !repeat {
                    output.push(("platform_event", json!({"method":"hotkey_capture","params":{
                    "key_code":code,"display_name":name,"is_modifier":modifier,"flag_mask":mask,"repeat":false
                }})));
                }
            }
            return (true, output);
        }
        let before = self.pressed();
        if let Some(binding) = self.bindings.iter().copied().find(|b| b.code == code) {
            self.last_cmd_up = None;
            self.cmd_interrupted = true;
            if binding.modifier && kind == 12 {
                // flags aggregate both siblings. A physical key's second change
                // is its release even if the other sibling keeps the flag set.
                if !self.modifiers.remove(&code) && flags & binding.flags != 0 {
                    self.modifiers.insert(code);
                }
            } else if !binding.modifier && kind == 10 {
                self.regular.insert(code);
            } else if !binding.modifier && kind == 11 {
                self.regular.remove(&code);
            }
            let after = self.pressed();
            if !before && after {
                output.push(("hotkey_pressed", json!({})));
            } else if before && !after {
                output.push(("hotkey_released", json!({})));
            }
            return (true, output);
        }
        if kind == 10 && code == 53 && !repeat {
            output.push(("cancel", json!({})));
        }
        if self.double_cmd && kind == 12 && matches!(code, 54 | 55) {
            let was_down = self.cmd_down.remove(&code);
            if !was_down && flags & 0x100000 != 0 {
                if self.cmd_down.is_empty() {
                    self.cmd_interrupted = false;
                } else {
                    // Both physical Command keys held together form one chord,
                    // never two taps when their releases arrive separately.
                    self.cmd_interrupted = true;
                    self.last_cmd_up = None;
                }
                self.cmd_down.insert(code);
            } else if was_down && self.cmd_down.is_empty() && !self.cmd_interrupted {
                if self.last_cmd_up.take().is_some_and(|time| {
                    now.saturating_duration_since(time) <= self.double_tap_threshold
                }) {
                    output.push(("toggle_recording", json!({})));
                } else {
                    self.last_cmd_up = Some(now);
                }
            }
        } else if kind == 10 || kind == 12 {
            self.last_cmd_up = None;
            self.cmd_interrupted = true;
        }
        (false, output)
    }
}

struct TapContext {
    state: Arc<Mutex<State>>,
    commands: CommandSink,
}
unsafe extern "C" fn callback(
    _: ffi::MutRef,
    kind: u32,
    event: ffi::MutRef,
    user: ffi::MutRef,
) -> ffi::MutRef {
    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        autoreleasepool(|_| {
            let context = unsafe { &*(user.cast::<TapContext>()) };
            if kind >= 0xffff_fffe {
                let mut state = context.state.lock().unwrap_or_else(|p| p.into_inner());
                if state.running && state.tap != 0 {
                    state.disabled_count += 1;
                    if state.pressed() {
                        context.commands.request("hotkey_released", json!({}));
                    }
                    state.modifiers.clear();
                    state.regular.clear();
                    state.capture_modifiers.clear();
                    state.cmd_down.clear();
                    state.cmd_interrupted = false;
                    state.last_cmd_up = None;
                    unsafe {
                        ffi::CGEventTapEnable(state.tap as ffi::Ref, true);
                    }
                }
                return event;
            }
            if event.is_null() {
                return event;
            }
            // Our own clipboard shortcut must not retrigger a user binding for V
            // or Command. Unmarked events from physical keyboards are unaffected.
            if unsafe { ffi::CGEventGetIntegerValueField(event, 42) } == super::paste::OWN_EVENT_TAG
            {
                return event;
            }
            if !context
                .state
                .lock()
                .unwrap_or_else(|p| p.into_inner())
                .running
            {
                return event;
            }
            let code = unsafe { ffi::CGEventGetIntegerValueField(event, 9) as u16 };
            let flags = unsafe { ffi::CGEventGetFlags(event) };
            let repeat = unsafe { ffi::CGEventGetIntegerValueField(event, 8) != 0 };
            let (consume, messages) = context
                .state
                .lock()
                .unwrap_or_else(|p| p.into_inner())
                .event(kind, code, flags, repeat);
            for (method, params) in messages {
                context.commands.request(method, params);
            }
            if consume { ptr::null_mut() } else { event }
        })
    }))
    .unwrap_or(event)
}

pub struct Hotkeys {
    state: Arc<Mutex<State>>,
    commands: CommandSink,
    worker: Option<JoinHandle<()>>,
    guard: FnGuard,
    disabled: bool,
}
impl Hotkeys {
    pub fn new(commands: CommandSink, config: &Value, disabled: bool) -> Self {
        let mut state = State::default();
        state.configure(config);
        let mut this = Self {
            state: Arc::new(Mutex::new(state)),
            commands,
            worker: None,
            guard: FnGuard::default(),
            disabled,
        };
        this.start();
        this
    }
    fn sync_guard(&mut self) {
        if self.disabled {
            return;
        }
        let needs_fn = {
            let s = self.state.lock().unwrap();
            s.running
                && s.tap != 0
                && unsafe {
                    ffi::AXIsProcessTrusted() && ffi::CGEventTapIsEnabled(s.tap as ffi::Ref)
                }
                && (s.capture || s.bindings.iter().any(|b| b.code == 63))
        };
        if needs_fn {
            self.guard.suppress();
        } else {
            self.guard.restore();
        }
    }
    fn start(&mut self) {
        if self.disabled {
            return;
        }
        if self.worker.as_ref().is_some_and(|t| !t.is_finished()) {
            return;
        }
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
        let uses_fn = self
            .state
            .lock()
            .unwrap()
            .bindings
            .iter()
            .any(|b| b.code == 63);
        if uses_fn {
            self.guard.suppress();
        } else {
            self.guard.restore();
        }
        let state = self.state.clone();
        let commands = self.commands.clone();
        let (ready, receiver) = mpsc::sync_channel(1);
        self.worker = thread::Builder::new()
            .name("vocal-more-event-tap".into())
            .spawn(move || {
                autoreleasepool(|_| {
                    let mut context = Box::new(TapContext {
                        state: state.clone(),
                        commands,
                    });
                    let mask = (1u64 << 10) | (1u64 << 11) | (1u64 << 12);
                    let Some(tap) = (unsafe {
                        ffi::Owned::from_create(ffi::CGEventTapCreate(
                            0,
                            0,
                            0,
                            mask,
                            callback,
                            (&mut *context as *mut TapContext).cast(),
                        ))
                    }) else {
                        let _ = ready.send(false);
                        return;
                    };
                    let Some(source) = (unsafe {
                        ffi::Owned::from_create(ffi::CFMachPortCreateRunLoopSource(
                            ptr::null(),
                            tap.as_ptr(),
                            0,
                        ))
                    }) else {
                        let _ = ready.send(false);
                        return;
                    };
                    let run_loop = unsafe { ffi::CFRunLoopGetCurrent() };
                    unsafe {
                        ffi::CFRunLoopAddSource(
                            run_loop,
                            source.as_ptr(),
                            ffi::kCFRunLoopCommonModes,
                        );
                        ffi::CGEventTapEnable(tap.as_ptr(), true);
                    }
                    {
                        let mut s = state.lock().unwrap();
                        s.tap = tap.as_ptr() as usize;
                        s.run_loop = run_loop as usize;
                        s.running = true;
                    }
                    let _ = ready.send(true);
                    unsafe {
                        ffi::CFRunLoopRun();
                    }
                    {
                        let mut s = state.lock().unwrap();
                        s.running = false;
                        s.tap = 0;
                        s.run_loop = 0;
                        s.modifiers.clear();
                        s.regular.clear();
                    }
                    unsafe {
                        ffi::CGEventTapEnable(tap.as_ptr(), false);
                        ffi::CFRunLoopRemoveSource(
                            run_loop,
                            source.as_ptr(),
                            ffi::kCFRunLoopCommonModes,
                        );
                        ffi::CFMachPortInvalidate(tap.as_ptr());
                    }
                })
            })
            .ok();
        let _ = receiver.recv_timeout(std::time::Duration::from_millis(300));
        self.sync_guard();
    }
    pub fn configure(&mut self, config: &Value) {
        let release = {
            let mut s = self.state.lock().unwrap();
            let before = s.pressed();
            s.configure(config);
            before && !s.pressed()
        };
        if release {
            self.commands.request("hotkey_released", json!({}));
        }
        self.sync_guard();
    }
    pub fn capture(&mut self, active: bool) {
        let release = {
            let mut s = self.state.lock().unwrap();
            let held = s.pressed();
            s.capture = active;
            s.modifiers.clear();
            s.regular.clear();
            s.capture_modifiers.clear();
            s.cmd_down.clear();
            s.cmd_interrupted = false;
            s.last_cmd_up = None;
            held
        };
        if release {
            self.commands.request("hotkey_released", json!({}));
        }
        self.sync_guard();
    }
    pub fn tick(&mut self) {
        self.start();
        if self.disabled {
            return;
        }
        {
            let state = self.state.lock().unwrap();
            if state.running
                && state.tap != 0
                && !unsafe { ffi::CGEventTapIsEnabled(state.tap as ffi::Ref) }
            {
                unsafe {
                    ffi::CGEventTapEnable(state.tap as ffi::Ref, true);
                }
            }
        }
        // If TCC or a system disable made filtering unavailable, release the
        // standalone Fn preference. Reacquire it after the listener recovers.
        self.sync_guard();
    }
    pub fn status(&self) -> Value {
        let s = self.state.lock().unwrap();
        let enabled = s.tap != 0 && unsafe { ffi::CGEventTapIsEnabled(s.tap as ffi::Ref) };
        json!({"running":s.running,"event_tap_present":s.tap != 0,"event_tap_enabled":enabled,
            "event_thread_alive":self.worker.as_ref().is_some_and(|t| !t.is_finished()),
            "configured_dictation_triggers":s.bindings.len(),"pressed_modifier_count":s.modifiers.len(),
            "pressed_regular_count":s.regular.len(),"capture":s.capture,"disabled_count":s.disabled_count,
            "uses_fn_key":s.bindings.iter().any(|b|b.code==63),"fn_suppressed":self.guard.suppressed})
    }
}
impl Drop for Hotkeys {
    fn drop(&mut self) {
        {
            let mut s = self.state.lock().unwrap();
            s.running = false;
            if s.run_loop != 0 {
                unsafe {
                    ffi::CFRunLoopStop(s.run_loop as ffi::Ref);
                }
            }
        }
        if let Some(worker) = self.worker.take() {
            let deadline = std::time::Instant::now() + std::time::Duration::from_millis(250);
            while !worker.is_finished() && std::time::Instant::now() < deadline {
                std::thread::sleep(std::time::Duration::from_millis(2));
            }
            if worker.is_finished() {
                let _ = worker.join();
            }
            // An OS run-loop delay retains the thread-owned port/context until
            // it returns; nothing borrowed from Hotkeys is freed underneath it.
        }
        if !self.disabled {
            self.guard.restore();
        }
    }
}

#[derive(Default)]
struct FnGuard {
    suppressed: bool,
}
const PREF_DOMAIN: &str = "com.sm-yjr.vocal-more";
const RECOVERY: [&str; 3] = [
    "FnSystemActionGuardActive",
    "FnSystemActionGuardOriginalValue",
    "FnSystemActionGuardOriginalWasExplicit",
];
#[cfg(test)]
static PREFERENCE_OPERATIONS: std::sync::atomic::AtomicUsize =
    std::sync::atomic::AtomicUsize::new(0);
fn copy_pref(key: &str, domain: &str) -> Option<ffi::Owned> {
    #[cfg(test)]
    PREFERENCE_OPERATIONS.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
    unsafe {
        ffi::Owned::from_create(ffi::CFPreferencesCopyValue(
            (&*ns(key) as *const objc2_foundation::NSString).cast(),
            (&*ns(domain) as *const objc2_foundation::NSString).cast(),
            ffi::kCFPreferencesCurrentUser,
            ffi::kCFPreferencesAnyHost,
        ))
    }
}
fn set_pref(key: &str, value: ffi::Ref, domain: &str) {
    #[cfg(test)]
    PREFERENCE_OPERATIONS.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
    unsafe {
        ffi::CFPreferencesSetValue(
            (&*ns(key) as *const objc2_foundation::NSString).cast(),
            value,
            (&*ns(domain) as *const objc2_foundation::NSString).cast(),
            ffi::kCFPreferencesCurrentUser,
            ffi::kCFPreferencesAnyHost,
        );
    }
}
fn synchronize(domain: &str) -> bool {
    #[cfg(test)]
    PREFERENCE_OPERATIONS.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
    unsafe {
        ffi::CFPreferencesSynchronize(
            (&*ns(domain) as *const objc2_foundation::NSString).cast(),
            ffi::kCFPreferencesCurrentUser,
            ffi::kCFPreferencesAnyHost,
        )
    }
}
fn number(value: isize) -> Retained<AnyObject> {
    unsafe { msg_send![class(c"NSNumber"), numberWithInteger:value] }
}
fn boolean(value: bool) -> Retained<AnyObject> {
    unsafe { msg_send![class(c"NSNumber"),numberWithBool:value] }
}
fn integer(value: &ffi::Owned) -> Option<isize> {
    unsafe {
        if ffi::CFGetTypeID(value.as_ptr()) != ffi::CFNumberGetTypeID() {
            return None;
        }
        Some(msg_send![
            &*(value.as_ptr().cast::<AnyObject>()),
            integerValue
        ])
    }
}
fn truth(value: &ffi::Owned) -> Option<bool> {
    unsafe {
        if ffi::CFGetTypeID(value.as_ptr()) != ffi::CFBooleanGetTypeID() {
            return None;
        }
        Some(msg_send![&*(value.as_ptr().cast::<AnyObject>()), boolValue])
    }
}
fn tis() -> Option<(
    *mut std::ffi::c_void,
    unsafe extern "C" fn() -> i32,
    unsafe extern "C" fn(i32),
)> {
    unsafe {
        let handle = ffi::dlopen(
            c"/System/Library/Frameworks/Carbon.framework/Carbon".as_ptr(),
            1,
        );
        if handle.is_null() {
            return None;
        }
        let get = ffi::dlsym(handle, c"TISGetFnUsageType".as_ptr());
        let set = ffi::dlsym(handle, c"TISUpdateFnUsageType".as_ptr());
        if get.is_null() || set.is_null() {
            ffi::dlclose(handle);
            return None;
        }
        Some((
            handle,
            std::mem::transmute::<ffi::MutRef, unsafe extern "C" fn() -> i32>(get),
            std::mem::transmute::<ffi::MutRef, unsafe extern "C" fn(i32)>(set),
        ))
    }
}
impl FnGuard {
    fn recovery() -> Option<(i32, bool)> {
        if !truth(&copy_pref(RECOVERY[0], PREF_DOMAIN)?)? {
            return None;
        }
        let value = integer(&copy_pref(RECOVERY[1], PREF_DOMAIN)?)?;
        let explicit = truth(&copy_pref(RECOVERY[2], PREF_DOMAIN)?)?;
        (0..4).contains(&value).then_some((value as i32, explicit))
    }
    fn suppress(&mut self) -> bool {
        if self.suppressed {
            return true;
        }
        let Some((handle, get, set)) = tis() else {
            return false;
        };
        let result = (|| {
            if Self::recovery().is_none() {
                let effective = unsafe { get() };
                if !(0..4).contains(&effective) {
                    return false;
                }
                let saved = copy_pref("AppleFnUsageType", "com.apple.HIToolbox")
                    .and_then(|v| integer(&v))
                    .filter(|v| (0..4).contains(v));
                set_pref(
                    RECOVERY[1],
                    Retained::as_ptr(&number(saved.unwrap_or(effective as isize))).cast(),
                    PREF_DOMAIN,
                );
                set_pref(
                    RECOVERY[2],
                    Retained::as_ptr(&boolean(saved.is_some())).cast(),
                    PREF_DOMAIN,
                );
                set_pref(
                    RECOVERY[0],
                    Retained::as_ptr(&boolean(true)).cast(),
                    PREF_DOMAIN,
                );
                if !synchronize(PREF_DOMAIN) {
                    return false;
                }
            }
            unsafe {
                set(0);
                get() == 0
            }
        })();
        unsafe {
            ffi::dlclose(handle);
        }
        self.suppressed = result;
        result
    }
    fn restore(&mut self) -> bool {
        let Some((original, explicit)) = Self::recovery() else {
            self.suppressed = false;
            return true;
        };
        let Some((handle, get, set)) = tis() else {
            return false;
        };
        unsafe {
            set(original);
        }
        let implicit_ok = explicit || {
            set_pref("AppleFnUsageType", ptr::null(), "com.apple.HIToolbox");
            synchronize("com.apple.HIToolbox")
        };
        let restored = implicit_ok && unsafe { get() == original };
        unsafe {
            ffi::dlclose(handle);
        }
        if !restored {
            return false;
        }
        for key in RECOVERY {
            set_pref(key, ptr::null(), PREF_DOMAIN);
        }
        if !synchronize(PREF_DOMAIN) {
            return false;
        }
        self.suppressed = false;
        true
    }
}

pub fn key_definition(code: u16) -> Option<(&'static str, bool, u64)> {
    let modifier = match code {
        54 => Some(("Right Command", 0x100000)),
        55 => Some(("Left Command", 0x100000)),
        56 => Some(("Left Shift", 0x20000)),
        60 => Some(("Right Shift", 0x20000)),
        58 => Some(("Left Option", 0x80000)),
        61 => Some(("Right Option", 0x80000)),
        59 => Some(("Left Control", 0x40000)),
        62 => Some(("Right Control", 0x40000)),
        57 => Some(("Caps Lock", 0x10000)),
        63 => Some(("Fn", 0x800000)),
        _ => None,
    };
    if let Some((name, flag)) = modifier {
        return Some((name, true, flag));
    }
    let name = match code {
        0 => "A",
        1 => "S",
        2 => "D",
        3 => "F",
        4 => "H",
        5 => "G",
        6 => "Z",
        7 => "X",
        8 => "C",
        9 => "V",
        11 => "B",
        12 => "Q",
        13 => "W",
        14 => "E",
        15 => "R",
        16 => "Y",
        17 => "T",
        18 => "1",
        19 => "2",
        20 => "3",
        21 => "4",
        22 => "6",
        23 => "5",
        24 => "=",
        25 => "9",
        26 => "7",
        27 => "-",
        28 => "8",
        29 => "0",
        30 => "]",
        31 => "O",
        32 => "U",
        33 => "[",
        34 => "I",
        35 => "P",
        36 => "Return",
        37 => "L",
        38 => "J",
        39 => "'",
        40 => "K",
        41 => ";",
        42 => "\\",
        43 => ",",
        44 => "/",
        45 => "N",
        46 => "M",
        47 => ".",
        48 => "Tab",
        49 => "Space",
        50 => "`",
        51 => "Delete",
        53 => "Escape",
        64 => "F17",
        65 => "Numpad .",
        67 => "Numpad *",
        69 => "Numpad +",
        71 => "Numpad Clear",
        75 => "Numpad /",
        76 => "Numpad Enter",
        78 => "Numpad -",
        79 => "F18",
        80 => "F19",
        81 => "Numpad =",
        82 => "Numpad 0",
        83 => "Numpad 1",
        84 => "Numpad 2",
        85 => "Numpad 3",
        86 => "Numpad 4",
        87 => "Numpad 5",
        88 => "Numpad 6",
        89 => "Numpad 7",
        90 => "F20",
        91 => "Numpad 8",
        92 => "Numpad 9",
        96 => "F5",
        97 => "F6",
        98 => "F7",
        99 => "F3",
        100 => "F8",
        101 => "F9",
        103 => "F11",
        105 => "F13",
        106 => "F16",
        107 => "F14",
        109 => "F10",
        111 => "F12",
        113 => "F15",
        114 => "Help",
        115 => "Home",
        116 => "Page Up",
        117 => "Forward Delete",
        118 => "F4",
        119 => "End",
        120 => "F2",
        121 => "Page Down",
        122 => "F1",
        123 => "Left Arrow",
        124 => "Right Arrow",
        125 => "Down Arrow",
        126 => "Up Arrow",
        _ => return None,
    };
    Some((name, false, 0))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn held_sibling_and_regular_repeats_preserve_edges() {
        let mut s = State {
            bindings: vec![
                Binding {
                    code: 55,
                    modifier: true,
                    flags: 0x100000,
                },
                Binding {
                    code: 105,
                    modifier: false,
                    flags: 0,
                },
            ],
            ..State::default()
        };
        assert_eq!(s.event(12, 55, 0x100000, false).1[0].0, "hotkey_pressed");
        assert!(s.event(10, 105, 0, false).1.is_empty());
        assert!(s.event(10, 105, 0, true).1.is_empty());
        assert!(s.event(12, 55, 0x100000, false).1.is_empty()); // other Command still held
        assert_eq!(s.event(11, 105, 0, false).1[0].0, "hotkey_released");
    }
    #[test]
    fn capture_suppresses_dictation_and_resolves_physical_variant() {
        let mut s = State {
            capture: true,
            ..State::default()
        };
        let (consume, messages) = s.event(12, 61, 0x80000, false);
        assert!(consume);
        assert_eq!(messages[0].1["params"]["display_name"], "Right Option");
        assert!(s.event(12, 61, 0x80000, false).1.is_empty());
        assert!(s.event(10, 4, 0, true).1.is_empty());
        assert_eq!(s.event(10, 4, 0, false).1[0].1["params"]["key_code"], 4);
    }
    #[test]
    fn malformed_custom_binding_cannot_change_modifier_semantics() {
        let mut s = State::default();
        s.configure(&json!({"hotkey":{"active_hotkeys":[],"custom_keys":[{"key_code":55,"is_modifier":false,"flag_mask":0}]}}));
        assert!(s.bindings.is_empty());
    }
    fn double_cmd_state(threshold: f64) -> State {
        let mut state = State::default();
        state.configure(
            &json!({"hotkey":{"active_hotkeys":["double_cmd"],"double_tap_threshold":threshold}}),
        );
        state
    }
    fn command_tap(
        state: &mut State,
        code: u16,
        release: std::time::Instant,
    ) -> Vec<(&'static str, Value)> {
        let (consume, output) = state.event_at(
            12,
            code,
            0x100000,
            false,
            release - std::time::Duration::from_millis(10),
        );
        assert!(!consume);
        assert!(output.is_empty());
        let (consume, output) = state.event_at(12, code, 0, false, release);
        assert!(!consume);
        output
    }
    #[test]
    fn double_command_taps_use_configured_threshold_and_emit_once() {
        let mut state = double_cmd_state(0.3);
        let now = std::time::Instant::now();
        assert!(command_tap(&mut state, 55, now).is_empty());
        let output = command_tap(&mut state, 54, now + std::time::Duration::from_millis(300));
        assert_eq!(output, vec![("toggle_recording", json!({}))]);
        assert!(
            command_tap(&mut state, 55, now + std::time::Duration::from_millis(310),).is_empty()
        );
        assert!(
            command_tap(&mut state, 54, now + std::time::Duration::from_millis(611),).is_empty()
        );
        state.configure(
            &json!({"hotkey":{"active_hotkeys":["double_cmd"],"double_tap_threshold":9}}),
        );
        assert_eq!(
            state.double_tap_threshold,
            std::time::Duration::from_millis(500)
        );
        state.configure(
            &json!({"hotkey":{"active_hotkeys":["double_cmd"],"double_tap_threshold":0}}),
        );
        assert_eq!(
            state.double_tap_threshold,
            std::time::Duration::from_millis(150)
        );
    }
    #[test]
    fn command_shortcuts_and_overlapping_siblings_cannot_count_as_taps() {
        let mut state = double_cmd_state(0.3);
        let now = std::time::Instant::now();
        assert!(command_tap(&mut state, 55, now).is_empty());
        state.event_at(12, 55, 0x100000, false, now);
        state.event_at(10, 9, 0x100000, false, now); // Command+V
        assert!(state.event_at(12, 55, 0, false, now).1.is_empty());
        assert!(command_tap(&mut state, 55, now).is_empty());
        state.event_at(12, 55, 0x100000, false, now);
        state.event_at(12, 54, 0x100000, false, now);
        assert!(state.event_at(12, 55, 0x100000, false, now).1.is_empty());
        assert!(state.event_at(12, 54, 0, false, now).1.is_empty());
        assert!(command_tap(&mut state, 54, now).is_empty());
    }
    #[test]
    fn custom_command_binding_has_press_release_priority_over_double_taps() {
        let mut state = double_cmd_state(0.3);
        state.configure(&json!({"hotkey":{"active_hotkeys":["double_cmd"],"custom_keys":[{"key_code":55,"is_modifier":true,"flag_mask":0x100000}]}}));
        let now = std::time::Instant::now();
        for _ in 0..2 {
            assert_eq!(
                state.event_at(12, 55, 0x100000, false, now),
                (true, vec![("hotkey_pressed", json!({}))])
            );
            assert_eq!(
                state.event_at(12, 55, 0, false, now),
                (true, vec![("hotkey_released", json!({}))])
            );
        }
    }
    #[test]
    fn ordinary_configuration_never_enables_legacy_double_command() {
        let mut state = State::default();
        state.configure(&json!({"hotkey":{"active_hotkeys":["fn"]}}));
        let now = std::time::Instant::now();
        assert!(!state.double_cmd);
        assert!(command_tap(&mut state, 55, now).is_empty());
        assert!(command_tap(&mut state, 55, now).is_empty());
    }
    #[test]
    fn isolated_listener_configuration_and_capture_never_touch_fn_preferences() {
        use crate::bridge::BackendDriver;
        let directory = tempfile::tempdir().unwrap();
        let (driver, sink, _) = BackendDriver::start(
            vocal_more_backend::application::Options::new(directory.path().into()),
        )
        .unwrap();
        let before = PREFERENCE_OPERATIONS.load(std::sync::atomic::Ordering::SeqCst);
        let mut listener = Hotkeys::new(sink, &json!({"hotkey":{"active_hotkeys":["fn"]}}), true);
        listener.configure(&json!({"hotkey":{"active_hotkeys":[],"custom_keys":[]}}));
        listener.capture(true);
        listener.capture(false);
        listener.tick();
        drop(listener);
        assert_eq!(
            PREFERENCE_OPERATIONS.load(std::sync::atomic::Ordering::SeqCst),
            before
        );
        driver.close();
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(2);
        while !driver.finished() && std::time::Instant::now() < deadline {
            std::thread::sleep(std::time::Duration::from_millis(2));
        }
        assert!(driver.finished());
    }
}
