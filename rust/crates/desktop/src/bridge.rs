// SPDX-License-Identifier: GPL-3.0-only
//! Bounded, nonblocking UI intents and backend events. Cocoa stays on the main
//! thread; one driver thread owns the backend's Tokio runtime and shutdown.
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use std::collections::VecDeque;
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering},
};
use vocal_more_backend::application::{Application, Options};

pub const UI_QUEUE_CAPACITY: usize = 1024;
pub const COMMAND_QUEUE_CAPACITY: usize = 64;
/// Checked requests reserve their error slot before UI admission. Rejections
/// can therefore always retain pending IDs without an unbounded error queue.
pub const REQUEST_CAPACITY: usize = UI_QUEUE_CAPACITY + COMMAND_QUEUE_CAPACITY;

#[derive(Debug)]
struct RequestPermit(Arc<AtomicUsize>);
impl RequestPermit {
    fn acquire(budget: &Arc<AtomicUsize>) -> Option<Self> {
        let mut count = budget.load(Ordering::Acquire);
        while count < REQUEST_CAPACITY {
            match budget.compare_exchange_weak(
                count,
                count + 1,
                Ordering::AcqRel,
                Ordering::Acquire,
            ) {
                Ok(_) => return Some(Self(budget.clone())),
                Err(current) => count = current,
            }
        }
        None
    }
}
impl Drop for RequestPermit {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::AcqRel);
    }
}

#[derive(Debug)]
pub struct Request {
    pub id: u64,
    pub admitted_at: std::time::Instant,
    forwarded_at: std::time::Instant,
    /// Delivery epoch at admission, before this request's main-thread hop.
    pub epoch: u64,
    pub method: String,
    pub params: Value,
    // Requests have one owner: cloning an accepted ID could duplicate both
    // backend actions and the error slot protected by this reservation.
    permit: Option<RequestPermit>,
}

#[derive(Debug)]
pub struct RequestError {
    pub request: Box<Request>,
    pub message: &'static str,
}
impl RequestError {
    pub fn into_params(self) -> Value {
        self.params()
    }
    fn params(&self) -> Value {
        json!({"request_id":self.request.id,"_ui_epoch":self.request.epoch,
            "method":self.request.method,"params":self.request.params,"message":self.message})
    }
}
impl std::fmt::Display for RequestError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}: {}", self.request.method, self.message)
    }
}
impl std::error::Error for RequestError {}

#[derive(Default)]
struct Overflow {
    errors: VecDeque<RequestError>,
    untracked_count: u64,
    last_untracked: Option<RequestError>,
}
impl Overflow {
    fn aggregate(&mut self, error: RequestError) {
        self.untracked_count = self.untracked_count.saturating_add(1);
        self.last_untracked = Some(error);
    }
}

/// Durable edits admitted before quit must survive shutdown. Starting new
/// recording/provider work is deliberately excluded once paste is revoked.
pub fn durable_request(request: &Request) -> bool {
    durable_method(&request.method, &request.params)
}

/// Also classifies an RPC error already queued for the UI when quit begins.
pub fn durable_method(method: &str, params: &Value) -> bool {
    if method == "ui_action" {
        return matches!(
            params["action"].as_str(),
            Some(
                "setConfig"
                    | "syncFormState"
                    | "setAsrModel"
                    | "setDevice"
                    | "setActiveHotkeys"
                    | "addDictEntry"
                    | "removeDictEntry"
                    | "approveDictionaryLearning"
                    | "rejectDictionaryLearning"
                    | "undoDictionaryLearning"
                    | "deleteRecording"
            )
        );
    }
    matches!(
        method,
        "set_config"
            | "sync_form_state"
            | "set_asr_model"
            | "set_device"
            | "set_active_hotkeys"
            | "set_mode"
            | "add_dict_entry"
            | "remove_dict_entry"
            | "approve_dictionary_learning"
            | "reject_dictionary_learning"
            | "undo_dictionary_learning"
            | "delete_recording"
    )
}

#[derive(Debug)]
pub enum UiEvent {
    Request(Request),
    Backend {
        method: String,
        params: Value,
        queued_at: std::time::Instant,
    },
}

