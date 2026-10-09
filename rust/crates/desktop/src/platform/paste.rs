// SPDX-License-Identifier: GPL-3.0-only
use super::{class, ffi, ns};
use crate::bridge::CommandSink;
use anyhow::{Context, Result, bail, ensure};
use objc2::{msg_send, rc::Retained, runtime::AnyObject};
use objc2_foundation::NSString;
use std::time::{Duration, Instant};

struct Restore {
    // Keep None as an ownership marker: a second streaming chunk must not
    // mistake our first chunk for the user's original non-text clipboard.
    text: Option<String>,
    payload: Option<String>,
    change_count: isize,
    at: Instant,
    retries: u8,
}
pub const OWN_EVENT_TAG: i64 = 0x564d52555354;
#[derive(Default)]
pub struct Clipboard {
    restore: Option<Restore>,
    failed: bool,
    pending: Option<PendingPaste>,
}
struct PendingWrite {
    text: String,
    before: Option<String>,
    previous: Option<Restore>,
    count: isize,
    restore: bool,
}
struct PendingPaste {
    write: PendingWrite,
    events: PasteEvents,
    commands: CommandSink,
    epoch: u64,
    generation: u64,
    at: Instant,
    admitted: Instant,
}
struct PasteEvents {
    down: ffi::Owned,
    up: ffi::Owned,
    command_down: Option<ffi::Owned>,
    command_up: Option<ffi::Owned>,
}
impl PasteEvents {
    fn post(self) {
        let _span = vocal_more_core::diagnostics::Span::new(
            vocal_more_core::diagnostics::Stage::NativePastePost,
        );
        unsafe {
            for event in [&self.down, &self.up]
                .into_iter()
                .chain(self.command_down.as_ref())
                .chain(self.command_up.as_ref())
            {
                ffi::CGEventSetIntegerValueField(event.as_ptr(), 42, OWN_EVENT_TAG);
            }
            if let Some(event) = self.command_down {
                ffi::CGEventSetFlags(event.as_ptr(), 0x100000);
                ffi::CGEventPost(0, event.as_ptr());
            }
            ffi::CGEventSetFlags(self.down.as_ptr(), 0x100000);
            ffi::CGEventSetFlags(self.up.as_ptr(), 0x100000);
            ffi::CGEventPost(0, self.down.as_ptr());
            ffi::CGEventPost(0, self.up.as_ptr());
            if let Some(event) = self.command_up {
                ffi::CGEventSetFlags(event.as_ptr(), 0);
                ffi::CGEventPost(0, event.as_ptr());
            }
        }
    }
}
#[derive(Debug)]
struct ClaimedWriteFailed(isize);
impl std::fmt::Display for ClaimedWriteFailed {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("无法写入系统剪贴板")
    }
}
impl std::error::Error for ClaimedWriteFailed {}
fn failed_claim(error: &anyhow::Error, fallback: isize) -> isize {
    error
        .downcast_ref::<ClaimedWriteFailed>()
        .map_or(fallback, |error| error.0)
}
trait Pasteboard {
    fn change_count(&self) -> isize;
    fn snapshot(&self) -> Result<(isize, Option<String>)>;
    fn copy(&mut self, text: &str) -> Result<isize>;
}
struct SystemPasteboard(Retained<AnyObject>);
impl Pasteboard for SystemPasteboard {
    fn change_count(&self) -> isize {
        unsafe { msg_send![&*self.0, changeCount] }
    }
    fn snapshot(&self) -> Result<(isize, Option<String>)> {
        let count = self.change_count();
        let text: Option<Retained<NSString>> =
            unsafe { msg_send![&*self.0,stringForType:&*ns("public.utf8-plain-text")] };
        ensure!(count == self.change_count(), "读取时剪贴板已变化，请重试");
        Ok((count, text.map(|text| text.to_string())))
    }
    fn copy(&mut self, text: &str) -> Result<isize> {
        write_text(&self.0, text)
    }
}
fn pasteboard() -> Retained<AnyObject> {
    unsafe { msg_send![class(c"NSPasteboard"), generalPasteboard] }
}
pub fn copy(text: &str) -> Result<()> {
    let board = pasteboard();
    write_text(&board, text).map(|_| ())
}
fn write_text(board: &AnyObject, text: &str) -> Result<isize> {
    unsafe {
        // clearContents returns our ownership token. Reading changeCount after
        // writing instead could adopt a different application's newer token.
        let claim: isize = msg_send![board, clearContents];
        let ok: bool = msg_send![board,setString:&*ns(text),forType:&*ns("public.utf8-plain-text")];
        if !ok {
            return Err(ClaimedWriteFailed(claim).into());
        }
        let actual: isize = msg_send![board, changeCount];
        ensure!(claim == actual, "写入时剪贴板已被其他应用修改");
        Ok(claim)
    }
}
impl Clipboard {
    pub fn next_tick_delay(&self) -> Option<Duration> {
        self.restore
            .iter()
            .map(|entry| entry.at)
            .chain(self.pending.iter().map(|entry| entry.at))
            .min()
            .map(|at| at.saturating_duration_since(Instant::now()))
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
        ensure!(self.pending.is_none(), "粘贴仍在等待提交");
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
        let mut board = SystemPasteboard(pasteboard());
        let Some(write) = self.begin(
            &mut board,
            text,
            restore,
            || commands.can_paste(epoch, generation),
            Instant::now(),
        )?
        else {
            return Ok(false);
        };
        let events = PasteEvents {
            down,
            up,
            command_down,
            command_up,
        };
        if native_fast {
            self.finish(
                &mut board,
                write,
                || commands.can_paste(epoch, generation),
                || events.post(),
                Instant::now(),
            )
        } else {
            // Keep the compatibility settling window without blocking AppKit.
            // The host holds its FIFO lane until tick validates and posts it.
            self.pending = Some(PendingPaste {
                write,
                events,
                commands: commands.clone(),
                epoch,
                generation,
                at: Instant::now() + Duration::from_millis(50),
                admitted: Instant::now(),
            });
            Ok(true)
        }
    }
    pub fn is_delivering(&self) -> bool {
        self.pending.is_some()
    }
    pub fn poll_delivery(&mut self) -> Result<Option<bool>> {
        if self.pending.as_ref().is_none_or(|entry| {
            entry.at > Instant::now() && entry.commands.can_paste(entry.epoch, entry.generation)
        }) {
            return Ok(None);
        }
        let entry = self.pending.take().expect("pending delivery");
        vocal_more_core::diagnostics::record(
            vocal_more_core::diagnostics::Stage::NativePasteWait,
            entry.admitted.elapsed(),
        );
        self.finish(
            &mut SystemPasteboard(pasteboard()),
            entry.write,
            || entry.commands.can_paste(entry.epoch, entry.generation),
            || entry.events.post(),
            Instant::now(),
        )
        .map(Some)
    }
    pub fn cancel_delivery(&mut self) -> Result<()> {
        if let Some(entry) = self.pending.take() {
            self.finish(
                &mut SystemPasteboard(pasteboard()),
                entry.write,
                || false,
                || unreachable!("cancelled delivery cannot post"),
                Instant::now(),
            )?;
        }
        Ok(())
    }
    fn begin<B: Pasteboard>(
        &mut self,
        board: &mut B,
        text: &str,
        restore: bool,
        allowed: impl Fn() -> bool,
        now: Instant,
    ) -> Result<Option<PendingWrite>> {
        if !allowed() {
            return Ok(None);
        }
        let (prior, before) = board.snapshot()?;
        if !allowed() || board.change_count() != prior {
            return Ok(None);
        }
        let previous = self
            .restore
            .take()
            .filter(|entry| entry.change_count == prior && before == entry.payload);
        let count = match board.copy(text) {
            Ok(count) => count,
            Err(error) => {
                if let Some(failure) = error.downcast_ref::<ClaimedWriteFailed>() {
                    self.rollback(board, before, previous, failure.0, None, now)?;
                } else {
                    self.restore = previous;
                }
                return Err(error);
            }
        };
        Ok(Some(PendingWrite {
            text: text.into(),
            before,
            previous,
            count,
            restore,
        }))
    }
    fn finish<B: Pasteboard>(
        &mut self,
        board: &mut B,
        write: PendingWrite,
        allowed: impl Fn() -> bool,
        post: impl FnOnce(),
        now: Instant,
    ) -> Result<bool> {
        let PendingWrite {
            text,
            before,
            previous,
            count,
            restore,
        } = write;
        let (actual, payload) = match board.snapshot() {
            Ok(value) => value,
            Err(error) => {
                // Keep a bounded cleanup owner when the insertion boundary
                // cannot be read; never guess that the shortcut was posted.
                self.restore = Some(Restore {
                    text: previous.map_or(before, |entry| entry.text),
                    payload: Some(text),
                    change_count: count,
                    at: now,
                    retries: 0,
                });
                return Err(error);
            }
        };
        let owned = actual == count && payload.as_deref() == Some(&text);
        if !allowed() || !owned {
            if owned && before.is_some() {
                self.rollback(board, before, previous, count, Some(&text), now)?;
            }
            return Ok(false);
        }
        post();
        if restore {
            self.restore = Some(Restore {
                text: previous.map_or(before, |entry| entry.text),
                payload: Some(text),
                change_count: count,
                at: now + Duration::from_millis(600),
                retries: 0,
            });
        }
        Ok(true)
    }
    // Existing ownership/race fixtures exercise the exact two production phases.
    #[cfg(test)]
    #[allow(clippy::too_many_arguments)]
    fn deliver<B: Pasteboard>(
        &mut self,
        board: &mut B,
        text: &str,
        restore: bool,
        allowed: impl Fn() -> bool,
        wait: impl FnOnce(&mut B),
        post: impl FnOnce(),
        clock: impl Fn() -> Instant,
    ) -> Result<bool> {
        let Some(write) = self.begin(board, text, restore, &allowed, clock())? else {
            return Ok(false);
        };
        wait(board);
        self.finish(board, write, allowed, post, clock())
    }
    pub fn is_pending(&self) -> bool {
        self.restore.is_some()
    }
    pub fn mark_failed(&mut self) {
        self.failed = true;
    }
    pub fn failed(&self) -> bool {
        self.failed
    }
    fn rollback(
        &mut self,
        board: &mut impl Pasteboard,
        before: Option<String>,
        mut previous: Option<Restore>,
        claim: isize,
        expected: Option<&str>,
        now: Instant,
    ) -> Result<()> {
        if !board
            .snapshot()
            .is_ok_and(|(count, payload)| count == claim && payload.as_deref() == expected)
        {
            return Ok(());
        }
        let Some(before) = before else {
            return Ok(());
        };
        match board.copy(&before) {
            Ok(count) => {
                if let Some(entry) = &mut previous {
                    entry.change_count = count;
                    entry.payload = Some(before);
                }
                self.restore = previous;
            }
            Err(error) => {
                let claim = failed_claim(&error, claim);
                let expected = if error.is::<ClaimedWriteFailed>() {
                    None
                } else {
                    expected
                };
                if let Ok((count, payload)) = board.snapshot()
                    && count == claim
                    && payload.as_deref() == expected
                {
                    // Preserve the user baseline for bounded cleanup, but
                    // report this transaction's rollback error immediately.
                    // A later successful cleanup is not a quit failure.
                    let mut entry = previous.unwrap_or(Restore {
                        text: Some(before),
                        payload: None,
                        change_count: claim,
                        at: now + Duration::from_millis(50),
                        retries: 0,
                    });
                    entry.change_count = count;
                    entry.payload = payload;
                    self.restore = Some(entry);
                }
                return Err(error.context("无法恢复系统剪贴板"));
            }
        }
        Ok(())
    }
    pub fn tick(&mut self) {
        let now = Instant::now();
        if self.restore.as_ref().is_some_and(|entry| entry.at <= now) {
            self.tick_with(&mut SystemPasteboard(pasteboard()), now);
        }
    }
    fn tick_with(&mut self, board: &mut impl Pasteboard, now: Instant) {
        if self.restore.as_ref().is_none_or(|entry| entry.at > now) {
            return;
        }
        let mut entry = self.restore.take().expect("pending restore");
        if board
            .snapshot()
            .is_ok_and(|(count, text)| count == entry.change_count && text == entry.payload)
            && let Some(text) = &entry.text
            && let Err(error) = board.copy(text)
        {
            let claim = failed_claim(&error, entry.change_count);
            let expected = if error.is::<ClaimedWriteFailed>() {
                None
            } else {
                entry.payload.as_deref()
            };
            if let Ok((count, payload)) = board.snapshot()
                && count == claim
                && payload.as_deref() == expected
            {
                if entry.retries == 0 {
                    entry.retries = 1;
                    entry.change_count = count;
                    entry.payload = payload;
                    entry.at = now + Duration::from_millis(50);
                    self.restore = Some(entry);
                } else {
                    // At most one delayed retry: cleanup must not keep a user
                    // from quitting forever when NSPasteboard rejects writes.
                    self.failed = true;
                }
            }
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::Cell;

    struct Board {
        count: isize,
        text: Option<String>,
        writes: Vec<String>,
        fail_copy: bool,
        partial_failures: u8,
        after_write: Option<String>,
        fail_snapshot: bool,
    }
    impl Board {
        fn new(text: Option<&str>) -> Self {
            Self {
                count: 1,
                text: text.map(str::to_owned),
                writes: Vec::new(),
                fail_copy: false,
                partial_failures: 0,
                after_write: None,
                fail_snapshot: false,
            }
        }
        fn external(&mut self, text: &str) {
            self.count += 1;
            self.text = Some(text.into());
        }
    }
    impl Pasteboard for Board {
        fn change_count(&self) -> isize {
            self.count
        }
        fn snapshot(&self) -> Result<(isize, Option<String>)> {
            ensure!(!self.fail_snapshot, "simulated clipboard read race");
            Ok((self.count, self.text.clone()))
        }
        fn copy(&mut self, text: &str) -> Result<isize> {
            ensure!(!self.fail_copy, "simulated clipboard write failure");
            if self.partial_failures > 0 {
                self.partial_failures -= 1;
                self.count += 1;
                self.text = None;
                return Err(ClaimedWriteFailed(self.count).into());
            }
            self.external(text);
            let claim = self.count;
            self.writes.push(text.into());
            if let Some(external) = self.after_write.take() {
                self.external(&external);
            }
            Ok(claim)
        }
    }
    fn paste(
        clipboard: &mut Clipboard,
        board: &mut Board,
        text: &str,
        restore: bool,
        now: Instant,
    ) {
        let posted = Cell::new(false);
        assert!(
            clipboard
                .deliver(
                    board,
                    text,
                    restore,
                    || true,
                    |_| {},
                    || posted.set(true),
                    || now
                )
                .unwrap()
        );
        assert!(posted.get());
    }

    #[test]
    fn compatibility_wait_yields_and_cancel_before_post_preserves_clipboard() {
        let now = Instant::now();
        let mut clipboard = Clipboard::default();
        let mut board = Board::new(Some("fixture-user"));
        let posted = Cell::new(false);
        let write = clipboard
            .begin(&mut board, "fixture-output", true, || true, now)
            .unwrap()
            .unwrap();
        // The real host may handle an Fn/cancel event between these phases.
        assert!(!posted.get());
        assert_eq!(board.text.as_deref(), Some("fixture-output"));
        assert!(
            !clipboard
                .finish(
                    &mut board,
                    write,
                    || false,
                    || posted.set(true),
                    now + Duration::from_millis(25)
                )
                .unwrap()
        );
        assert!(!posted.get());
        assert_eq!(board.text.as_deref(), Some("fixture-user"));
    }
    #[test]
    fn deferred_post_keeps_the_full_600ms_read_grace() {
        let now = Instant::now();
        let mut clipboard = Clipboard::default();
        let mut board = Board::new(Some("fixture-user"));
        let write = clipboard
            .begin(&mut board, "fixture-output", true, || true, now)
            .unwrap()
            .unwrap();
        assert!(
            clipboard
                .finish(
                    &mut board,
                    write,
                    || true,
                    || {},
                    now + Duration::from_millis(50)
                )
                .unwrap()
        );
        clipboard.tick_with(&mut board, now + Duration::from_millis(600));
        assert_eq!(board.text.as_deref(), Some("fixture-output"));
        clipboard.tick_with(&mut board, now + Duration::from_millis(650));
        assert_eq!(board.text.as_deref(), Some("fixture-user"));
    }
    #[test]
    fn compatibility_microbenchmark_uses_only_fixture_board() {
        let mut legacy = Vec::new();
        let mut deferred = Vec::new();
        for _ in 0..12 {
            let mut clipboard = Clipboard::default();
            let mut board = Board::new(Some("fixture-user"));
            let start = Instant::now();
            clipboard
                .deliver(
                    &mut board,
                    "fixture-output",
                    true,
                    || true,
                    |_| std::thread::sleep(Duration::from_millis(50)),
                    || {},
                    Instant::now,
                )
                .unwrap();
            legacy.push(start.elapsed().as_secs_f64() * 1000.0);
            let start = Instant::now();
            let write = clipboard
                .begin(&mut board, "fixture-next", true, || true, Instant::now())
                .unwrap()
                .unwrap();
            deferred.push(start.elapsed().as_secs_f64() * 1000.0);
            clipboard
                .finish(
                    &mut board,
                    write,
                    || true,
                    || {},
                    Instant::now() + Duration::from_millis(50),
                )
                .unwrap();
        }
        legacy.sort_by(f64::total_cmp);
        deferred.sort_by(f64::total_cmp);
        println!(
            "fixture_compatibility_main_thread legacy_median_ms={:.3} deferred_median_ms={:.3}",
            legacy[6], deferred[6]
        );
        assert!(legacy[6] >= 50.0);
        // No wall-clock threshold for the new path; scheduler load is not a
        // correctness failure. Deterministic phase tests prove the yield.
    }
    #[test]
    fn consecutive_chunks_replace_deadline_and_restore_the_user_baseline() {
        let now = Instant::now();
        let mut clipboard = Clipboard::default();
        let mut board = Board::new(Some("user"));
        paste(&mut clipboard, &mut board, "A", true, now);
        paste(
            &mut clipboard,
            &mut board,
            "B",
            true,
            now + Duration::from_millis(300),
        );
        clipboard.tick_with(&mut board, now + Duration::from_millis(600));
        assert_eq!(board.text.as_deref(), Some("B"));
        assert!(clipboard.is_pending());
        clipboard.tick_with(&mut board, now + Duration::from_millis(900));
        assert_eq!(board.text.as_deref(), Some("user"));
        assert_eq!(board.writes, ["A", "B", "user"]);
        assert!(!clipboard.is_pending());
    }
    #[test]
    fn an_external_write_between_chunks_becomes_the_new_baseline() {
        let now = Instant::now();
        let mut clipboard = Clipboard::default();
        let mut board = Board::new(Some("user"));
        paste(&mut clipboard, &mut board, "A", true, now);
        board.external("external");
        paste(&mut clipboard, &mut board, "B", true, now);
        clipboard.tick_with(&mut board, now + Duration::from_millis(600));
        assert_eq!(board.text.as_deref(), Some("external"));
        assert!(!clipboard.is_pending());
    }
    #[test]
    fn an_external_write_after_the_last_chunk_is_preserved() {
        let now = Instant::now();
        let mut clipboard = Clipboard::default();
        let mut board = Board::new(Some("user"));
        paste(&mut clipboard, &mut board, "A", true, now);
        board.external("external");
        clipboard.tick_with(&mut board, now + Duration::from_secs(1));
        assert_eq!(board.text.as_deref(), Some("external"));
        assert_eq!(board.writes, ["A"]);
        assert!(!clipboard.is_pending());
    }
    #[test]
    fn an_external_write_during_compatibility_wait_prevents_injection() {
        let mut clipboard = Clipboard::default();
        let mut board = Board::new(Some("user"));
        let posted = Cell::new(false);
        assert!(
            !clipboard
                .deliver(
                    &mut board,
                    "A",
                    true,
                    || true,
                    |board| board.external("external"),
                    || posted.set(true),
                    Instant::now
                )
                .unwrap()
        );
        assert!(!posted.get());
        assert_eq!(board.text.as_deref(), Some("external"));
        assert!(!clipboard.is_pending());
    }
    #[test]
    fn cancellation_before_post_rolls_back_without_injection() {
        let mut clipboard = Clipboard::default();
        let mut board = Board::new(Some("user"));
        let allowed = Cell::new(true);
        let posted = Cell::new(false);
        assert!(
            !clipboard
                .deliver(
                    &mut board,
                    "A",
                    true,
                    || allowed.get(),
                    |_| allowed.set(false),
                    || posted.set(true),
                    Instant::now
                )
                .unwrap()
        );
        assert!(!posted.get());
        assert_eq!(board.text.as_deref(), Some("user"));
        assert_eq!(board.writes, ["A", "user"]);
        assert!(!clipboard.is_pending());
    }
    #[test]
    fn a_cancelled_chunk_preserves_the_already_posted_payload_and_its_deadline() {
        for restore in [false, true] {
            let now = Instant::now();
            let mut clipboard = Clipboard::default();
            let mut board = Board::new(Some("user"));
            paste(&mut clipboard, &mut board, "A", true, now);
            let allowed = Cell::new(true);
            let posted = Cell::new(false);
            assert!(
                !clipboard
                    .deliver(
                        &mut board,
                        "B",
                        restore,
                        || allowed.get(),
                        |_| allowed.set(false),
                        || posted.set(true),
                        || now + Duration::from_millis(300)
                    )
                    .unwrap()
            );
            assert!(!posted.get());
            clipboard.tick_with(&mut board, now + Duration::from_millis(599));
            // The asynchronous event for A can still read A during its grace period.
            assert_eq!(board.text.as_deref(), Some("A"));
            assert!(clipboard.is_pending());
            clipboard.tick_with(&mut board, now + Duration::from_millis(600));
            assert_eq!(board.text.as_deref(), Some("user"));
            assert_eq!(board.writes, ["A", "B", "A", "user"]);
            assert!(!clipboard.is_pending());
        }
    }
    #[test]
    fn cancellation_never_overwrites_a_new_external_owner() {
        let mut clipboard = Clipboard::default();
        let mut board = Board::new(Some("user"));
        let allowed = Cell::new(true);
        let posted = Cell::new(false);
        assert!(
            !clipboard
                .deliver(
                    &mut board,
                    "A",
                    true,
                    || allowed.get(),
                    |board| {
                        board.external("external");
                        allowed.set(false);
                    },
                    || posted.set(true),
                    Instant::now
                )
                .unwrap()
        );
        assert!(!posted.get());
        assert_eq!(board.text.as_deref(), Some("external"));
        assert_eq!(board.writes, ["A"]);
        assert!(!clipboard.is_pending());
    }
    #[test]
    fn disabling_restore_for_a_successful_chunk_keeps_the_new_payload() {
        let now = Instant::now();
        let mut clipboard = Clipboard::default();
        let mut board = Board::new(Some("user"));
        paste(&mut clipboard, &mut board, "A", true, now);
        paste(&mut clipboard, &mut board, "B", false, now);
        clipboard.tick_with(&mut board, now + Duration::from_secs(1));
        assert_eq!(board.text.as_deref(), Some("B"));
        assert_eq!(board.writes, ["A", "B"]);
        assert!(!clipboard.is_pending());
    }
    #[test]
    fn non_text_baselines_are_not_confused_with_empty_text_or_our_first_chunk() {
        for original in [None, Some("")] {
            let now = Instant::now();
            let mut clipboard = Clipboard::default();
            let mut board = Board::new(original);
            paste(&mut clipboard, &mut board, "A", true, now);
            paste(&mut clipboard, &mut board, "B", true, now);
            clipboard.tick_with(&mut board, now + Duration::from_millis(599));
            assert_eq!(board.text.as_deref(), Some("B"));
            assert!(clipboard.is_pending());
            clipboard.tick_with(&mut board, now + Duration::from_millis(600));
            assert_eq!(board.text.as_deref(), original.or(Some("B")));
            assert!(!clipboard.is_pending());
        }
    }
    #[test]
    fn failed_writes_do_not_post_or_discard_an_earlier_restore() {
        let now = Instant::now();
        let mut clipboard = Clipboard::default();
        let mut board = Board::new(Some("user"));
        paste(&mut clipboard, &mut board, "A", true, now);
        board.fail_copy = true;
        let posted = Cell::new(false);
        assert!(
            clipboard
                .deliver(
                    &mut board,
                    "B",
                    true,
                    || true,
                    |_| {},
                    || posted.set(true),
                    || now
                )
                .is_err()
        );
        assert!(!posted.get());
        assert_eq!(board.text.as_deref(), Some("A"));
        assert!(clipboard.is_pending());
        board.fail_copy = false;
        clipboard.tick_with(&mut board, now + Duration::from_millis(600));
        assert_eq!(board.text.as_deref(), Some("user"));
        assert!(!clipboard.is_pending());
    }
    #[test]
    fn a_revoked_request_does_not_touch_the_clipboard() {
        let mut clipboard = Clipboard::default();
        let mut board = Board::new(Some("user"));
        let posted = Cell::new(false);
        assert!(
            !clipboard
                .deliver(
                    &mut board,
                    "A",
                    true,
                    || false,
                    |_| {},
                    || posted.set(true),
                    Instant::now
                )
                .unwrap()
        );
        assert!(!posted.get());
        assert_eq!(board.text.as_deref(), Some("user"));
        assert!(board.writes.is_empty());
        assert!(!clipboard.is_pending());
    }
    #[test]
    fn a_copy_returns_its_claim_instead_of_adopting_a_later_external_owner() {
        let now = Instant::now();
        let mut clipboard = Clipboard::default();
        let mut board = Board::new(Some("user"));
        board.after_write = Some("external".into());
        let posted = Cell::new(false);
        assert!(
            !clipboard
                .deliver(
                    &mut board,
                    "A",
                    true,
                    || true,
                    |_| {},
                    || posted.set(true),
                    || now
                )
                .unwrap()
        );
        clipboard.tick_with(&mut board, now + Duration::from_secs(1));
        assert!(!posted.get());
        assert_eq!(board.text.as_deref(), Some("external"));
        assert!(!clipboard.is_pending());
    }
    #[test]
    fn payload_checks_also_preserve_changes_that_do_not_advance_the_owner_count() {
        let now = Instant::now();
        let mut clipboard = Clipboard::default();
        let mut board = Board::new(Some("user"));
        let posted = Cell::new(false);
        assert!(
            !clipboard
                .deliver(
                    &mut board,
                    "A",
                    true,
                    || true,
                    |board| board.text = Some("external".into()),
                    || posted.set(true),
                    || now
                )
                .unwrap()
        );
        assert!(!posted.get());
        assert_eq!(board.text.as_deref(), Some("external"));
        paste(&mut clipboard, &mut board, "B", true, now);
        board.text = Some("another external value".into());
        clipboard.tick_with(&mut board, now + Duration::from_millis(600));
        assert_eq!(board.text.as_deref(), Some("another external value"));
        assert!(!clipboard.is_pending());
    }
    #[test]
    fn a_claimed_partial_write_failure_rolls_back_before_any_injection() {
        let mut clipboard = Clipboard::default();
        let mut board = Board::new(Some("user"));
        board.partial_failures = 1;
        let posted = Cell::new(false);
        assert!(
            clipboard
                .deliver(
                    &mut board,
                    "A",
                    true,
                    || true,
                    |_| {},
                    || posted.set(true),
                    Instant::now
                )
                .is_err()
        );
        assert!(!posted.get());
        assert_eq!(board.text.as_deref(), Some("user"));
        assert!(!clipboard.is_pending());
        assert!(!clipboard.failed());
    }
    #[test]
    fn failed_cancellation_rollback_retains_the_user_baseline_and_reports_failure() {
        let now = Instant::now();
        let mut clipboard = Clipboard::default();
        let mut board = Board::new(Some("user"));
        paste(&mut clipboard, &mut board, "A", true, now);
        let allowed = Cell::new(true);
        let posted = Cell::new(false);
        assert!(
            clipboard
                .deliver(
                    &mut board,
                    "B",
                    true,
                    || allowed.get(),
                    |board| {
                        allowed.set(false);
                        board.partial_failures = 1;
                    },
                    || posted.set(true),
                    || now + Duration::from_millis(300)
                )
                .is_err()
        );
        assert!(!posted.get());
        assert_eq!(board.text, None);
        assert!(clipboard.is_pending());
        assert!(!clipboard.failed());
        clipboard.tick_with(&mut board, now + Duration::from_millis(600));
        assert_eq!(board.text.as_deref(), Some("user"));
        assert!(!clipboard.is_pending());
        assert!(!clipboard.failed());
    }
    #[test]
    fn a_partial_deadline_restore_failure_has_one_bounded_retry() {
        let now = Instant::now();
        let mut clipboard = Clipboard::default();
        let mut board = Board::new(Some("user"));
        paste(&mut clipboard, &mut board, "A", true, now);
        board.partial_failures = 1;
        clipboard.tick_with(&mut board, now + Duration::from_millis(600));
        assert_eq!(board.text, None);
        assert!(clipboard.is_pending());
        clipboard.tick_with(&mut board, now + Duration::from_millis(650));
        assert_eq!(board.text.as_deref(), Some("user"));
        assert!(!clipboard.is_pending());
        assert!(!clipboard.failed());
    }
    #[test]
    fn permanent_restore_failure_is_reported_without_preventing_quit_forever() {
        let now = Instant::now();
        let mut clipboard = Clipboard::default();
        let mut board = Board::new(Some("user"));
        paste(&mut clipboard, &mut board, "A", true, now);
        board.fail_copy = true;
        clipboard.tick_with(&mut board, now + Duration::from_millis(600));
        assert!(clipboard.is_pending());
        clipboard.tick_with(&mut board, now + Duration::from_millis(650));
        assert!(!clipboard.is_pending());
        assert!(clipboard.failed());
        assert_eq!(board.text.as_deref(), Some("A"));
        clipboard.tick_with(&mut board, now + Duration::from_secs(1));
        assert_eq!(board.writes, ["A"]);
    }
    #[test]
    fn re_enabling_restore_uses_the_text_intentionally_kept_while_disabled() {
        let now = Instant::now();
        let mut clipboard = Clipboard::default();
        let mut board = Board::new(Some("user"));
        paste(&mut clipboard, &mut board, "A", true, now);
        paste(&mut clipboard, &mut board, "B", false, now);
        paste(&mut clipboard, &mut board, "C", true, now);
        clipboard.tick_with(&mut board, now + Duration::from_millis(600));
        assert_eq!(board.text.as_deref(), Some("B"));
        assert_eq!(board.writes, ["A", "B", "C", "B"]);
        assert!(!clipboard.is_pending());
        assert!(!clipboard.failed());
    }
    #[test]
    fn a_snapshot_race_after_write_never_posts_or_overwrites_an_external_owner() {
        let now = Instant::now();
        let mut clipboard = Clipboard::default();
        let mut board = Board::new(Some("user"));
        paste(&mut clipboard, &mut board, "A", true, now);
        let posted = Cell::new(false);
        assert!(
            clipboard
                .deliver(
                    &mut board,
                    "B",
                    true,
                    || true,
                    |board| {
                        board.external("external");
                        board.fail_snapshot = true;
                    },
                    || posted.set(true),
                    || now
                )
                .is_err()
        );
        clipboard.tick_with(&mut board, now + Duration::from_secs(1));
        assert!(!posted.get());
        assert_eq!(board.text.as_deref(), Some("external"));
        assert_eq!(board.writes, ["A", "B"]);
        assert!(!clipboard.is_pending());
    }
    #[test]
    fn a_partial_write_failure_keeps_an_earlier_posted_payload_and_restore_deadline() {
        let now = Instant::now();
        let mut clipboard = Clipboard::default();
        let mut board = Board::new(Some("user"));
        paste(&mut clipboard, &mut board, "A", true, now);
        board.partial_failures = 1;
        let posted = Cell::new(false);
        assert!(
            clipboard
                .deliver(
                    &mut board,
                    "B",
                    true,
                    || true,
                    |_| {},
                    || posted.set(true),
                    || now + Duration::from_millis(300)
                )
                .is_err()
        );
        assert!(!posted.get());
        clipboard.tick_with(&mut board, now + Duration::from_millis(599));
        assert_eq!(board.text.as_deref(), Some("A"));
        assert!(clipboard.is_pending());
        clipboard.tick_with(&mut board, now + Duration::from_millis(600));
        assert_eq!(board.text.as_deref(), Some("user"));
        assert_eq!(board.writes, ["A", "A", "user"]);
        assert!(!clipboard.is_pending());
        assert!(!clipboard.failed());
    }
}
