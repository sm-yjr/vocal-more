// SPDX-License-Identifier: GPL-3.0-only
//! One owned worker retains the exact AX object across focus changes.
use super::{class, ffi, ns};
use crate::bridge::CommandSink;
use anyhow::{Result, bail};
use objc2::{msg_send, rc::autoreleasepool, runtime::AnyObject};
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    ptr,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
        mpsc::{self, SyncSender},
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

/// Optional correction learning must not dominate result delivery. The budget
/// includes time queued behind an earlier AX operation, not just each IPC call.
pub const AX_CAPTURE_BUDGET: Duration = Duration::from_millis(150);
fn remaining_timeout(deadline: Instant, now: Instant) -> Option<f32> {
    let remaining = deadline.checked_duration_since(now)?;
    (!remaining.is_zero()).then(|| remaining.min(Duration::from_millis(40)).as_secs_f32())
}
struct Snapshot {
    element: ffi::Owned,
    value: Value,
}
fn attribute(element: ffi::Ref, name: &str, deadline: Instant) -> Option<ffi::Owned> {
    let timeout = remaining_timeout(deadline, Instant::now())?;
    unsafe {
        ffi::AXUIElementSetMessagingTimeout(element, timeout);
    }
    let mut out = ptr::null();
    let error = unsafe {
        ffi::AXUIElementCopyAttributeValue(
            element,
            (&*ns(name) as *const objc2_foundation::NSString).cast(),
            &mut out,
        )
    };
    let value = unsafe { ffi::Owned::from_create(out) };
    if error == 0 && Instant::now() < deadline {
        value
    } else {
        None
    }
}
fn text_attribute(element: ffi::Ref, name: &str, deadline: Instant) -> Option<String> {
    attribute(element, name, deadline).and_then(|v| ffi::cf_string(v.as_ptr()))
}
fn snapshot(element: ffi::Owned, expected: Option<&Value>, deadline: Instant) -> Option<Snapshot> {
    remaining_timeout(deadline, Instant::now())?;
    let mut pid = 0;
    if unsafe { ffi::AXUIElementGetPid(element.as_ptr(), &mut pid) } != 0 {
        return None;
    }
    let role = text_attribute(element.as_ptr(), "AXRole", deadline)?;
    if !matches!(
        role.as_str(),
        "AXTextField" | "AXTextArea" | "AXComboBox" | "AXSearchField"
    ) {
        return None;
    }
    // A timed-out subrole cannot prove this is a non-secure field. Optional
    // learning fails closed instead of reading AXValue under that uncertainty.
    let subrole = text_attribute(element.as_ptr(), "AXSubrole", deadline)?;
    let secure_marker = format!("{role} {subrole}").to_lowercase();
    let secure = secure_marker.contains("secure") || secure_marker.contains("password");
    let identifier = text_attribute(element.as_ptr(), "AXIdentifier", deadline).unwrap_or_default();
    let target_id = format!("{pid}:{identifier}:{}", unsafe {
        ffi::CFHash(element.as_ptr())
    });
    if expected.is_some_and(|v| v["target_id"] != target_id || v["pid"] != pid) {
        return None;
    }
    // Do not request AXValue or selected text from a secure field, including
    // retained elements whose subrole changed during the observation window.
    let value = if secure {
        String::new()
    } else {
        text_attribute(element.as_ptr(), "AXValue", deadline)?
    };
    if value.chars().take(8001).count() > 8000 || Instant::now() >= deadline {
        return None;
    }
    let range = if secure {
        None
    } else {
        attribute(element.as_ptr(), "AXSelectedTextRange", deadline).and_then(|v| {
            let mut range = ffi::Range::default();
            if unsafe {
                ffi::AXValueGetType(v.as_ptr()) == 4
                    && ffi::AXValueGetValue(v.as_ptr(), 4, (&mut range as *mut ffi::Range).cast())
            } {
                utf16_range(&value, range.location, range.length)
            } else {
                None
            }
        })
    };
    let (bundle, name) = if let Some(expected) = expected {
        (
            expected["app_bundle_id"]
                .as_str()
                .unwrap_or_default()
                .to_owned(),
            expected["app_name"].as_str().unwrap_or_default().to_owned(),
        )
    } else {
        unsafe {
            let app: Option<objc2::rc::Retained<AnyObject>> = msg_send![class(c"NSRunningApplication"),runningApplicationWithProcessIdentifier:pid];
            app.map(|app| {
                let bundle: Option<objc2::rc::Retained<objc2_foundation::NSString>> =
                    msg_send![&*app, bundleIdentifier];
                let name: Option<objc2::rc::Retained<objc2_foundation::NSString>> =
                    msg_send![&*app, localizedName];
                (
                    bundle.map(|v| v.to_string()).unwrap_or_default(),
                    name.map(|v| v.to_string()).unwrap_or_default(),
                )
            })
            .unwrap_or_default()
        }
    };
    if Instant::now() >= deadline {
        return None;
    }
    Some(Snapshot {
        element,
        value: json!({"target_id":target_id,"pid":pid,"value":value,"role":role,"subrole":subrole,
        "app_bundle_id":bundle,"app_name":name,"is_secure":secure,
        "selection_start":range.map(|v|v.0),"selection_length":range.map(|v|v.1)}),
    })
}
fn focused(deadline: Instant) -> Option<Snapshot> {
    if !unsafe { ffi::AXIsProcessTrusted() } {
        return None;
    }
    let system = unsafe { ffi::Owned::from_create(ffi::AXUIElementCreateSystemWide()) }?;
    snapshot(
        attribute(system.as_ptr(), "AXFocusedUIElement", deadline)?,
        None,
        deadline,
    )
}
pub fn utf16_range(text: &str, start: isize, length: isize) -> Option<(usize, usize)> {
    let start = usize::try_from(start).ok()?;
    let end = start.checked_add(usize::try_from(length).ok()?)?;
    let mut units = 0;
    let mut chars = 0;
    let mut first = None;
    let mut last = None;
    for character in text.chars() {
        if units == start {
            first = Some(chars);
        }
        if units == end {
            last = Some(chars);
        }
        units += character.len_utf16();
        chars += 1;
    }
    if units == start {
        first = Some(chars);
    }
    if units == end {
        last = Some(chars);
    }
    Some((first?, last?.checked_sub(first?)?))
}

