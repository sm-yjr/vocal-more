// SPDX-License-Identifier: GPL-3.0-only
//! Isolated, real Cocoa acceptance. Never types into another application's UI.
//! Run the dedicated binary from a trusted macOS terminal; it needs AX access.
use anyhow::{Context, Result, bail, ensure};
use async_channel::Receiver;
use base64::{Engine, engine::general_purpose::STANDARD};
use objc2::{
    MainThreadMarker, msg_send,
    rc::{Allocated, Retained},
    runtime::{AnyClass, AnyObject},
};
use objc2_foundation::{NSPoint, NSRange, NSRect, NSSize, NSString};
use serde_json::{Value, json};
use std::{
    ffi::CStr,
    path::{Path, PathBuf},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use vocal_more_backend::application::Options;
use vocal_more_desktop::{
    bridge::{BackendDriver, UiEvent},
    platform::{self, Platform},
};

fn class(name: &CStr) -> &'static AnyClass {
    AnyClass::get(name).expect("native framework class")
}
fn ns(text: &str) -> Retained<NSString> {
    NSString::from_str(text)
}
fn count(board: &AnyObject) -> isize {
    unsafe { msg_send![board, changeCount] }
}
fn field_text(field: &AnyObject) -> String {
    unsafe {
        let text: Retained<NSString> = msg_send![field, string];
        text.to_string()
    }
}
fn set_text(field: &AnyObject, text: &str) {
    unsafe {
        let _: () = msg_send![field,setString:&*ns(text)];
        let _: () = msg_send![field,setSelectedRange:NSRange{location:text.encode_utf16().count(),length:0}];
    }
}
fn pump(platform: &mut Platform, time: Duration) {
    let until = Instant::now() + time;
    while Instant::now() < until {
        unsafe {
            // NSRunLoop alone services timers/AX but does not dispatch queued
            // keyboard events to NSTextView. This is the normal NSApplication
            // event path, restricted to this process's own event queue.
            let app: Retained<AnyObject> = msg_send![class(c"NSApplication"), sharedApplication];
            let run_loop: Retained<AnyObject> = msg_send![class(c"NSRunLoop"), mainRunLoop];
            let date: Retained<AnyObject> =
                msg_send![class(c"NSDate"),dateWithTimeIntervalSinceNow:0.01f64];
            let _: () = msg_send![&*run_loop,runUntilDate:&*date];
            let now: Retained<AnyObject> = msg_send![class(c"NSDate"), date];
            loop {
                let event: Option<Retained<AnyObject>> = msg_send![&*app,nextEventMatchingMask:usize::MAX,untilDate:&*now,inMode:&*ns("kCFRunLoopDefaultMode"),dequeue:true];
                let Some(event) = event else {
                    break;
                };
                let _: () = msg_send![&*app,sendEvent:&*event];
            }
            let _: () = msg_send![&*app, updateWindows];
        }
        if let Some(result) = platform.poll_paste() {
            result.expect("native paste delivery");
        }
        platform.tick();
        std::thread::sleep(Duration::from_millis(2));
    }
}
fn native_events(events: &Receiver<UiEvent>) -> Vec<(String, Value)> {
    let mut found = Vec::new();
    while let Ok(event) = events.try_recv() {
        if let UiEvent::Request(request) = event {
            if request.method == "platform_event" {
                found.push((
                    request.params["method"].as_str().unwrap_or_default().into(),
                    request.params["params"].clone(),
                ));
            } else if request.method == "poll_observation" {
                found.push((request.method, request.params));
            }
        }
    }
    found
}
fn wait_event(platform: &mut Platform, events: &Receiver<UiEvent>, method: &str) -> Result<Value> {
    let deadline = Instant::now() + Duration::from_secs(4);
    loop {
        for (name, params) in native_events(events) {
            if name == method {
                return Ok(params);
            }
        }
        ensure!(
            Instant::now() < deadline,
            "native event did not arrive: {method}"
        );
        pump(platform, Duration::from_millis(20));
    }
}

