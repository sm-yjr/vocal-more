// SPDX-License-Identifier: GPL-3.0-only
//! One ordinary thread owns every native call and the optional stopped graph.
use super::*;
use std::sync::mpsc::{Receiver, SyncSender, sync_channel};
use std::time::Instant;

const IDLE_RETENTION: Duration = Duration::from_secs(30 * 60);

pub(super) struct Capture {
    pub lease: NativeLease,
    pub dsp: Dsp,
    pub device: Option<DeviceOptions>,
    pub tx: mpsc::Sender<Input>,
    pub stop: CancellationToken,
    pub cancel: CancellationToken,
}
enum Command {
    Prepare(Dsp, Option<DeviceOptions>),
    Capture(Capture),
}
pub(super) struct Worker {
    tx: SyncSender<Command>,
    preparing: Arc<AtomicBool>,
    closed: CancellationToken,
}
impl Worker {
    pub fn new(symbols: Arc<NativeSymbols>) -> Result<Self> {
        // At most one prepare plus one admitted recording, including during a
        // stalled prepare. A recording's existing startup deadline still wins.
        let (tx, rx) = sync_channel(2);
        let preparing = Arc::new(AtomicBool::new(false));
        let closed = CancellationToken::new();
        let flag = preparing.clone();
        let stop = closed.clone();
        std::thread::Builder::new()
            .name("vocal-more-native-owner".into())
            .spawn(move || run(&symbols, rx, &flag, &stop))?;
        Ok(Self {
            tx,
            preparing,
            closed,
        })
    }
    pub fn prepare(&self, dsp: Dsp, device: Option<DeviceOptions>) -> Result<bool> {
        if self.preparing.swap(true, Ordering::AcqRel) {
            return Ok(false);
        }
        if self.tx.try_send(Command::Prepare(dsp, device)).is_err() {
            self.preparing.store(false, Ordering::Release);
            return Ok(false);
        }
        Ok(true)
    }
    pub fn capture(&self, request: Capture) -> Result<()> {
        self.tx
            .try_send(Command::Capture(request))
            .map_err(|_| anyhow::anyhow!("native owner is unavailable or busy"))
    }
}
impl Drop for Worker {
    fn drop(&mut self) {
        // No join on the command/UI thread. Dropping the only sender wakes an
        // idle owner; an in-flight native call rejects publication on return.
        self.closed.cancel();
    }
}

struct Warm<'a> {
    handle: Handle<'a>,
    dsp: Dsp,
    device: Option<DeviceOptions>,
    route: Value,
    retained_at: Instant,
}
impl Warm<'_> {
    fn matches(&self, dsp: &Dsp, device: &Option<DeviceOptions>, route: &Value) -> bool {
        !route.is_null()
            && &self.dsp == dsp
            && &self.device == device
            && &self.route == route
            && self.retained_at.elapsed() < IDLE_RETENTION
    }
}

