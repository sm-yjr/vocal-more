// SPDX-License-Identifier: GPL-3.0-only
//! Keep AppKit's termination decision pending until durable backend work ends.
//!
//! GPUI 0.3.7 creates its private delegate class at runtime and exposes only
//! applicationWillTerminate, whose shutdown futures have a 200 ms deadline.
//! This gate changes the existing delegate and application instances to no-ivar
//! subclasses. All original GPUI methods/ivars remain inherited; no original
//! IMP is changed. The application override moves terminate off GPUI's GCD main
//! callback so AppKit's pending-termination loop can still service GPUI tasks.
use super::class;
use crate::bridge::CommandSink;
use anyhow::{Context, Result, ensure};
use block2::RcBlock;
use objc2::{
    MainThreadMarker, msg_send,
    rc::Retained,
    runtime::{AnyClass, AnyObject, ClassBuilder, Sel},
    sel,
};
use objc2_foundation::NSString;
use serde_json::json;
use std::cell::{Cell, RefCell};

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
    original_application: &'static AnyClass,
    native_request_queued: bool,
    commands: CommandSink,
    phase: Phase,
    pending_native: bool,
    quit_published: bool,
}

extern "C" fn defer_terminate(this: *mut AnyObject, _: Sel, sender: *mut AnyObject) {
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let original = STATE.with_borrow_mut(|slot| {
            let state = slot
                .as_mut()
                .filter(|state| state.application == this as usize)?;
            if state.native_request_queued || state.phase == Phase::Waiting {
                return None;
            }
            state.native_request_queued = true;
            let commands = (state.phase == Phase::Installed && !state.quit_published).then(|| {
                state.quit_published = true;
                state.commands.clone()
            });
            Some((state.original_application, state.installation, commands))
        });
        let Some((original, installation, commands)) = original else {
            return;
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
    original: &'static AnyClass,
    subclass: &'static AnyClass,
    application: Retained<AnyObject>,
    original_application: &'static AnyClass,
    application_subclass: &'static AnyClass,
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
        let original_application = application.class();
        // AppKit installs NSKVONotifying_GPUIApplication while observing its
        // delegate. Inherit that exact current class so its KVO behavior also
        // survives; validate the GPUI ancestor, not just the reported class.
        let gpui_application =
            AnyClass::get(c"GPUIApplication").context("GPUI application class missing")?;
        let mut ancestor = Some(original_application);
        while ancestor.is_some_and(|class| class != gpui_application) {
            ancestor = ancestor.and_then(AnyClass::superclass);
        }
        ensure!(
            ancestor == Some(gpui_application),
            "termination gate requires the pinned GPUI application, found {}",
            original_application.name().to_string_lossy()
        );
        let original = delegate.class();
        ensure!(
            original.name() == c"GPUIApplicationDelegate",
            "termination gate requires the pinned GPUI delegate, found {}",
            original.name().to_string_lossy()
        );
        ensure!(
            original
                .instance_method(sel!(applicationShouldTerminate:))
                .is_none(),
            "GPUI now provides applicationShouldTerminate; review its native hook before installing the gate"
        );
        let subclass = if let Some(existing) = AnyClass::get(c"VMRustGPUIFlushTerminationDelegate")
        {
            ensure!(
                existing.superclass() == Some(original),
                "termination subclass has an unexpected superclass"
            );
            existing
        } else {
            let mut builder = ClassBuilder::new(c"VMRustGPUIFlushTerminationDelegate", original)
                .context("could not allocate the GPUI termination subclass")?;
            // NSUInteger return and the original NSObject receiver ABI match
            // NSApplicationDelegate.applicationShouldTerminate exactly.
            unsafe {
                builder.add_method(
                    sel!(applicationShouldTerminate:),
                    should_terminate as extern "C" fn(*mut AnyObject, Sel, *mut AnyObject) -> usize,
                );
            }
            builder.register()
        };
        ensure!(
            subclass.instance_size() == original.instance_size(),
            "termination subclass changed the delegate layout"
        );
        let application_subclass =
            if let Some(existing) = AnyClass::get(c"VMRustGPUITerminationApplication") {
                ensure!(
                    existing.superclass() == Some(original_application),
                    "termination application subclass has an unexpected superclass"
                );
                existing
            } else {
                let mut builder =
                    ClassBuilder::new(c"VMRustGPUITerminationApplication", original_application)
                        .context("could not allocate the GPUI termination application subclass")?;
                unsafe {
                    builder.add_method(
                        sel!(terminate:),
                        defer_terminate as extern "C" fn(*mut AnyObject, Sel, *mut AnyObject),
                    );
                }
                builder.register()
            };
        ensure!(
            application_subclass.instance_size() == original_application.instance_size(),
            "termination application subclass changed the application layout"
        );
        STATE.with_borrow_mut(|slot| {
            *slot = Some(State {
                installation,
                delegate: Retained::as_ptr(&delegate) as usize,
                application: Retained::as_ptr(&application) as usize,
                original_application,
                native_request_queued: false,
                commands,
                phase: Phase::Installed,
                pending_native: false,
                quit_published: false,
            })
        });
        // The private runtime GPUI class has no Rust ClassType, so define_class!
        // cannot name it as a superclass. This no-ivar dynamic subclass has the
        // exact same layout and affects only the retained original instance.
        let previous = unsafe { AnyObject::set_class(&delegate, subclass) };
        assert_eq!(previous, original, "GPUI delegate changed concurrently");
        let previous = unsafe { AnyObject::set_class(&application, application_subclass) };
        assert_eq!(
            previous, original_application,
            "GPUI application changed concurrently"
        );
        Ok(Self {
            installation,
            _main_thread: mtm,
            delegate,
            original,
            subclass,
            application,
            original_application,
            application_subclass,
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
        if self.delegate.class() == self.subclass {
            // objc2::AnyObject::set_class documents subclass installation only.
            // Restoring the exact original isa uses the runtime API directly;
            // both layouts/ivars are identical and this happens on the main
            // thread, with no method/ivar mutation or delegate replacement.
            unsafe {
                objc2::ffi::object_setClass(
                    Retained::as_ptr(&self.delegate).cast_mut(),
                    self.original,
                );
            }
        }
        if self.application.class() == self.application_subclass {
            unsafe {
                objc2::ffi::object_setClass(
                    Retained::as_ptr(&self.application).cast_mut(),
                    self.original_application,
                );
            }
        }
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