struct ClipboardGuard {
    board: Retained<AnyObject>,
    original: Retained<AnyObject>,
    expected: isize,
}
impl ClipboardGuard {
    fn new() -> Self {
        unsafe {
            let board: Retained<AnyObject> = msg_send![class(c"NSPasteboard"), generalPasteboard];
            let items: Option<Retained<AnyObject>> = msg_send![&*board, pasteboardItems];
            let mut copies = Vec::<Retained<AnyObject>>::new();
            if let Some(items) = items {
                let length: usize = msg_send![&*items, count];
                for index in 0..length {
                    let item: Retained<AnyObject> = msg_send![&*items,objectAtIndex:index];
                    let types: Retained<AnyObject> = msg_send![&*item, types];
                    let allocated: Allocated<AnyObject> =
                        msg_send![class(c"NSPasteboardItem"), alloc];
                    let copy: Retained<AnyObject> = msg_send![allocated, init];
                    let count: usize = msg_send![&*types, count];
                    for index in 0..count {
                        let kind: Retained<NSString> = msg_send![&*types,objectAtIndex:index];
                        let data: Option<Retained<AnyObject>> =
                            msg_send![&*item,dataForType:&*kind];
                        if let Some(data) = data {
                            let _: bool = msg_send![&*copy,setData:&*data,forType:&*kind];
                        }
                    }
                    copies.push(copy);
                }
            }
            let objects: Vec<*const AnyObject> = copies.iter().map(Retained::as_ptr).collect();
            let original: Retained<AnyObject> =
                msg_send![class(c"NSArray"),arrayWithObjects:objects.as_ptr(),count:objects.len()];
            let expected = count(&board);
            Self {
                board,
                original,
                expected,
            }
        }
    }
    fn owned_change(&mut self) {
        self.expected = count(&self.board);
    }
    fn text(&self) -> Option<String> {
        unsafe {
            let text: Option<Retained<NSString>> =
                msg_send![&*self.board,stringForType:&*ns("public.utf8-plain-text")];
            text.map(|v| v.to_string())
        }
    }
}
impl Drop for ClipboardGuard {
    fn drop(&mut self) {
        unsafe {
            if count(&self.board) != self.expected {
                return;
            }
            let _: isize = msg_send![&*self.board, clearContents];
            let _: bool = msg_send![&*self.board,writeObjects:&*self.original];
        }
    }
}