struct Shared {
    sequence: AtomicU64,
    // Low bit is cancellation; upper bits are the epoch. One atomic admission
    // prevents concurrent start/cancel callbacks from mixing the two states.
    admission: AtomicU64,
    generation: AtomicU64,
    idle: AtomicBool,
    closing: AtomicBool,
    quit_requested: AtomicBool,
    budget: Arc<AtomicUsize>,
    overflow: Mutex<Overflow>,
    shutdown_requests: Mutex<Option<Vec<Request>>>,
}

#[derive(Clone)]
pub struct CommandSink {
    events: async_channel::Sender<UiEvent>,
    shared: Arc<Shared>,
}

impl CommandSink {
    /// Enqueue a UI intent without blocking a key event tap or Cocoa callback.
    /// Epoch revocation happens before the main-thread hop, so a queued paste
    /// cannot outrun a cancel received on the keyboard listener thread.
    pub fn request(&self, method: &str, params: Value) -> u64 {
        match self.enqueue(method, params, false) {
            Ok(id) => id,
            Err(error) => {
                let id = error.request.id;
                if !self.shared.closing.load(Ordering::Acquire) {
                    self.shared
                        .overflow
                        .lock()
                        .unwrap_or_else(|e| e.into_inner())
                        .aggregate(error);
                }
                id
            }
        }
    }

    /// Callers establish pending state only after successful admission. A full
    /// or closed queue returns the exact rejected request directly to the UI.
    pub fn request_checked(
        &self,
        method: &str,
        params: Value,
    ) -> std::result::Result<u64, RequestError> {
        self.enqueue(method, params, true)
    }

    fn enqueue(
        &self,
        method: &str,
        params: Value,
        checked: bool,
    ) -> std::result::Result<u64, RequestError> {
        let id = self.shared.sequence.fetch_add(1, Ordering::Relaxed) + 1;
        let closing = self.shared.closing.load(Ordering::Acquire);
        if !closing && matches!(method, "platform_quit" | "quit") {
            self.shared.quit_requested.store(true, Ordering::Release);
        }
        let epoch = if closing {
            self.paste_epoch()
        } else {
            self.admit_epoch(method, &params)
        };
        let mut request = Request {
            id,
            admitted_at: std::time::Instant::now(),
            forwarded_at: std::time::Instant::now(),
            epoch,
            method: method.into(),
            params,
            permit: None,
        };
        if closing {
            return Err(RequestError {
                request: Box::new(request),
                message: "UI command queue is closed; the requested action was not accepted",
            });
        }
        if checked {
            request.permit = RequestPermit::acquire(&self.shared.budget);
            if request.permit.is_none() {
                return Err(RequestError {
                    request: Box::new(request),
                    message: "Too many UI requests are awaiting completion; the requested action was not accepted",
                });
            }
        }
        match self.events.try_send(UiEvent::Request(request)) {
            Ok(()) => Ok(id),
            Err(error) => {
                let message = if error.is_full() {
                    "UI command queue is full; the requested action was not accepted"
                } else {
                    "UI command queue is closed; the requested action was not accepted"
                };
                let UiEvent::Request(request) = error.into_inner() else {
                    unreachable!()
                };
                Err(RequestError {
                    request: Box::new(request),
                    message,
                })
            }
        }
    }

