// SPDX-License-Identifier: GPL-3.0-only
//! Real GPUI/AppKit termination acceptance in an isolated, windowless process.
//! No global hotkeys, pasteboard, activation, microphone, or user data access.
use anyhow::{Context, Result, ensure};
use block2::RcBlock;
use objc2::{
    MainThreadMarker, msg_send,
    rc::Retained,
    runtime::{AnyClass, AnyObject},
    sel,
};
use objc2_foundation::NSString;
use serde_json::json;
use std::{
    cell::RefCell,
    ffi::CStr,
    io::Write,
    path::PathBuf,
    rc::Rc,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use vocal_more_backend::{application::Options, config::ConfigRepository};
use vocal_more_desktop::{
    bridge::{BackendDriver, CommandSink, UiEvent},
    platform::TerminationGate,
};

const DELAY: Duration = Duration::from_millis(650);
fn class(name: &CStr) -> &'static AnyClass {
    AnyClass::get(name).expect("AppKit/Foundation class")
}
fn fail(error: impl std::fmt::Display) -> ! {
    eprintln!("FAIL native termination acceptance: {error}");
    std::process::exit(2)
}
fn check(result: Result<()>) {
    if let Err(error) = result {
        fail(format!("{error:#}"));
    }
}

struct Acceptance {
    driver: Arc<BackendDriver>,
    commands: CommandSink,
    gate: TerminationGate,
    path: PathBuf,
    start: Instant,
    pending: Option<Instant>,
    gpui_ticks: usize,
    cocoa_ticks: usize,
    in_app_update: bool,
    finishing: bool,
    observed_500ms: bool,
    worker_done: Arc<AtomicBool>,
    foreground_observed: Arc<AtomicBool>,
    host_quit: bool,
}
impl Acceptance {
    fn begin(&mut self) -> Result<()> {
        ensure!(self.pending.is_none(), "quit request was dispatched twice");
        ensure!(
            !self.host_quit || !self.gate.is_waiting(),
            "host quit unexpectedly began as a native request"
        );
        ensure!(
            !self.driver.finished(),
            "driver finished before delayed save began"
        );
        self.pending = Some(Instant::now());
        let driver = self.driver.clone();
        let done = self.worker_done.clone();
        let foreground_observed = self.foreground_observed.clone();
        let marker = self.path.join("durable-marker");
        std::thread::Builder::new()
            .name("termination-acceptance-delayed-save".into())
            .spawn(move || {
                // Simulate an external durable operation lasting beyond GPUI's
                // 200 ms shutdown limit, then shut down the real backend driver.
                std::thread::sleep(DELAY);
                // Timer intervals do not promise a callback rate on a loaded
                // runner. Keep storage pending until both foreground domains
                // have actually progressed; the independent watchdogs still
                // fail a domain that never runs.
                while !foreground_observed.load(Ordering::Acquire) {
                    std::thread::sleep(Duration::from_millis(2));
                }
                let saved = (|| -> Result<()> {
                    let mut file = std::fs::File::create(marker)?;
                    file.write_all(b"durable save completed\n")?;
                    file.sync_all()?;
                    driver.close();
                    done.store(true, Ordering::Release);
                    Ok(())
                })();
                if let Err(error) = saved {
                    fail(error);
                }
            })
            .context("could not start delayed durable save")?;
        Ok(())
    }
    fn poll(&mut self) -> Result<()> {
        let Some(pending) = self.pending else {
            return Ok(());
        };
        if self.finishing {
            return Ok(());
        }
        self.gpui_ticks += 1;
        if pending.elapsed() >= Duration::from_millis(500)
            && !self.worker_done.load(Ordering::Acquire)
        {
            ensure!(
                self.gate.is_waiting() != self.host_quit,
                "AppKit stopped waiting before storage finished"
            );
            ensure!(!self.driver.finished(), "driver prematurely finished");
            if self.cocoa_ticks >= 2 && self.gpui_ticks >= 2 {
                self.observed_500ms = true;
                self.foreground_observed.store(true, Ordering::Release);
            }
        }
        if self.worker_done.load(Ordering::Acquire) && self.driver.finished() {
            ensure!(
                self.observed_500ms,
                "could not observe a live app after 500 ms"
            );
            ensure!(
                pending.elapsed() >= DELAY,
                "termination bypassed the delayed durable operation"
            );
            ensure!(
                self.path.join("durable-marker").is_file(),
                "durable marker was lost"
            );
            let config = ConfigRepository::open(&self.path.join("config.yaml"))?;
            ensure!(
                config.config.get("ui.language") == "en",
                "accepted backend configuration edit was not saved"
            );
            self.finishing = true;
            self.in_app_update = true;
            self.gate.finish()?;
            self.gate.finish()?; // Idempotent while the native reply is queued.
            self.in_app_update = false;
            println!(
                "finish queued from GPUI App update after {} ms; GPUI={}, Cocoa={}",
                pending.elapsed().as_millis(),
                self.gpui_ticks,
                self.cocoa_ticks
            );
        }
        Ok(())
    }
    fn verify_quit(&mut self) -> Result<()> {
        ensure!(
            !self.in_app_update,
            "AppKit termination reentered the borrowed GPUI App"
        );
        ensure!(
            self.finishing && self.driver.finished() && self.worker_done.load(Ordering::Acquire),
            "AppKit exited before storage finished"
        );
        let elapsed = self
            .pending
            .context("quit without pending request")?
            .elapsed();
        ensure!(
            elapsed >= DELAY && self.observed_500ms,
            "quit did not remain deferred for at least 500 ms"
        );
        ensure!(
            self.cocoa_ticks >= 2 && self.gpui_ticks >= 2,
            "run loop callbacks did not remain live: GPUI={}, Cocoa={}, elapsed={elapsed:?}",
            self.gpui_ticks,
            self.cocoa_ticks
        );
        println!(
            "PASS native termination acceptance {}",
            json!({
                "path":if self.host_quit {"host_finish"} else {"NSTerminateLater"},
                "delay_ms":elapsed.as_millis(),"gpui_callbacks":self.gpui_ticks,"cocoa_callbacks":self.cocoa_ticks,
                "durable_marker":true,"backend_finished":true,"saved_config":true,"deferred_from_app_update":true,
                "original_gpui_will_terminate":true
            })
        );
        std::fs::remove_dir_all(&self.path)?;
        Ok(())
    }
}