struct TestWindow {
    window: Retained<AnyObject>,
    first: Retained<AnyObject>,
    second: Retained<AnyObject>,
    previous_app: Option<Retained<AnyObject>>,
}
impl TestWindow {
    fn new() -> Self {
        unsafe {
            let workspace: Retained<AnyObject> = msg_send![class(c"NSWorkspace"), sharedWorkspace];
            let previous_app: Option<Retained<AnyObject>> =
                msg_send![&*workspace, frontmostApplication];
            let allocated: Allocated<AnyObject> = msg_send![class(c"NSWindow"), alloc];
            let window: Retained<AnyObject> = msg_send![allocated,initWithContentRect:NSRect::new(NSPoint::new(100.,100.),NSSize::new(520.,280.)),styleMask:3usize,backing:2usize,defer:false];
            let _: () = msg_send![&*window,setReleasedWhenClosed:false];
            let _: () = msg_send![&*window,setTitle:&*ns("Vocal More isolated native acceptance")];
            let view: Retained<AnyObject> = msg_send![&*window, contentView];
            let make = |y: f64| -> Retained<AnyObject> {
                let allocated: Allocated<AnyObject> = msg_send![class(c"NSTextView"), alloc];
                let field: Retained<AnyObject> = msg_send![allocated,initWithFrame:NSRect::new(NSPoint::new(12.,y),NSSize::new(496.,112.))];
                let _: () = msg_send![&*field,setEditable:true];
                let _: () = msg_send![&*view,addSubview:&*field];
                field
            };
            let first = make(148.);
            let second = make(12.);
            let app: Retained<AnyObject> = msg_send![class(c"NSApplication"), sharedApplication];
            let _: () = msg_send![&*app,activateIgnoringOtherApps:true];
            let _: () = msg_send![&*window,makeKeyAndOrderFront:std::ptr::null::<AnyObject>()];
            let _: bool = msg_send![&*window,makeFirstResponder:&*first];
            Self {
                window,
                first,
                second,
                previous_app,
            }
        }
    }
    fn focus(&self, field: &AnyObject, platform: &mut Platform) -> Result<()> {
        unsafe {
            let app: Retained<AnyObject> = msg_send![class(c"NSApplication"), sharedApplication];
            let _: () = msg_send![&*app,activateIgnoringOtherApps:true];
            let _: () = msg_send![&*self.window,makeKeyAndOrderFront:std::ptr::null::<AnyObject>()];
            let ok: bool = msg_send![&*self.window,makeFirstResponder:field];
            ensure!(ok, "could not focus the test field");
        }
        // Activation is asynchronous in recent macOS versions/Stage Manager.
        // Wait for key ownership, never remove this guard to force an injection.
        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            let key: bool = unsafe { msg_send![&*self.window, isKeyWindow] };
            let activation = platform.activation_status();
            if key
                && activation["active"] == true
                && activation["foreground_pid"] == std::process::id()
            {
                break;
            }
            ensure!(
                Instant::now() < deadline,
                "refusing keyboard injection because the isolated window is not key; activation {}",
                platform.activation_status()
            );
            pump(platform, Duration::from_millis(20));
        }
        Ok(())
    }
}
impl Drop for TestWindow {
    fn drop(&mut self) {
        unsafe {
            let _: () = msg_send![&*self.window, close];
            if let Some(app) = &self.previous_app {
                let _: bool = msg_send![&**app,activateWithOptions:2usize];
            }
        }
    }
}
struct Scratch(PathBuf);
impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn wav_silence(path: &Path) -> Result<Vec<u8>> {
    let bytes = 6400u32;
    let mut wav = Vec::new();
    wav.extend(b"RIFF");
    wav.extend((36 + bytes).to_le_bytes());
    wav.extend(b"WAVEfmt ");
    wav.extend(16u32.to_le_bytes());
    wav.extend(1u16.to_le_bytes());
    wav.extend(1u16.to_le_bytes());
    wav.extend(16000u32.to_le_bytes());
    wav.extend(32000u32.to_le_bytes());
    wav.extend(2u16.to_le_bytes());
    wav.extend(16u16.to_le_bytes());
    wav.extend(b"data");
    wav.extend(bytes.to_le_bytes());
    wav.resize(44 + bytes as usize, 0);
    std::fs::write(path, &wav)?;
    Ok(wav)
}
fn verify_playback(
    platform: &mut Platform,
    events: &Receiver<UiEvent>,
    directory: &Path,
) -> Result<()> {
    native_events(events);
    let wav = wav_silence(&directory.join("silent.wav"))?;
    platform.play("native-history", &directory.join("silent.wav"))?;
    let ended = wait_event(platform, events, "recordingPlaybackEnded")?;
    ensure!(
        ended["id"] == "native-history",
        "history end event lost its recording ID"
    );
    println!("PASS real AVPlayer WAV playback ends without a manual stop");
    platform.play_preview(&STANDARD.encode(&wav))?;
    wait_event(platform, events, "micTestPlaybackEnded")?;
    println!("PASS in-memory AVAudioPlayer microphone preview ends");
    Ok(())
}
fn run_playback_only() -> Result<()> {
    let mtm = MainThreadMarker::new().context("native acceptance must run on the main thread")?;
    let directory = std::env::temp_dir().join(format!(
        "vocal-more-playback-acceptance-{}-{}",
        std::process::id(),
        SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos()
    ));
    std::fs::create_dir(&directory)?;
    let scratch = Scratch(directory.clone());
    let (driver, sink, events) = BackendDriver::start(Options::new(directory.join("backend")))?;
    let config = json!({"ui":{"language":"en"},"hotkey":{"active_hotkeys":["fn"]}});
    let mut platform = Platform::new(mtm, sink, &config, true)?;
    verify_playback(&mut platform, &events, &directory)?;
    ensure!(
        platform.status()["hotkeys"]["fn_suppressed"] == false,
        "no-hotkeys playback acceptance altered Fn suppression"
    );
    platform.close();
    driver.close();
    let deadline = Instant::now() + Duration::from_secs(5);
    while !driver.finished() && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
    ensure!(driver.finished(), "backend driver did not terminate");
    drop(driver);
    drop(scratch);
    println!("PASS isolated silent playback; no clipboard or application focus changes");
    Ok(())
}
fn run() -> Result<()> {
    let mtm = MainThreadMarker::new().context("native acceptance must run on the main thread")?;
    unsafe {
        let app: Retained<AnyObject> = msg_send![class(c"NSApplication"), sharedApplication];
        let _: bool = msg_send![&*app,setActivationPolicy:1isize];
        // NSTextView receives Command shortcuts through the Cocoa responder
        // chain and an Edit menu, as it does in a normal native application.
        let allocated: Allocated<AnyObject> = msg_send![class(c"NSMenu"), alloc];
        let main: Retained<AnyObject> =
            msg_send![allocated,initWithTitle:&*ns("Native Acceptance")];
        let allocated: Allocated<AnyObject> = msg_send![class(c"NSMenu"), alloc];
        let edit: Retained<AnyObject> = msg_send![allocated,initWithTitle:&*ns("Edit")];
        let allocated: Allocated<AnyObject> = msg_send![class(c"NSMenuItem"), alloc];
        let item: Retained<AnyObject> = msg_send![allocated,initWithTitle:&*ns("Edit"),action:None::<objc2::runtime::Sel>,keyEquivalent:&*ns("")];
        let _: () = msg_send![&*item,setSubmenu:&*edit];
        let _: () = msg_send![&*main,addItem:&*item];
        let allocated: Allocated<AnyObject> = msg_send![class(c"NSMenuItem"), alloc];
        let paste: Retained<AnyObject> = msg_send![allocated,initWithTitle:&*ns("Paste"),action:Some(objc2::sel!(paste:)),keyEquivalent:&*ns("v")];
        let _: () = msg_send![&*edit,addItem:&*paste];
        let _: () = msg_send![&*app,setMainMenu:&*main];
    }
    let directory = std::env::temp_dir().join(format!(
        "vocal-more-native-acceptance-{}-{}",
        std::process::id(),
        SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos()
    ));
    std::fs::create_dir(&directory)?;
    let scratch = Scratch(directory.clone());
    let (driver, sink, events) = BackendDriver::start(Options::new(directory.join("backend")))?;
    sink.update_session("idle", 0);
    let config = json!({"ui":{"language":"en"},"hotkey":{"active_hotkeys":["fn"],"custom_keys":[]},"update_channel":null});
    let mut platform = Platform::new(mtm, sink.clone(), &config, true)?;
    ensure!(
        platform.status()["accessibility"] == true,
        "Accessibility is unavailable for this test process; no keyboard events were injected"
    );
    let mut clipboard = ClipboardGuard::new();
    let window = TestWindow::new();
    pump(&mut platform, Duration::from_millis(100));
    set_text(&window.first, "");
    window.focus(&window.first, &mut platform)?;
    platform.copy_text("native-acceptance-original")?;
    clipboard.owned_change();
    ensure!(
        platform.paste_guarded("你好 Rust 🦀\n第二行", true, true, sink.paste_epoch(), 0)?,
        "native paste was rejected"
    );
    clipboard.owned_change();
    pump(&mut platform, Duration::from_millis(200));
    ensure!(
        field_text(&window.first) == "你好 Rust 🦀\n第二行",
        "Unicode native paste did not reach the isolated NSTextView"
    );
    pump(&mut platform, Duration::from_millis(500));
    ensure!(
        clipboard.text().as_deref() == Some("native-acceptance-original"),
        "clipboard was not restored after native paste"
    );
    // Only adopt a count produced by the expected restoration. On a failed
    // restore, keep the previous owned count so Drop preserves a later writer.
    clipboard.owned_change();
    println!("PASS Unicode native paste + 600 ms clipboard restore");
    window.focus(&window.first, &mut platform)?;
    ensure!(
        platform.paste_guarded(" compatibility", true, false, sink.paste_epoch(), 0)?,
        "compatibility paste rejected"
    );
    clipboard.owned_change();
    pump(&mut platform, Duration::from_millis(180));
    ensure!(
        field_text(&window.first).ends_with(" compatibility"),
        "compatibility Command down/V/up did not paste"
    );
    platform.copy_text("external-owner-write")?;
    clipboard.owned_change();
    pump(&mut platform, Duration::from_millis(600));
    ensure!(
        clipboard.text().as_deref() == Some("external-owner-write"),
        "restore overwrote a later clipboard write"
    );
    println!("PASS compatibility paste + external clipboard owner protection");
    let epoch = sink.paste_epoch();
    let before = field_text(&window.first);
    sink.request("cancel", json!({}));
    ensure!(
        !platform.paste_guarded("must never appear", false, true, epoch, 0)?,
        "cancelled epoch was accepted"
    );
    pump(&mut platform, Duration::from_millis(50));
    ensure!(
        field_text(&window.first) == before,
        "cancelled text reached the test field"
    );
    sink.request("start", json!({}));
    println!("PASS cancellation invalidates final native injection");
    set_text(&window.first, "a🦀中b");
    window.focus(&window.first, &mut platform)?;
    unsafe {
        let _: () = msg_send![&*window.first,setSelectedRange:NSRange{location:1,length:2}];
    }
    platform.capture_focused(json!("before"))?;
    let before =
        wait_event(&mut platform, &events, "platform_focused_snapshot")?["snapshot"].clone();
    ensure!(
        before["pid"] == std::process::id() && before["value"] == "a🦀中b",
        "AX snapshot did not read the isolated field"
    );
    ensure!(
        before["selection_start"] == 1 && before["selection_length"] == 1,
        "AX selection was not converted from UTF-16"
    );
    platform.retain_observation("native-observation", &before)?;
    set_text(&window.first, "a🦀中b edited");
    set_text(&window.second, "other field");
    window.focus(&window.second, &mut platform)?;
    platform.observe("native-observation")?;
    let observed = wait_event(&mut platform, &events, "poll_observation")?;
    ensure!(
        observed["focused"]["target_id"] != before["target_id"],
        "AX focus did not move to the second field"
    );
    ensure!(
        observed["retained"]["target_id"] == before["target_id"]
            && observed["retained"]["value"] == "a🦀中b edited",
        "AX retained read followed focus or lost the exact original object"
    );
    platform.end_observation("native-observation")?;
    println!("PASS exact AX target retention across focus changes + Unicode selection");
    verify_playback(&mut platform, &events, &directory)?;
    match platform::capture_screen(false) {
        Ok(bytes) => {
            ensure!(
                bytes.starts_with(&[0xff, 0xd8, 0xff]) && bytes.len() <= platform::MAX_JPEG_BYTES,
                "screen frame violates JPEG/190 KiB contract"
            );
            println!("PASS real main-display JPEG {} bytes", bytes.len());
        }
        Err(error) => bail!("screen capture permission/runtime verification failed: {error}"),
    }
    ensure!(
        platform.status()["hotkeys"]["fn_suppressed"] == false,
        "no-hotkeys acceptance altered Fn suppression"
    );
    platform.close();
    driver.close();
    let deadline = Instant::now() + Duration::from_secs(5);
    while !driver.finished() && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
    ensure!(driver.finished(), "backend driver did not terminate");
    drop(window);
    drop(clipboard);
    drop(driver);
    drop(scratch);
    println!(
        "PASS native platform acceptance; original clipboard and foreground application restored"
    );
    Ok(())
}
fn main() {
    // Run a real Cocoa application loop. finishLaunching + NSRunLoop alone
    // leaves NSApplication.isRunning false and activation may be ignored.
    let result = std::rc::Rc::new(std::cell::RefCell::new(None));
    let completed = result.clone();
    let playback_only = std::env::args().any(|arg| arg == "--playback-only");
    unsafe {
        let app: Retained<AnyObject> = msg_send![class(c"NSApplication"), sharedApplication];
        let _: bool = msg_send![&*app,setActivationPolicy:1isize];
        let callback = block2::RcBlock::new(move |_timer: *mut AnyObject| {
            *completed.borrow_mut() = Some(if playback_only {
                run_playback_only()
            } else {
                run()
            });
            let app: Retained<AnyObject> = msg_send![class(c"NSApplication"), sharedApplication];
            let _: () = msg_send![&*app,stop:std::ptr::null::<AnyObject>()];
            let event: Option<Retained<AnyObject>> = msg_send![class(c"NSEvent"),otherEventWithType:15usize,location:NSPoint::new(0.,0.),modifierFlags:0usize,timestamp:0f64,windowNumber:0isize,context:std::ptr::null::<AnyObject>(),subtype:0i16,data1:0isize,data2:0isize];
            if let Some(event) = event {
                let _: () = msg_send![&*app,postEvent:&*event,atStart:true];
            }
        });
        let _timer: Retained<AnyObject> = msg_send![class(c"NSTimer"),scheduledTimerWithTimeInterval:0.1f64,repeats:false,block:&*callback];
        let _: () = msg_send![&*app, run];
    }
    let outcome = result
        .borrow_mut()
        .take()
        .unwrap_or_else(|| Err(anyhow::anyhow!("Cocoa loop exited before acceptance ran")));
    if let Err(error) = outcome {
        eprintln!("Native platform acceptance failed: {error:#}");
        std::process::exit(1);
    }
}