    pub fn paste_epoch(&self) -> u64 {
        self.shared.admission.load(Ordering::Acquire) >> 1
    }
    pub fn generation(&self) -> u64 {
        self.shared.generation.load(Ordering::Acquire)
    }
    pub fn can_paste(&self, epoch: u64, generation: u64) -> bool {
        let admission = self.shared.admission.load(Ordering::Acquire);
        !self.shared.closing.load(Ordering::Acquire)
            && !self.shared.quit_requested.load(Ordering::Acquire)
            && admission & 1 == 0
            && admission >> 1 == epoch
            && self.generation() == generation
    }
    fn admit_epoch(&self, method: &str, params: &Value) -> u64 {
        let mut admission = self.shared.admission.load(Ordering::Acquire);
        loop {
            let revokes = method == "cancel"
                || (session_start(method, params)
                    && (self.shared.idle.load(Ordering::Acquire) || admission & 1 != 0));
            if !revokes {
                return admission >> 1;
            }
            let next = ((admission & !1).wrapping_add(2)) | u64::from(method == "cancel");
            match self.shared.admission.compare_exchange_weak(
                admission,
                next,
                Ordering::AcqRel,
                Ordering::Acquire,
            ) {
                Ok(_) => return next >> 1,
                Err(current) => admission = current,
            }
        }
    }
    pub fn update_session(&self, state: &str, generation: u64) {
        self.shared.generation.store(generation, Ordering::Release);
        self.shared.idle.store(state == "idle", Ordering::Release);
    }
    /// Quit survives a saturated event queue and immediately revokes paste.
    pub fn take_quit_request(&self) -> bool {
        self.shared.quit_requested.swap(false, Ordering::AcqRel)
    }
    pub fn emit(&self, method: &str, params: Value) {
        let _ = self.events.try_send(UiEvent::Backend {
            queued_at: std::time::Instant::now(),
            method: method.into(),
            params,
        });
    }
    pub fn take_overflow(&self) -> Option<Value> {
        let (error, count) = {
            let mut overflow = self
                .shared
                .overflow
                .lock()
                .unwrap_or_else(|e| e.into_inner());
            if let Some(error) = overflow.errors.pop_front() {
                (error, None)
            } else {
                let error = overflow.last_untracked.take()?;
                (error, Some(std::mem::take(&mut overflow.untracked_count)))
            }
        };
        // JSON copying happens after releasing the tiny callback-facing lock.
        let mut params = error.into_params();
        if let Some(count) = count {
            params["rejected_count"] = json!(count);
        }
        Some(params)
    }

    fn retain_error(&self, mut error: RequestError) {
        // A checked request already reserved this slot. Untracked callback
        // failures may use spare slots, then coalesce when the budget is full.
        if error.request.permit.is_none() {
            error.request.permit = RequestPermit::acquire(&self.shared.budget);
        }
        let mut overflow = self
            .shared
            .overflow
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if error.request.permit.is_some() {
            debug_assert!(overflow.errors.len() < REQUEST_CAPACITY);
            overflow.errors.push_back(error);
        } else {
            overflow.aggregate(error);
        }
    }
}

/// Final shutdown outcome. Counts and flags deliberately exclude request
/// identifiers, configuration keys/values, prompts and raw error text.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct ShutdownFailures {
    pub failed_durable_requests: usize,
    pub cleanup_failed: bool,
}
impl ShutdownFailures {
    fn record_durable_failure(&mut self) {
        self.failed_durable_requests = self.failed_durable_requests.saturating_add(1);
    }
}

#[derive(Default)]
struct PublishedShutdownFailures {
    failed_durable_requests: AtomicUsize,
    cleanup_failed: AtomicBool,
}
impl PublishedShutdownFailures {
    fn publish(&self, failures: ShutdownFailures) {
        self.failed_durable_requests
            .store(failures.failed_durable_requests, Ordering::Relaxed);
        self.cleanup_failed
            .store(failures.cleanup_failed, Ordering::Relaxed);
    }
    fn snapshot(&self) -> ShutdownFailures {
        ShutdownFailures {
            failed_durable_requests: self.failed_durable_requests.load(Ordering::Relaxed),
            cleanup_failed: self.cleanup_failed.load(Ordering::Relaxed),
        }
    }
}

pub struct BackendDriver {
    commands: async_channel::Sender<Request>,
    sink: CommandSink,
    finished: Arc<AtomicBool>,
    shutdown_failures: Arc<PublishedShutdownFailures>,
}

impl BackendDriver {
    pub fn start(
        options: Options,
    ) -> Result<(Self, CommandSink, async_channel::Receiver<UiEvent>)> {
        let (events, receiver) = async_channel::bounded(UI_QUEUE_CAPACITY);
        let sink = CommandSink {
            events,
            shared: Arc::new(Shared {
                sequence: AtomicU64::new(0),
                admission: AtomicU64::new(0),
                generation: AtomicU64::new(0),
                idle: AtomicBool::new(true),
                closing: AtomicBool::new(false),
                quit_requested: AtomicBool::new(false),
                budget: Arc::new(AtomicUsize::new(0)),
                overflow: Mutex::new(Overflow::default()),
                shutdown_requests: Mutex::new(None),
            }),
        };
        let (commands, incoming) = async_channel::bounded(COMMAND_QUEUE_CAPACITY);
        let finished = Arc::new(AtomicBool::new(false));
        let shutdown_failures = Arc::new(PublishedShutdownFailures::default());
        let thread_sink = sink.clone();
        let thread_finished = finished.clone();
        let thread_failures = shutdown_failures.clone();
        std::thread::Builder::new()
            .name("vocal-more-backend-driver".into())
            .spawn(move || {
                let mut failures = ShutdownFailures::default();
                let result = run_driver(options, incoming, &thread_sink, &mut failures);
                if let Err(error) = result {
                    failures.cleanup_failed = true;
                    let _ = thread_sink.events.send_blocking(UiEvent::Backend {
                        queued_at: std::time::Instant::now(),
                        method: "backend_disconnected".into(),
                        params: json!({"message":error.to_string()}),
                    });
                }
                // The finished release publishes both fields as one final
                // snapshot; readers never need a lock or wait for the driver.
                thread_failures.publish(failures);
                thread_finished.store(true, Ordering::Release);
            })
            .context("could not start the backend driver")?;
        Ok((
            Self {
                commands,
                sink: sink.clone(),
                finished,
                shutdown_failures,
            },
            sink,
            receiver,
        ))
    }