fn install(mtm: MainThreadMarker, commands: CommandSink) -> Result<TerminationGate> {
    let app: Retained<AnyObject> = unsafe { msg_send![class(c"NSApplication"), sharedApplication] };
    let delegate: Retained<AnyObject> = unsafe { msg_send![&*app, delegate] };
    let original = delegate.class();
    let original_application = app.class();
    let application_methods: Vec<_> = original_application
        .instance_methods()
        .iter()
        .map(|method| (method.name(), method.implementation() as usize))
        .collect();
    let original_methods: Vec<_> = original
        .instance_methods()
        .iter()
        .map(|method| (method.name(), method.implementation() as usize))
        .collect();
    let original_platform: *mut std::ffi::c_void = unsafe {
        *original
            .instance_variable(c"platform")
            .context("GPUI platform ivar missing")?
            .load(&delegate)
    };
    let gate = TerminationGate::install(mtm, commands.clone())?;
    // Instances keep their (possibly KVO) classes: swapping NSApp's isa under
    // a KVO subclass corrupts KVO and crashes on activation in macOS 27.
    ensure!(
        delegate.class() == original,
        "termination gate changed the GPUI delegate class"
    );
    ensure!(
        app.class() == original_application,
        "termination gate changed the GPUI/KVO application class"
    );
    ensure!(
        original
            .instance_method(sel!(applicationShouldTerminate:))
            .is_some()
            && original_application
                .instance_method(sel!(terminate:))
                .is_some(),
        "termination hooks are not reachable from the GPUI instances"
    );
    for (selector, imp) in application_methods {
        ensure!(
            app.class()
                .instance_method(selector)
                .is_some_and(|method| method.implementation() as usize == imp),
            "original application selector {selector:?} was replaced"
        );
    }
    let application_platform: *mut std::ffi::c_void = unsafe {
        *app.class()
            .instance_variable(c"platform")
            .unwrap()
            .load(&app)
    };
    ensure!(
        application_platform == original_platform,
        "GPUI application platform ivar changed"
    );
    for (selector, imp) in original_methods {
        ensure!(
            delegate
                .class()
                .instance_method(selector)
                .is_some_and(|method| method.implementation() as usize == imp),
            "original GPUI selector {selector:?} was replaced"
        );
    }
    let platform: *mut std::ffi::c_void = unsafe {
        *delegate
            .class()
            .instance_variable(c"platform")
            .unwrap()
            .load(&delegate)
    };
    ensure!(platform == original_platform, "GPUI platform ivar changed");
    drop(gate);
    ensure!(
        delegate.class() == original && app.class() == original_application,
        "dropping the gate changed the GPUI instance classes"
    );
    // Without a gate the delegate hook answers NSTerminateNow, like AppKit.
    let reply: usize = unsafe { msg_send![&*delegate,applicationShouldTerminate:&*app] };
    ensure!(reply == 1, "inactive termination hook did not answer Now");
    TerminationGate::install(mtm, commands)
}

