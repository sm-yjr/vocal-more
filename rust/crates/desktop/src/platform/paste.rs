// SPDX-License-Identifier: GPL-3.0-only
use super::{class, ffi, ns};
use crate::bridge::CommandSink;
use anyhow::{Context, Result, bail};
use objc2::{msg_send, rc::Retained, runtime::AnyObject};
use objc2_foundation::NSString;
use std::time::{Duration, Instant};

struct Restore {
    text: String,
    change_count: isize,
    at: Instant,
}
pub const OWN_EVENT_TAG: i64 = 0x564d52555354;
#[derive(Default)]
pub struct Clipboard {
    restore: Vec<Restore>,
}
fn pasteboard() -> Retained<AnyObject> {
    unsafe { msg_send![class(c"NSPasteboard"), generalPasteboard] }
}
pub fn copy(text: &str) -> Result<()> {
    let board = pasteboard();
    unsafe {
        let _: isize = msg_send![&*board, clearContents];
        let ok: bool =
            msg_send![&*board,setString:&*ns(text),forType:&*ns("public.utf8-plain-text")];
        if !ok {
            bail!("无法写入系统剪贴板");
        }
    }
    Ok(())
}
impl Clipboard {
    pub fn next_tick_delay(&self) -> Option<Duration> {
        self.restore
            .iter()
            .map(|entry| entry.at.saturating_duration_since(Instant::now()))
            .min()
    }
    pub fn paste(
        &mut self,
        commands: &CommandSink,
        text: &str,
        restore: bool,
        native_fast: bool,
        epoch: u64,
        generation: u64,
    ) -> Result<bool> {
        if !commands.can_paste(epoch, generation) {
            return Ok(false);
        }
        let board = pasteboard();
        // While an earlier paste still awaits restore, the board holds our own
        // text; inherit that entry's user content instead of capturing ours.
        let prior: isize = unsafe { msg_send![&*board, changeCount] };
        let inherited = self
            .restore
            .iter()
            .find(|entry| entry.change_count == prior)
            .map(|entry| entry.text.clone());
        let original: Option<String> = if !restore {
            None
        } else if inherited.is_some() {
            inherited
        } else {
            let text: Option<Retained<NSString>> =
                unsafe { msg_send![&*board,stringForType:&*ns("public.utf8-plain-text")] };
            text.map(|text| text.to_string())
        };
        // Allocate all events before mutating the clipboard. Even a failure to
        // create one half of the shortcut must not replace the user's content.
        let source = unsafe { ffi::Owned::from_create(ffi::CGEventSourceCreate(1)) }
            .context("无法创建系统键盘事件源")?;
        let down = unsafe {
            ffi::Owned::from_create(ffi::CGEventCreateKeyboardEvent(source.as_ptr(), 9, true))
        }
        .context("无法创建粘贴按键")?;
        let up = unsafe {
            ffi::Owned::from_create(ffi::CGEventCreateKeyboardEvent(source.as_ptr(), 9, false))
        }
        .context("无法创建粘贴松开事件")?;
        let command_down = if native_fast {
            None
        } else {
            unsafe {
                ffi::Owned::from_create(ffi::CGEventCreateKeyboardEvent(source.as_ptr(), 55, true))
            }
        };
        let command_up = if native_fast {
            None
        } else {
            unsafe {
                ffi::Owned::from_create(ffi::CGEventCreateKeyboardEvent(source.as_ptr(), 55, false))
            }
        };
        if !native_fast && (command_down.is_none() || command_up.is_none()) {
            bail!("无法创建兼容模式粘贴事件");
        }
        if !commands.can_paste(epoch, generation) {
            return Ok(false);
        }
        copy(text)?;
        let count: isize = unsafe { msg_send![&*board, changeCount] };
        if !native_fast {
            std::thread::sleep(Duration::from_millis(50));
        }
        // The event-tap can revoke the epoch while this main-thread method is
        // running. Check again at the actual native injection boundary.
        if !commands.can_paste(epoch, generation) {
            if let Some(original) = original {
                let actual: isize = unsafe { msg_send![&*board, changeCount] };
                if actual == count {
                    let _ = copy(&original);
                }
            }
            return Ok(false);
        }
        unsafe {
            for event in [&down, &up] {
                ffi::CGEventSetIntegerValueField(event.as_ptr(), 42, OWN_EVENT_TAG);
            }
            for event in [command_down.as_ref(), command_up.as_ref()]
                .into_iter()
                .flatten()
            {
                ffi::CGEventSetIntegerValueField(event.as_ptr(), 42, OWN_EVENT_TAG);
            }
            if let Some(event) = command_down {
                ffi::CGEventSetFlags(event.as_ptr(), 0x100000);
                ffi::CGEventPost(0, event.as_ptr());
            }
            ffi::CGEventSetFlags(down.as_ptr(), 0x100000);
            ffi::CGEventSetFlags(up.as_ptr(), 0x100000);
            ffi::CGEventPost(0, down.as_ptr());
            ffi::CGEventPost(0, up.as_ptr());
            if let Some(event) = command_up {
                ffi::CGEventSetFlags(event.as_ptr(), 0);
                ffi::CGEventPost(0, event.as_ptr());
            }
        }
        if let Some(original) = original {
            // The inherited entry can no longer match; this one replaces it.
            self.restore.retain(|entry| entry.change_count != prior);
            self.restore.push(Restore {
                text: original,
                change_count: count,
                at: Instant::now() + Duration::from_millis(600),
            });
        }
        Ok(true)
    }
    pub fn tick(&mut self) {
        let now = Instant::now();
        self.restore.retain(|entry| {
            if entry.at > now {
                return true;
            }
            let board = pasteboard();
            let count: isize = unsafe { msg_send![&*board, changeCount] };
            if should_restore(entry.change_count, count) {
                let _ = copy(&entry.text);
            }
            false
        });
    }
}
fn should_restore(expected: isize, actual: isize) -> bool {
    expected == actual
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn external_clipboard_changes_reject_restore() {
        assert!(should_restore(4, 4));
        assert!(!should_restore(4, 5));
    }
}