    pub fn send(&self, mut request: Request) {
        request.forwarded_at = std::time::Instant::now();
        if let Err(error) = self.commands.try_send(request) {
            let request = error.into_inner();
            let error = RequestError {
                request: Box::new(request),
                message: "Backend command queue is unavailable or full; the requested action was not accepted",
            };
            if self
                .sink
                .events
                .try_send(UiEvent::Backend {
                    queued_at: std::time::Instant::now(),
                    method: "rpc_error".into(),
                    params: error.params(),
                })
                .is_err()
            {
                self.sink.retain_error(error);
            }
        }
    }

    pub fn close(&self) {
        let _ = self.close_with_requests(Vec::new());
    }

    /// Transfer the bounded UI queue once; the main thread never waits for
    /// backend capacity. Accepted backend commands precede these UI requests.
    pub fn close_with_requests(&self, mut requests: Vec<Request>) -> Result<()> {
        ensure!(
            requests.len() <= UI_QUEUE_CAPACITY,
            "shutdown handoff exceeds UI queue capacity ({UI_QUEUE_CAPACITY})"
        );
        let mut handoff = self
            .sink
            .shared
            .shutdown_requests
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if self.sink.shared.closing.load(Ordering::Acquire) {
            return Ok(());
        }
        requests.retain(durable_request);
        *handoff = Some(requests);
        // Publication follows the handoff, so the driver cannot observe close
        // and finish before the accepted UI edits have become available.
        self.sink.shared.closing.store(true, Ordering::Release);
        self.sink.shared.admission.fetch_add(2, Ordering::AcqRel);
        self.commands.close();
        self.sink.events.close();
        Ok(())
    }
    pub fn finished(&self) -> bool {
        self.finished.load(Ordering::Acquire)
    }
    /// Read after `finished()` is true. Before completion the outcome is not
    /// known and this returns the default; no partial counts are published.
    pub fn shutdown_failures(&self) -> ShutdownFailures {
        if self.finished() {
            self.shutdown_failures.snapshot()
        } else {
            ShutdownFailures::default()
        }
    }
}
impl Drop for BackendDriver {
    fn drop(&mut self) {
        self.close();
    }
}

fn session_start(method: &str, params: &Value) -> bool {
    matches!(
        method,
        "start" | "toggle_recording" | "hotkey_pressed" | "start_mic_test"
    ) || (method == "ui_action" && params["action"] == "startMicTest")
}

/// The backend increments generation only for a new session, never for cancel.
/// Tag events by that session's admitted request, even when the UI has already
/// admitted cancel and another start before the driver drains old events.
/// Old evicted generations remain untagged and delivery must reject them.
#[derive(Default)]
struct SessionEpochs {
    entries: VecDeque<(u64, u64)>,
}
const SESSION_EPOCH_CAPACITY: usize = 128;
impl SessionEpochs {
    fn remember_start(&mut self, request: &Request, result: &Value) {
        if session_start(&request.method, &request.params)
            && let Some(generation) = result["generation"].as_u64()
            && result["recording_id"].is_string()
            && !self.entries.iter().any(|(known, _)| *known == generation)
        {
            if self.entries.len() == SESSION_EPOCH_CAPACITY {
                self.entries.pop_front();
            }
            self.entries.push_back((generation, request.epoch));
        }
    }
    fn annotate(&self, data: &mut Value) {
        let Some(object) = data.as_object_mut() else {
            return;
        };
        if let Some(generation) = object.get("generation").and_then(Value::as_u64) {
            // Never trust an epoch supplied by a caller or reconstruct it from
            // the current UI epoch: missing provenance is a rejected delivery.
            object.remove("_ui_epoch");
            if let Some((_, epoch)) = self.entries.iter().find(|(known, _)| *known == generation) {
                object.insert("_ui_epoch".into(), json!(epoch));
            }
        }
        if let Some(pastes) = object
            .get_mut("pending_pastes")
            .and_then(Value::as_array_mut)
        {
            for paste in pastes {
                self.annotate(paste);
            }
        }
        if let Some(result) = object.get_mut("last_result") {
            self.annotate(result);
        }
    }
}