fn run(
    symbols: &NativeSymbols,
    rx: Receiver<Command>,
    preparing: &AtomicBool,
    closed: &CancellationToken,
) {
    let mut warm: Option<Warm<'_>> = None;
    loop {
        let wait = warm.as_ref().map_or(IDLE_RETENTION, |w| {
            IDLE_RETENTION.saturating_sub(w.retained_at.elapsed())
        });
        let command = match rx.recv_timeout(wait) {
            Ok(command) => command,
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {
                warm = None;
                symbols.diagnostics.lock().unwrap()["warm_prepared"] = json!(false);
                continue;
            }
            Err(_) => break,
        };
        if closed.is_cancelled() {
            break;
        }
        match command {
            Command::Prepare(dsp, device) => {
                if !symbols.busy.load(Ordering::Acquire)
                    && !symbols.quarantined.load(Ordering::Acquire)
                {
                    // Re-read route on this isolated owner, never in a hotkey
                    // or state notification. A default-input change invalidates
                    // the warm handle even when the config still says "default".
                    let route = symbols.devices().unwrap_or(Value::Null);
                    if warm
                        .as_ref()
                        .is_none_or(|w| !w.matches(&dsp, &device, &route))
                    {
                        warm = None;
                        let result = (|| -> Result<Warm<'_>> {
                            ensure!(
                                !symbols.quarantined.load(Ordering::Acquire),
                                "native cleanup failed"
                            );
                            ensure!(
                                symbols
                                    .authorization
                                    .is_some_and(|call| unsafe { call() } == 3),
                                "microphone is not authorized"
                            );
                            let handle = create_handle(symbols, &dsp, device.as_ref())?;
                            if closed.is_cancelled() {
                                bail!("audio owner closed");
                            }
                            let mut error = [0 as c_char; 512];
                            let code = unsafe {
                                symbols.prepare.unwrap()(
                                    handle.raw,
                                    error.as_mut_ptr(),
                                    error.len(),
                                )
                            };
                            ensure!(code == 0, "native graph preparation failed");
                            Ok(Warm {
                                handle,
                                dsp,
                                device,
                                route,
                                retained_at: Instant::now(),
                            })
                        })();
                        if let Ok(prepared) = result
                            && !closed.is_cancelled()
                        {
                            warm = Some(prepared);
                        }
                    }
                }
                symbols.diagnostics.lock().unwrap()["warm_prepared"] = json!(warm.is_some());
                preparing.store(false, Ordering::Release);
            }
            Command::Capture(request) => {
                // The lease remains owned until capture AND teardown finish.
                // A caller timing out only cancels; it never destroys our handle.
                let result = (|| -> Result<()> {
                    if request.cancel.is_cancelled() {
                        return Ok(());
                    }
                    if request.stop.is_cancelled() {
                        return Ok(());
                    }
                    let route = if symbols.supports_reuse() {
                        symbols.devices().unwrap_or(Value::Null)
                    } else {
                        Value::Null
                    };
                    if warm
                        .as_ref()
                        .is_some_and(|w| !w.matches(&request.dsp, &request.device, &route))
                    {
                        warm = None;
                    }
                    symbols.begin_session();
                    ensure!(
                        !symbols.quarantined.load(Ordering::Acquire),
                        "native cleanup failed; restart host"
                    );
                    if request.cancel.is_cancelled() || request.stop.is_cancelled() {
                        return Ok(());
                    }
                    let reused = warm.is_some();
                    let handle = match warm.take() {
                        Some(warm) => warm.handle,
                        None => create_handle(symbols, &request.dsp, request.device.as_ref())?,
                    };
                    if request.cancel.is_cancelled()
                        || request.stop.is_cancelled()
                        || closed.is_cancelled()
                    {
                        return Ok(());
                    }
                    let reusable = capture(&handle, &request, reused)?;
                    if reusable
                        && symbols.supports_reuse()
                        && request.stop.is_cancelled()
                        && !request.cancel.is_cancelled()
                        && !closed.is_cancelled()
                        && symbols.pending_dsp.lock().unwrap().is_none()
                    {
                        // pause drained EOS and removed tap/consumer. A resumed
                        // session resets queues, converter and stateful DSP.
                        observe_native(symbols, handle.raw);
                        finish_diagnostics(symbols, true);
                        warm = Some(Warm {
                            handle,
                            dsp: request.dsp.clone(),
                            device: request.device.clone(),
                            route,
                            retained_at: Instant::now(),
                        });
                    }
                    Ok(())
                })();
                symbols.diagnostics.lock().unwrap()["warm_prepared"] = json!(warm.is_some());
                match result {
                    Ok(()) => {
                        let _ = request.tx.blocking_send(Input::Finish);
                    }
                    Err(error) => {
                        let _ = request.tx.blocking_send(Input::Fault(error.to_string()));
                    }
                }
                drop(request.lease);
            }
        }
    }
}