fn launch(cx: &mut gpui_kit::App, host_quit: bool, slow_cocoa: bool) -> Result<()> {
    let mtm = MainThreadMarker::new().context("harness must run on main thread")?;
    let app: Retained<AnyObject> = unsafe { msg_send![class(c"NSApplication"), sharedApplication] };
    unsafe {
        let _: bool = msg_send![&*app,setActivationPolicy:1isize];
    }
    let stamp = SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos();
    let path = std::env::temp_dir().join(format!(
        "vocal-more-termination-{}-{stamp}",
        std::process::id()
    ));
    std::fs::create_dir(&path)?;
    let (driver, commands, events) = BackendDriver::start(Options::new(path.clone()))?;
    let gate = install(mtm, commands.clone())?;
    commands.request_checked("set_config", json!({"key":"ui.language","value":"en"}))?;
    let test = Rc::new(RefCell::new(Acceptance {
        driver: Arc::new(driver),
        commands,
        gate,
        path,
        start: Instant::now(),
        pending: None,
        gpui_ticks: 0,
        cocoa_ticks: 0,
        in_app_update: false,
        finishing: false,
        observed_500ms: false,
        worker_done: Arc::new(AtomicBool::new(false)),
        foreground_observed: Arc::new(AtomicBool::new(false)),
        host_quit,
    }));
    let on_quit = test.clone();
    cx.on_app_quit(move |_| {
        check(on_quit.borrow_mut().verify_quit());
        async {}
    })
    .detach();
    let native_test = test.clone();
    let timer_block = RcBlock::new(move |_timer: *mut AnyObject| {
        let mut state = native_test.borrow_mut();
        if state.start.elapsed() > Duration::from_secs(6) {
            fail(format!(
                "Cocoa watchdog: termination did not complete; pending={:?}, GPUI={}, Cocoa={}, worker_done={}, gate_waiting={}",
                state.pending.map(|pending| pending.elapsed()),
                state.gpui_ticks,
                state.cocoa_ticks,
                state.worker_done.load(Ordering::Acquire),
                state.gate.is_waiting()
            ));
        }
        if state.pending.is_some() && !state.finishing {
            state.cocoa_ticks += 1;
        }
    });
    unsafe {
        // Exercise coalesced native callbacks without relaxing the watchdog.
        let interval = if slow_cocoa { 0.4f64 } else { 0.025f64 };
        let timer: Retained<AnyObject> = msg_send![class(c"NSTimer"),timerWithTimeInterval:interval,repeats:true,block:&*timer_block];
        let run_loop: Retained<AnyObject> = msg_send![class(c"NSRunLoop"), mainRunLoop];
        let mode = NSString::from_str("kCFRunLoopCommonModes");
        let _: () = msg_send![&*run_loop,addTimer:&*timer,forMode:&*mode];
    }
    cx.spawn(async move |cx| {
        let mut triggered = false;
        loop {
            cx.background_executor()
                .timer(Duration::from_millis(25))
                .await;
            cx.update(|cx| {
                let mut state = test.borrow_mut();
                while let Ok(event) = events.try_recv() {
                    if let UiEvent::Request(request) = event {
                        if request.method == "platform_quit" {
                            continue;
                        }
                        state.driver.send(request);
                    }
                }
                if !triggered && state.start.elapsed() >= Duration::from_millis(150) {
                    triggered = true;
                    if state.host_quit {
                        check(state.begin());
                    } else {
                        cx.quit();
                    }
                }
                if state.commands.take_quit_request() {
                    check(state.begin());
                }
                check(state.poll());
            });
        }
    })
    .detach();
    Ok(())
}
fn main() {
    // A background watchdog cannot be blocked by AppKit/GPUI run-loop stalls.
    std::thread::spawn(|| {
        std::thread::sleep(Duration::from_secs(8));
        fail("process watchdog: foreground execution stalled");
    });
    let host_quit = std::env::args().any(|arg| arg == "--host-quit");
    let slow_cocoa = std::env::args().any(|arg| arg == "--slow-cocoa");
    gpui_kit::application().run(move |cx| {
        if let Err(error) = launch(cx, host_quit, slow_cocoa) {
            fail(format!("{error:#}"));
        }
    });
    fail("GPUI run returned without the native termination callback");
}
