// SPDX-License-Identifier: GPL-3.0-only
//! One bounded owner for current-app AX, guarded paste and its durable ledger.
//! No UI thread waits for AX, clipboard settling or file synchronization.
use crate::bluetooth_delivery::{Admission, Envelope, Ledger, now_ms};
use anyhow::{Context, Result};
use libloading::Library;
use std::{
    ffi::c_void,
    path::PathBuf,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicU64, Ordering},
        mpsc::{self, Receiver, SyncSender},
    },
    thread,
    time::{Duration, Instant},
};

#[derive(Clone, Copy)]
pub struct Target {
    pub revision: u64,
    pub ready: bool,
    checked: Instant,
}
impl Default for Target {
    fn default() -> Self {
        Self {
            revision: 0,
            ready: false,
            checked: Instant::now(),
        }
    }
}
pub struct Completion {
    pub id: u64,
    pub peer: String,
    pub connection: u64,
    pub confirmed: bool,
}
struct Job {
    id: u64,
    peer: String,
    connection: u64,
    raw: String,
    token: u64,
}
struct Guard {
    token: Arc<AtomicU64>,
    expected: u64,
    expires: u64,
    deadline: Instant,
}
impl Guard {
    fn valid(&self) -> bool {
        self.token.load(Ordering::Acquire) == self.expected
            && now_ms() <= self.expires
            && Instant::now() <= self.deadline
    }
}
unsafe extern "C" fn valid(context: *mut c_void) -> i32 {
    i32::from(unsafe { &*(context.cast::<Guard>()) }.valid())
}
trait Destination {
    fn target(&mut self) -> Target;
    // Confirmed only when AX accepted the write. A keyboard paste is an
    // unconfirmed outcome: neither CGEventPost nor queue admission proves text.
    fn insert(&mut self, text: &str, revision: u64, guard: &Guard) -> bool;
}
struct Native {
    handle: *mut c_void,
    close: unsafe extern "C" fn(*mut c_void),
    status: unsafe extern "C" fn(*mut c_void, *mut u64, *mut u32) -> i32,
    insert: unsafe extern "C" fn(
        *mut c_void,
        *const u8,
        u32,
        u64,
        u32,
        unsafe extern "C" fn(*mut c_void) -> i32,
        *mut c_void,
    ) -> i32,
    _library: Library,
    restore: bool,
}
impl Native {
    fn load(restore: bool) -> Result<Self> {
        let path = std::env::current_exe()
            .ok()
            .and_then(|p| {
                Some(
                    p.parent()?
                        .parent()?
                        .join("Frameworks/libvocal_more_audio.dylib"),
                )
            })
            .filter(|p| p.is_file())
            .unwrap_or_else(|| {
                PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                    .join("../../../.build/native/libvocal_more_audio.dylib")
            });
        // Created, used and destroyed on this worker; no AppKit UI objects move
        // through the channel and the native library outlives every handle.
        unsafe {
            let library = Library::new(path)?;
            let version =
                *library.get::<unsafe extern "C" fn() -> u32>(b"vm_bt_current_abi_version\0")?;
            anyhow::ensure!(version() == 1, "input ABI mismatch");
            let create =
                *library.get::<unsafe extern "C" fn() -> *mut c_void>(b"vm_bt_current_create\0")?;
            let close = *library.get(b"vm_bt_current_close\0")?;
            let status = *library.get(b"vm_bt_current_status\0")?;
            let insert = *library.get(b"vm_bt_current_insert\0")?;
            let handle = create();
            anyhow::ensure!(!handle.is_null(), "input target unavailable");
            Ok(Self {
                handle,
                restore,
                close,
                status,
                insert,
                _library: library,
            })
        }
    }
}
impl Destination for Native {
    fn target(&mut self) -> Target {
        let (mut revision, mut ready) = (0, 0);
        let ok = objc2::rc::autoreleasepool(
            |_| unsafe { (self.status)(self.handle, &mut revision, &mut ready) } == 1,
        );
        Target {
            revision,
            ready: ok && ready == 1,
            checked: Instant::now(),
        }
    }
    fn insert(&mut self, text: &str, revision: u64, guard: &Guard) -> bool {
        if !guard.valid() {
            return false;
        }
        objc2::rc::autoreleasepool(|_| unsafe {
            (self.insert)(
                self.handle,
                text.as_ptr(),
                text.len() as u32,
                revision,
                u32::from(self.restore),
                valid,
                (guard as *const Guard).cast_mut().cast(),
            ) == 1
        })
    }
}
impl Drop for Native {
    fn drop(&mut self) {
        unsafe { (self.close)(self.handle) };
    }
}
fn deliver(
    target: &mut impl Destination,
    ledger: &mut Ledger,
    job: &Job,
    token: Arc<AtomicU64>,
) -> bool {
    let result = (|| -> Result<bool> {
        let destination = target.target();
        let message =
            Envelope::decode(&job.raw, destination.revision, destination.ready, now_ms())?;
        let guard = Guard {
            token,
            expected: job.token,
            expires: message.expires(),
            deadline: Instant::now()
                + Duration::from_millis(message.expires().saturating_sub(now_ms())),
        };
        anyhow::ensure!(guard.valid(), "input cancelled");
        match ledger.begin(&job.peer, job.id, message.expires(), now_ms())? {
            Admission::Replay(done) => Ok(done),
            Admission::New => {
                let done =
                    guard.valid() && target.insert(message.text(), message.revision(), &guard);
                ledger.finish(&job.peer, job.id, done)?;
                Ok(done)
            }
        }
    })();
    result.unwrap_or(false)
}
pub struct Input {
    jobs: Option<SyncSender<Job>>,
    completions: Receiver<Completion>,
    target: Arc<Mutex<Target>>,
    token: Arc<AtomicU64>,
    busy: Arc<AtomicBool>,
    finished: Arc<AtomicBool>,
}
struct Finished(Arc<AtomicBool>);
impl Drop for Finished {
    fn drop(&mut self) {
        self.0.store(true, Ordering::Release);
    }
}
impl Input {
    pub fn start(path: PathBuf, restore: bool) -> Result<Self> {
        Self::start_with(path, move || Native::load(restore))
    }
    fn start_with<D: Destination + 'static>(
        path: PathBuf,
        factory: impl FnOnce() -> Result<D> + Send + 'static,
    ) -> Result<Self> {
        let (jobs, rx) = mpsc::sync_channel::<Job>(1);
        let (tx, completions) = mpsc::sync_channel(2);
        let target = Arc::new(Mutex::new(Target::default()));
        let token = Arc::new(AtomicU64::new(1));
        let busy = Arc::new(AtomicBool::new(false));
        let finished = Arc::new(AtomicBool::new(false));
        let (cache, stamp, working, ended) = (
            target.clone(),
            token.clone(),
            busy.clone(),
            finished.clone(),
        );
        thread::Builder::new()
            .name("vocal-more-bluetooth-input".into())
            .spawn(move || {
                let _finished = Finished(ended);
                let Ok(mut native) = factory() else {
                    return;
                };
                let Ok(mut ledger) = Ledger::open(path) else {
                    return;
                };
                loop {
                    *cache.lock().unwrap_or_else(|e| e.into_inner()) = native.target();
                    match rx.recv_timeout(Duration::from_millis(200)) {
                        Ok(job) => {
                            let confirmed = deliver(&mut native, &mut ledger, &job, stamp.clone());
                            *cache.lock().unwrap_or_else(|e| e.into_inner()) = native.target();
                            let _ = tx.try_send(Completion {
                                id: job.id,
                                peer: job.peer,
                                connection: job.connection,
                                confirmed,
                            });
                            working.store(false, Ordering::Release);
                        }
                        Err(mpsc::RecvTimeoutError::Timeout) => {}
                        Err(mpsc::RecvTimeoutError::Disconnected) => break,
                    }
                }
            })
            .context("input worker unavailable")?;
        Ok(Self {
            jobs: Some(jobs),
            completions,
            target,
            token,
            busy,
            finished,
        })
    }
    pub fn target(&self) -> Target {
        let mut value = *self.target.lock().unwrap_or_else(|e| e.into_inner());
        value.ready &= self.jobs.is_some()
            && !self.finished.load(Ordering::Acquire)
            && value.checked.elapsed() < Duration::from_millis(600);
        value
    }
    pub fn submit(&self, id: u64, peer: String, connection: u64, raw: String) -> Result<()> {
        anyhow::ensure!(
            self.busy
                .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
                .is_ok(),
            "receiver busy"
        );
        let result = self.jobs.as_ref().context("input stopped").and_then(|tx| {
            tx.try_send(Job {
                id,
                peer,
                connection,
                raw,
                token: self.token.load(Ordering::Acquire),
            })
            .context("input queue full")
        });
        if result.is_err() {
            self.busy.store(false, Ordering::Release);
        }
        result
    }
    pub fn poll(&self) -> Option<Completion> {
        self.completions.try_recv().ok()
    }
    pub fn invalidate(&self) {
        self.token.fetch_add(1, Ordering::AcqRel);
    }
    pub fn stop(&mut self) {
        if self.jobs.is_some() {
            self.invalidate();
            self.jobs.take();
        }
    }
    pub fn finished(&self) -> bool {
        self.finished.load(Ordering::Acquire)
    }
    pub fn stopped(&self) -> bool {
        self.jobs.is_none()
    }
}
impl Drop for Input {
    fn drop(&mut self) {
        self.stop();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    struct Fake {
        revision: u64,
        ready: bool,
        writes: usize,
        confirms: bool,
        cancel: bool,
    }
    impl Default for Fake {
        fn default() -> Self {
            Self {
                revision: 9,
                ready: true,
                writes: 0,
                confirms: true,
                cancel: false,
            }
        }
    }
    impl Destination for Fake {
        fn target(&mut self) -> Target {
            Target {
                revision: self.revision,
                ready: self.ready,
                checked: Instant::now(),
            }
        }
        fn insert(&mut self, _: &str, revision: u64, guard: &Guard) -> bool {
            if self.cancel {
                guard.token.fetch_add(1, Ordering::AcqRel);
            }
            if !guard.valid() || revision != self.revision || !self.ready {
                return false;
            }
            self.writes += 1;
            self.confirms
        }
    }
    fn job() -> Job {
        Job {
            id: 17,
            peer: "fixed-peer".into(),
            connection: 3,
            raw: Envelope::encode("fixed fixture", 9, now_ms()).unwrap(),
            token: 1,
        }
    }
    #[test]
    fn clock_rollback_cannot_extend_an_already_admitted_deadline() {
        let guard = Guard {
            token: Arc::new(AtomicU64::new(1)),
            expected: 1,
            expires: u64::MAX,
            deadline: Instant::now() - Duration::from_millis(1),
        };
        assert!(!guard.valid());
    }
    #[test]
    fn changed_unready_and_cancelled_targets_never_mutate() {
        for (revision, ready, cancel) in [(8, true, false), (9, false, false), (9, true, true)] {
            let mut target = Fake {
                revision,
                ready,
                cancel,
                ..Default::default()
            };
            assert!(!deliver(
                &mut target,
                &mut Ledger::default(),
                &job(),
                Arc::new(AtomicU64::new(1))
            ));
            assert_eq!(target.writes, 0);
        }
    }
    #[test]
    fn unconfirmed_mutation_and_success_are_never_repeated_after_restart() {
        for confirms in [false, true] {
            let temp = tempfile::tempdir().unwrap();
            let path = temp.path().join("ledger.json");
            let mut ledger = Ledger::open(path.clone()).unwrap();
            let mut target = Fake {
                confirms,
                ..Default::default()
            };
            let job = job();
            let token = Arc::new(AtomicU64::new(1));
            assert_eq!(
                deliver(&mut target, &mut ledger, &job, token.clone()),
                confirms
            );
            let mut restarted = Ledger::open(path.clone()).unwrap();
            assert_eq!(deliver(&mut target, &mut restarted, &job, token), confirms);
            assert_eq!(target.writes, 1);
            assert!(
                !std::fs::read_to_string(path)
                    .unwrap()
                    .contains("fixed fixture")
            );
        }
    }
    #[test]
    fn storage_failure_rejects_before_any_app_mutation() {
        let temp = tempfile::tempdir().unwrap();
        let parent = temp.path().join("not-directory");
        let mut ledger = Ledger::open(parent.join("ledger.json")).unwrap();
        std::fs::write(&parent, b"fixed").unwrap();
        let mut target = Fake::default();
        assert!(!deliver(
            &mut target,
            &mut ledger,
            &job(),
            Arc::new(AtomicU64::new(1))
        ));
        assert_eq!(target.writes, 0);
    }
    #[test]
    fn stopped_worker_finishes_without_joining_the_ui_thread() {
        let temp = tempfile::tempdir().unwrap();
        let mut input =
            Input::start_with(temp.path().join("ledger.json"), || Ok(Fake::default())).unwrap();
        let deadline = Instant::now() + Duration::from_secs(2);
        while !input.target().ready && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(1));
        }
        assert!(input.target().ready);
        input.stop();
        assert!(!input.target().ready);
        assert!(input.submit(17, "fixed-peer".into(), 3, job().raw).is_err());
        while !input.finished() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(1));
        }
        assert!(input.finished());
    }
    #[test]
    fn initialization_failure_is_distinct_from_an_explicit_stop_request() {
        let temp = tempfile::tempdir().unwrap();
        let mut input = Input::start_with::<Fake>(temp.path().join("ledger.json"), || {
            anyhow::bail!("synthetic initialization failure")
        })
        .unwrap();
        let deadline = Instant::now() + Duration::from_secs(2);
        while !input.finished() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(1));
        }
        assert!(input.finished());
        assert!(
            !input.stopped(),
            "failure must not look like permission to restart every UI tick"
        );
        assert!(!input.target().ready);
        input.stop();
        assert!(input.stopped());
    }
}
