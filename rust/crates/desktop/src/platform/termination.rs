// SPDX-License-Identifier: GPL-3.0-only
//! Keep AppKit's termination decision pending until durable backend work ends.
//!
//! GPUI 0.3.7 creates its private delegate class at runtime and exposes only
//! applicationWillTerminate, whose shutdown futures have a 200 ms deadline.
//! This gate adds `applicationShouldTerminate:` to GPUI's delegate class and
//! `terminate:` to GPUI's application class; neither class defines them, so no
//! original IMP is changed. Instances keep their classes: AppKit may already
//! have KVO-subclassed NSApp, and re-subclassing a KVO class corrupts KVO
//! bookkeeping (macOS 27 crashes on activation). KVO subclasses inherit the
//! added methods. Without an installed gate both methods behave like AppKit's.
//! The application override moves terminate off GPUI's GCD main callback so
//! AppKit's pending-termination loop can still service GPUI tasks.
use super::class;
use crate::bridge::CommandSink;
use anyhow::{Context, Result, ensure};
use block2::RcBlock;
use objc2::{
    MainThreadMarker, msg_send,
    rc::Retained,
    runtime::{AnyClass, AnyObject, Imp, Sel},
    sel,
};
use objc2_foundation::NSString;
use serde_json::json;
use std::{
    cell::{Cell, RefCell},
    ffi::CStr,
};

const CANCEL: usize = 0;
const NOW: usize = 1;
const LATER: usize = 2;

#[derive(Clone, Copy, PartialEq, Eq)]
enum Phase {
    Installed,
    Waiting,
    Ready,
}
struct State {
    installation: u64,
    delegate: usize,
    application: usize,
    native_request_queued: bool,
    commands: CommandSink,
    phase: Phase,
    pending_native: bool,
    quit_published: bool,
}

/// AppKit's own `terminate:` lives on GPUIApplication's superclass.
fn appkit_application() -> Option<&'static AnyClass> {
    AnyClass::get(c"GPUIApplication").and_then(AnyClass::superclass)
}
enum Terminate {
    /// No gate owns this application: behave exactly like AppKit.
    Forward,
    /// A deferred request is already queued or awaiting the backend.
    Ignore,
    Defer(u64, Option<CommandSink>),
}
extern "C" fn defer_terminate(this: *mut AnyObject, _: Sel, sender: *mut AnyObject) {
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let decision = STATE.with_borrow_mut(|slot| {
            let Some(state) = slot
                .as_mut()
                .filter(|state| state.application == this as usize)
            else {
                return Terminate::Forward;
            };
            if state.native_request_queued || state.phase == Phase::Waiting {
                return Terminate::Ignore;
            }
            state.native_request_queued = true;
            let commands = (state.phase == Phase::Installed && !state.quit_published).then(|| {
                state.quit_published = true;
                state.commands.clone()
            });
            Terminate::Defer(state.installation, commands)
        });
        let Some(original) = appkit_application() else {
            return;
        };
        let (installation, commands) = match decision {
            Terminate::Ignore => return,
            Terminate::Forward => {
                unsafe {
                    let _: () = msg_send![super(&*this, original),terminate:sender];
                }
                return;
            }
            Terminate::Defer(installation, commands) => (installation, commands),
        };
        if let Some(commands) = commands {
            // Reject pending paste admission immediately, before the native
            // timer starts the ShouldTerminate decision on the next turn.
            commands.request("platform_quit", json!({"native_termination":true}));
        }
        // Both receiver and sender belong to this main-thread Objective-C
        // message and must survive until the next native run-loop turn.
        let application = unsafe { Retained::retain(this) }.expect("live NSApplication receiver");
        let sender = unsafe { Retained::retain(sender) };
        let callback = RcBlock::new(move |_timer: *mut AnyObject| {
            let active = STATE.with_borrow_mut(|slot| {
                let Some(state) = slot.as_mut().filter(|state| {
                    state.application == Retained::as_ptr(&application) as usize
                        && state.installation == installation
                }) else {
                    return false;
                };
                state.native_request_queued = false;
                true
            });
            if active {
                // Unlike dispatch_async(main), a Cocoa timer is not executing
                // inside the main GCD queue. Its nested NSTerminateLater loop
                // can therefore dispatch the GPUI tasks that await driver exit.
                unsafe {
                    let _: () =
                        msg_send![super(&*application, original),terminate:sender.as_deref()];
                }
            }
        });
        unsafe {
            let timer: Retained<AnyObject> = msg_send![class(c"NSTimer"),timerWithTimeInterval:0.001f64,repeats:false,block:&*callback];
            let run_loop: Retained<AnyObject> = msg_send![class(c"NSRunLoop"), mainRunLoop];
            let mode = NSString::from_str("kCFRunLoopCommonModes");
            let _: () = msg_send![&*run_loop,addTimer:&*timer,forMode:&*mode];
        }
    }));
    if result.is_err() {
        eprintln!("Could not defer the native application termination request");
    }
}
thread_local! {
    // All accesses are AppKit main-thread calls, and only one gate may exist.
    static STATE: RefCell<Option<State>> = const { RefCell::new(None) };
    static INSTALLATION: Cell<u64> = const { Cell::new(0) };
}