fn run_driver(
    options: Options,
    commands: async_channel::Receiver<Request>,
    sink: &CommandSink,
    failures: &mut ShutdownFailures,
) -> Result<()> {
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .max_blocking_threads(4)
        .thread_name("vocal-more-backend")
        .enable_all()
        .build()?;
    let result = runtime.block_on(async {
        let app = Application::open(options).await?;
        let mut events = app.subscribe();
        let mut epochs = SessionEpochs::default();
        let mut snapshot = app.call("initialize", json!({})).await?;
        epochs.annotate(&mut snapshot);
        let _=sink.events.send(UiEvent::Backend { queued_at: std::time::Instant::now(), method: "initialized".into(), params: snapshot }).await;
        let mut fatal = None;
        loop {
            if sink.shared.closing.load(Ordering::Acquire) {
                break;
            }
            tokio::select! {
                request = commands.recv() => {
                    let Ok(request) = request else { break };
                    vocal_more_core::diagnostics::record(vocal_more_core::diagnostics::Stage::MainToBackend, request.forwarded_at.elapsed());
                    let result = {
                        let _span = vocal_more_core::diagnostics::Span::new(vocal_more_core::diagnostics::Stage::BackendRequest);
                        app.call(&request.method, request.params.clone()).await
                    };
                    let durable_failed = durable_request(&request) && result.is_err();
                    let (method, params) = match result {
                        Ok(mut result) => {
                            epochs.remember_start(&request, &result);
                            epochs.annotate(&mut result);
                            if request.method == "claim_paste"
                                && result.get("generation").is_none()
                                && let Some(object) = result.as_object_mut()
                            { object.insert("_ui_epoch".into(), json!(request.epoch)); }
                            ("rpc_response", json!({"request_id":request.id,"_ui_epoch":request.epoch,
                                "method":request.method,"params":request.params,"result":result}))
                        },
                        Err(error) => ("rpc_error", json!({"request_id":request.id,
                            "_ui_epoch":request.epoch,"method":request.method,"params":request.params,"message":error.to_string()})),
                    };
                    let response_lost = sink.events.send(UiEvent::Backend { queued_at: std::time::Instant::now(), method:method.into(), params }).await.is_err();
                    if response_lost && durable_failed {
                        // Closing also releases a response already blocked by
                        // a full UI queue. Preserve that in-flight write's
                        // failure even though its rpc_error cannot be sent.
                        failures.record_durable_failure();
                    }
                    if response_lost && !sink.shared.closing.load(Ordering::Acquire) { break; }
                }
                event = events.recv() => {
                    let event = match event {
                        Ok(event) => {
                            let mut params = event["params"].clone();
                            epochs.annotate(&mut params);
                            UiEvent::Backend {
            queued_at: std::time::Instant::now(),
                                method:event["method"].as_str().unwrap_or("error").into(), params,
                            }
                        },
                        Err(tokio::sync::broadcast::error::RecvError::Lagged(_)) => {
                            // Still run the durable shutdown drain below.
                            let mut params = match app.call("snapshot",json!({})).await {
                                Ok(params) => params,
                                Err(error) => { fatal = Some(error); break; }
                            };
                            epochs.annotate(&mut params);
                            UiEvent::Backend { queued_at: std::time::Instant::now(), method:"resync".into(), params }
                        }
                        Err(_) => break,
                    };
                    if sink.events.send(event).await.is_err() && !sink.shared.closing.load(Ordering::Acquire) { break; }
                }
            }
        }
        // Also reached when close wakes a pending commands.recv(). Draining
        // only at the top of the loop would lose the handoff on that path.
        // Revoke active audio/provider work before potentially many durable
        // writes. A full 1088-write drain can take seconds on a synced disk.
        if app.call("cancel", json!({})).await.is_err() {
            failures.cleanup_failed = true;
        }
        while let Ok(request) = commands.try_recv() {
            if durable_request(&request) && app.call(&request.method, request.params).await.is_err() {
                failures.record_durable_failure();
            }
        }
        let requests = sink.shared.shutdown_requests.lock().unwrap_or_else(|e| e.into_inner()).take().unwrap_or_default();
        for request in requests {
            if durable_request(&request) && app.call(&request.method, request.params).await.is_err() {
                failures.record_durable_failure();
            }
        }
        if app.call("shutdown", json!({})).await.is_err() {
            failures.cleanup_failed = true;
        }
        fatal.map_or(Ok(()), Err)
    });
    runtime.shutdown_timeout(std::time::Duration::from_secs(1));
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{Duration, Instant};
    use vocal_more_backend::{config::ConfigRepository, dictionary::Dictionary};

    fn wait_until(mut ready: impl FnMut() -> bool) {
        let deadline = Instant::now() + Duration::from_secs(20);
        while !ready() {
            assert!(Instant::now() < deadline, "backend fixture timed out");
            std::thread::sleep(Duration::from_millis(1));
        }
    }

    #[test]
    fn in_flight_durable_failure_is_counted_when_its_rpc_error_cannot_be_delivered() -> Result<()> {
        let data = tempfile::tempdir()?;
        let config_path = data.path().join("config.yaml");
        ConfigRepository::open(&config_path)?.update("ui.language", &json!("en"))?;
        let (driver, sink, events) = BackendDriver::start(Options::new(data.path().into()))?;
        wait_until(
            || matches!(events.try_recv(), Ok(UiEvent::Backend { method, .. }) if method == "initialized"),
        );
        let failure_id = sink.request_checked(
            "set_config",
            json!({"key":"api_key","value":"private in-flight value"}),
        )?;
        let later_id = sink.request_checked(
            "add_dict_entry",
            json!({"term":"saved after in-flight failure"}),
        )?;
        let mut failure = None;
        let mut later = None;
        wait_until(|| {
            if let Ok(UiEvent::Request(request)) = events.try_recv() {
                if request.id == failure_id {
                    failure = Some(request);
                } else if request.id == later_id {
                    later = Some(request);
                }
            }
            failure.is_some() && later.is_some()
        });
        for _ in 0..UI_QUEUE_CAPACITY {
            sink.emit("test_filler", json!({}));
        }
        assert_eq!(events.len(), UI_QUEUE_CAPACITY);
        std::fs::remove_file(&config_path)?;
        std::fs::create_dir(&config_path)?;
        driver.send(failure.unwrap());
        // Private queue observation is only a test synchronization barrier:
        // the durable RPC was consumed and is either executing or waiting to
        // deliver its error to the deliberately saturated event channel.
        wait_until(|| driver.commands.is_empty());
        driver.close_with_requests(vec![later.unwrap()])?;
        wait_until(|| driver.finished());
        assert_eq!(
            driver.shutdown_failures(),
            ShutdownFailures {
                failed_durable_requests: 1,
                cleanup_failed: false,
            }
        );
        assert_eq!(events.len(), UI_QUEUE_CAPACITY);
        assert_eq!(
            Dictionary::open(&data.path().join("dictionary.yaml"))?.entries[0].term,
            "saved after in-flight failure"
        );
        Ok(())
    }

    #[test]
    fn driver_fatal_error_sets_only_the_cleanup_failure_flag() -> Result<()> {
        let data = tempfile::NamedTempFile::new()?;
        // A file cannot be opened as an Application data directory. The fatal
        // driver error is aggregated without retaining its raw message/path.
        let (driver, _sink, _events) = BackendDriver::start(Options::new(data.path().into()))?;
        wait_until(|| driver.finished());
        let failures = driver.shutdown_failures();
        assert_eq!(
            failures,
            ShutdownFailures {
                failed_durable_requests: 0,
                cleanup_failed: true,
            }
        );
        assert_eq!(
            format!("{failures:?}"),
            "ShutdownFailures { failed_durable_requests: 0, cleanup_failed: true }"
        );
        Ok(())
    }

    fn sink() -> CommandSink {
        CommandSink {
            events: async_channel::bounded(8).0,
            shared: Arc::new(Shared {
                sequence: AtomicU64::new(0),
                admission: AtomicU64::new(0),
                generation: AtomicU64::new(7),
                idle: AtomicBool::new(false),
                closing: AtomicBool::new(false),
                quit_requested: AtomicBool::new(false),
                budget: Arc::new(AtomicUsize::new(0)),
                overflow: Mutex::new(Overflow::default()),
                shutdown_requests: Mutex::new(None),
            }),
        }
    }
    #[test]
    fn cancellation_revokes_platform_delivery_before_main_thread_dispatch() {
        let sink = sink();
        assert!(sink.can_paste(0, 7));
        sink.request("cancel", json!({}));
        assert!(!sink.can_paste(0, 7));
    }
    #[test]
    fn quit_revokes_paste_and_survives_a_full_ui_queue() {
        let (events, _receiver) = async_channel::bounded(1);
        let sink = CommandSink {
            events,
            shared: sink().shared,
        };
        sink.request_checked("snapshot", json!({})).unwrap();
        assert!(sink.request_checked("platform_quit", json!({})).is_err());
        assert!(!sink.can_paste(0, 7));
        assert!(sink.take_quit_request());
        assert!(!sink.take_quit_request());
    }
    #[test]
    fn stale_generation_and_previous_idle_session_cannot_paste() {
        let sink = sink();
        assert!(!sink.can_paste(0, 6));
        sink.update_session("idle", 7);
        sink.request("hotkey_pressed", json!({}));
        assert!(!sink.can_paste(0, 7));
        assert!(sink.can_paste(1, 7));
    }
    #[test]
    fn queued_requests_keep_their_admitted_epoch_after_cancel_and_restart() {
        let sink = sink();
        // Retain a receiver: a disconnected queue deliberately rejects work.
        let (events, requests) = async_channel::bounded(8);
        let sink = CommandSink {
            events,
            shared: sink.shared,
        };
        sink.update_session("idle", 7);
        sink.request("start", json!({}));
        sink.request("cancel", json!({}));
        sink.request("start", json!({}));
        let admitted: Vec<_> = (0..3)
            .map(|_| match requests.try_recv().unwrap() {
                UiEvent::Request(request) => request.epoch,
                _ => unreachable!(),
            })
            .collect();
        assert_eq!(admitted, [1, 2, 3]);
        assert_eq!(sink.paste_epoch(), 3);
        assert!(!sink.can_paste(1, 7));
    }
    #[test]
    fn provenance_is_bounded_and_snapshot_recovery_does_not_relabel_old_pastes() {
        let mut epochs = SessionEpochs::default();
        for generation in 1..=SESSION_EPOCH_CAPACITY as u64 + 1 {
            let request = Request {
                id: generation,
                admitted_at: std::time::Instant::now(),
                forwarded_at: std::time::Instant::now(),
                epoch: generation * 2,
                method: "start".into(),
                params: json!({}),
                permit: None,
            };
            epochs.remember_start(
                &request,
                &json!({"generation":generation,"recording_id":"fixture"}),
            );
        }
        assert_eq!(epochs.entries.len(), SESSION_EPOCH_CAPACITY);
        let mut snapshot = json!({"generation":129,"pending_pastes":[
            {"generation":1,"_ui_epoch":999}, {"generation":128}, {"generation":129}
        ],"last_result":{"generation":128}});
        epochs.annotate(&mut snapshot);
        assert!(snapshot["pending_pastes"][0].get("_ui_epoch").is_none());
        assert_eq!(snapshot["pending_pastes"][1]["_ui_epoch"], 256);
        assert_eq!(snapshot["pending_pastes"][2]["_ui_epoch"], 258);
        assert_eq!(snapshot["last_result"]["_ui_epoch"], 256);
        // A repeated response with the same generation cannot rebind it.
        epochs.remember_start(
            &Request {
                id: 200,
                admitted_at: std::time::Instant::now(),
                forwarded_at: std::time::Instant::now(),
                epoch: 999,
                method: "hotkey_pressed".into(),
                params: json!({}),
                permit: None,
            },
            &json!({"generation":128,"recording_id":"fixture"}),
        );
        epochs.annotate(&mut snapshot);
        assert_eq!(snapshot["last_result"]["_ui_epoch"], 256);
    }
}