enum Command {
    Capture(Value, Instant),
    Retain(String, Value),
    Poll(String),
    End(String),
}
pub struct AccessibilityWorker {
    sender: Option<SyncSender<Command>>,
    closed: Arc<AtomicBool>,
    worker: Option<JoinHandle<()>>,
}
impl AccessibilityWorker {
    pub fn new(commands: CommandSink) -> Self {
        let (sender, receiver) = mpsc::sync_channel(16);
        let closed = Arc::new(AtomicBool::new(false));
        let stopping = closed.clone();
        let worker=thread::Builder::new().name("vocal-more-accessibility".into()).spawn(move || {
            let mut pending:HashMap<String,Snapshot>=HashMap::new(); let mut retained:HashMap<String,Snapshot>=HashMap::new();
            while let Ok(command)=receiver.recv() {
                if stopping.load(Ordering::Acquire) {break;}
                autoreleasepool(|_|match command {
                    Command::Capture(request_id,deadline)=>{
                        let _span=vocal_more_core::diagnostics::Span::new(vocal_more_core::diagnostics::Stage::AxCapture);
                        let captured=focused(deadline);let value=captured.as_ref().map(|s|s.value.clone()).unwrap_or(Value::Null);
                        if let Some(captured)=captured {pending.clear();pending.insert(captured.value["target_id"].as_str().unwrap_or_default().into(),captured);}
                        if !stopping.load(Ordering::Acquire) {commands.request("platform_event",json!({"method":"platform_focused_snapshot","params":{"request_id":request_id,"snapshot":value}}));}
                    },
                    Command::Retain(id,value)=>{if let Some(snapshot)=pending.remove(value["target_id"].as_str().unwrap_or_default()) && snapshot.value["pid"]==value["pid"] {retained.insert(id,snapshot);}},
                    Command::Poll(id)=>{
                        let deadline=Instant::now()+AX_CAPTURE_BUDGET;
                        let current=focused(deadline);let original=retained.get(&id);
                        let same=current.as_ref().zip(original).is_some_and(|(a,b)|a.value["target_id"]==b.value["target_id"]&&a.value["pid"]==b.value["pid"]);
                        let final_read=if same {None} else {original.and_then(|old|snapshot(old.element.clone(),Some(&old.value),deadline))};
                        if !stopping.load(Ordering::Acquire) {commands.request("poll_observation",json!({"observation_id":id,"focused":current.map(|v|v.value),"retained":final_read.map(|v|v.value)}));}
                    },
                    // Ending the previous observation can arrive between a new
                    // capture and its prepare RPC response. Do not discard that
                    // pending target before the new observation retains it.
                    Command::End(id)=>{retained.remove(&id);},
                });
            }
        }).expect("create accessibility worker");
        Self {
            sender: Some(sender),
            closed,
            worker: Some(worker),
        }
    }
    fn submit(&self, command: Command) -> Result<()> {
        if self.closed.load(Ordering::Acquire) {
            bail!("平台读取服务已关闭");
        }
        self.sender
            .as_ref()
            .ok_or_else(|| anyhow::anyhow!("平台读取服务已关闭"))?
            .try_send(command)
            .map_err(|_| anyhow::anyhow!("系统操作繁忙，请稍后再试"))
    }
    pub fn capture(&self, id: Value) -> Result<()> {
        self.submit(Command::Capture(id, Instant::now() + AX_CAPTURE_BUDGET))
    }
    pub fn retain(&self, id: &str, snapshot: &Value) -> Result<()> {
        self.submit(Command::Retain(id.into(), snapshot.clone()))
    }
    pub fn observe(&self, id: &str) -> Result<()> {
        self.submit(Command::Poll(id.into()))
    }
    pub fn end(&self, id: &str) -> Result<()> {
        self.submit(Command::End(id.into()))
    }
}
impl Drop for AccessibilityWorker {
    fn drop(&mut self) {
        self.closed.store(true, Ordering::Release);
        self.sender.take();
        if let Some(worker) = self.worker.take() {
            let deadline = std::time::Instant::now() + std::time::Duration::from_millis(250);
            while !worker.is_finished() && std::time::Instant::now() < deadline {
                std::thread::sleep(std::time::Duration::from_millis(2));
            }
            if worker.is_finished() {
                let _ = worker.join();
            }
            // An unresponsive AX client keeps its worker-owned CF references until
            // the call returns. Close rejects publication and never frees them early.
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn queued_capture_uses_one_total_deadline_and_bounded_ipc() {
        let now = Instant::now();
        let deadline = now + AX_CAPTURE_BUDGET;
        assert_eq!(remaining_timeout(deadline, now), Some(0.04));
        assert_eq!(
            remaining_timeout(deadline, now + Duration::from_millis(140)),
            Some(0.01)
        );
        assert_eq!(remaining_timeout(deadline, deadline), None);
        assert_eq!(
            remaining_timeout(deadline, deadline + Duration::from_millis(1)),
            None
        );
    }
    #[test]
    fn ax_utf16_offsets_keep_scalar_indices_and_reject_split_surrogate() {
        assert_eq!(utf16_range("a🦀中b", 1, 2), Some((1, 1)));
        assert_eq!(utf16_range("a🦀中b", 3, 1), Some((2, 1)));
        assert_eq!(utf16_range("a🦀中b", 2, 1), None);
        assert_eq!(utf16_range("a🦀中b", 0, 9), None);
        assert_eq!(utf16_range("", 0, 0), Some((0, 0)));
    }
}