extern "C" fn should_terminate(this: *mut AnyObject, _: Sel, _: *mut AnyObject) -> usize {
    // A Rust panic must never unwind into AppKit. Fail closed if the callback
    // cannot safely publish the request; normal queue pressure is handled by
    // CommandSink's independent quit_requested atomic.
    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let (reply, commands) = STATE.with_borrow_mut(|slot| {
            let Some(state) = slot
                .as_mut()
                .filter(|state| state.delegate == this as usize)
            else {
                return (NOW, None);
            };
            match state.phase {
                Phase::Ready => (NOW, None),
                Phase::Waiting => (LATER, None),
                Phase::Installed => {
                    state.phase = Phase::Waiting;
                    state.pending_native = true;
                    let commands = (!state.quit_published).then(|| {
                        state.quit_published = true;
                        state.commands.clone()
                    });
                    (LATER, commands)
                }
            }
        });
        if let Some(commands) = commands {
            commands.request("platform_quit", json!({"native_termination":true}));
        }
        reply
    }))
    .unwrap_or(CANCEL)
}

/// Own separately from Platform: native resources close before storage finishes.
pub struct TerminationGate {
    installation: u64,
    _main_thread: MainThreadMarker,
    delegate: Retained<AnyObject>,
    // Keeps the instance whose address identifies this gate alive.
    _application: Retained<AnyObject>,
}

/// Add `selector` to `class` (which must not define it already), or accept
/// the identical IMP left by an earlier installation in this process.
fn add_method(class: &'static AnyClass, selector: Sel, imp: Imp, types: &CStr) -> Result<()> {
    if let Some(existing) = class
        .instance_methods()
        .iter()
        .find(|method| method.name() == selector)
    {
        ensure!(
            existing.implementation() as usize == imp as usize,
            "{} already defines {selector:?}; review its native hook before installing the gate",
            class.name().to_string_lossy()
        );
        return Ok(());
    }
    let added = unsafe {
        objc2::ffi::class_addMethod(
            (class as *const AnyClass).cast_mut(),
            selector,
            imp,
            types.as_ptr(),
        )
    };
    ensure!(added.as_bool(), "could not add {selector:?}");
    Ok(())
}
/// Find `name` in the class chain, looking through any KVO subclass.
fn ancestor(mut class: Option<&'static AnyClass>, name: &CStr) -> Option<&'static AnyClass> {
    while let Some(current) = class {
        if current.name() == name {
            return Some(current);
        }
        class = current.superclass();
    }
    None
}
impl TerminationGate {
    /// Install inside GPUI's did-finish-launching callback, before exposing UI.
    /// Only the pinned GPUI delegate is accepted; unrelated delegates are never
    /// replaced or subclassed, and a future native ShouldTerminate hook fails
    /// explicitly rather than being silently overridden.
    pub fn install(mtm: MainThreadMarker, commands: CommandSink) -> Result<Self> {
        ensure!(
            STATE.with_borrow(Option::is_none),
            "termination gate already installed"
        );
        let installation = INSTALLATION.with(|serial| -> Result<u64> {
            let next = serial
                .get()
                .checked_add(1)
                .context("termination installation counter exhausted")?;
            serial.set(next);
            Ok(next)
        })?;
        let application: Retained<AnyObject> =
            unsafe { msg_send![class(c"NSApplication"), sharedApplication] };
        let delegate: Option<Retained<AnyObject>> = unsafe { msg_send![&*application, delegate] };
        let delegate = delegate.context("GPUI application delegate is not installed yet")?;
        let gpui_application = ancestor(Some(application.class()), c"GPUIApplication")
            .with_context(|| {
                format!(
                    "termination gate requires the pinned GPUI application, found {}",
                    application.class().name().to_string_lossy()
                )
            })?;
        let gpui_delegate = ancestor(Some(delegate.class()), c"GPUIApplicationDelegate")
            .with_context(|| {
                format!(
                    "termination gate requires the pinned GPUI delegate, found {}",
                    delegate.class().name().to_string_lossy()
                )
            })?;
        ensure!(
            appkit_application().is_some(),
            "GPUI application has no AppKit superclass"
        );
        // NSUInteger return and the original NSObject receiver ABI match
        // NSApplicationDelegate.applicationShouldTerminate exactly.
        type ShouldTerminate = extern "C" fn(*mut AnyObject, Sel, *mut AnyObject) -> usize;
        type Terminate = extern "C" fn(*mut AnyObject, Sel, *mut AnyObject);
        add_method(
            gpui_delegate,
            sel!(applicationShouldTerminate:),
            unsafe { std::mem::transmute::<ShouldTerminate, Imp>(should_terminate) },
            c"Q@:@",
        )?;
        add_method(
            gpui_application,
            sel!(terminate:),
            unsafe { std::mem::transmute::<Terminate, Imp>(defer_terminate) },
            c"v@:@",
        )?;
        STATE.with_borrow_mut(|slot| {
            *slot = Some(State {
                installation,
                delegate: Retained::as_ptr(&delegate) as usize,
                application: Retained::as_ptr(&application) as usize,
                native_request_queued: false,
                commands,
                phase: Phase::Installed,
                pending_native: false,
                quit_published: false,
            })
        });
        Ok(Self {
            installation,
            _main_thread: mtm,
            delegate,
            _application: application,
        })
    }
    pub fn is_waiting(&self) -> bool {
        STATE.with_borrow(|slot| {
            slot.as_ref().is_some_and(|state| {
                state.delegate == Retained::as_ptr(&self.delegate) as usize
                    && state.installation == self.installation
                    && state.phase == Phase::Waiting
            })
        })
    }
    /// Call only after BackendDriver::finished confirms accepted edits saved.
    /// Repeated calls are harmless. Native quit requests receive their pending
    /// reply; a menu/automation quit initiates termination after opening the gate.
    /// Actual termination is queued on the main run loop so this may safely be
    /// called while GPUI's App is borrowed inside an Entity/AsyncApp update.
    pub fn finish(&mut self) -> Result<()> {
        let application: Retained<AnyObject> =
            unsafe { msg_send![class(c"NSApplication"), sharedApplication] };
        let current: Option<Retained<AnyObject>> = unsafe { msg_send![&*application, delegate] };
        ensure!(
            current.as_ref().is_some_and(
                |current| Retained::as_ptr(current) == Retained::as_ptr(&self.delegate)
            ),
            "application delegate changed before termination finished"
        );
        let phase = STATE.with_borrow_mut(|slot| {
            let state = slot.as_mut().expect("installed termination state");
            let previous = state.phase;
            state.phase = Phase::Ready;
            previous
        });
        if phase == Phase::Ready {
            return Ok(());
        }
        let delegate = Retained::as_ptr(&self.delegate) as usize;
        let installation = self.installation;
        let operation = RcBlock::new(move || {
            let should_finish = STATE.with_borrow_mut(|slot| {
                let Some(state) = slot.as_mut().filter(|state| {
                    state.delegate == delegate
                        && state.installation == installation
                        && state.phase == Phase::Ready
                }) else {
                    // Owner dropped before this queued operation executed.
                    return false;
                };
                state.pending_native = false;
                true
            });
            if !should_finish {
                return;
            }
            unsafe {
                match phase {
                    Phase::Waiting => {
                        let _: () = msg_send![&*application,replyToApplicationShouldTerminate:true];
                    }
                    Phase::Installed => {
                        let _: () =
                            msg_send![&*application,terminate:std::ptr::null::<AnyObject>()];
                    }
                    Phase::Ready => unreachable!(),
                }
            }
        });
        unsafe {
            let queue: Retained<AnyObject> = msg_send![class(c"NSOperationQueue"), mainQueue];
            let _: () = msg_send![&*queue,addOperationWithBlock:&*operation];
        }
        Ok(())
    }
}
impl Drop for TerminationGate {
    fn drop(&mut self) {
        let pending =
            STATE.with_borrow_mut(|slot| slot.take().is_some_and(|state| state.pending_native));
        // The added methods stay on the GPUI classes; with no state they
        // forward to AppKit's behavior, so nothing needs restoring.
        if pending {
            // Dropping an unfinished owner must not leave AppKit waiting forever.
            let application: Retained<AnyObject> =
                unsafe { msg_send![class(c"NSApplication"), sharedApplication] };
            unsafe {
                let _: () = msg_send![&*application,replyToApplicationShouldTerminate:false];
            }
        }
    }
}
