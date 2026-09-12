// SPDX-License-Identifier: GPL-3.0-only
//! File/native sources run on ordinary threads, never a Tokio worker.
mod worker;
use crate::runtime::Input;
use anyhow::{Context, Result, bail, ensure};
use bytes::Bytes;
use libloading::Library;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    ffi::{CString, c_char, c_void},
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};
use tokio::sync::mpsc;
use tokio_util::sync::CancellationToken;

#[derive(Clone, Default, Debug, Deserialize, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Source {
    #[default]
    Stream,
    Wav {
        path: PathBuf,
        #[serde(default = "yes")]
        paced: bool,
    },
    Native {
        #[serde(default)]
        dsp: Dsp,
    },
    ConfiguredNative {
        #[serde(default)]
        dsp: Dsp,
        #[serde(default)]
        device: DeviceOptions,
    },
}
impl Source {
    pub fn is_native(&self) -> bool {
        matches!(self, Self::Native { .. } | Self::ConfiguredNative { .. })
    }
    pub fn native_dsp(&self) -> Option<&Dsp> {
        match self {
            Self::Native { dsp } | Self::ConfiguredNative { dsp, .. } => Some(dsp),
            _ => None,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub struct DeviceOptions {
    pub voice_processing: bool,
    pub input_device: Option<String>,
    pub capture_channels: u32,
    pub block_frames: u32,
}
impl Default for DeviceOptions {
    fn default() -> Self {
        Self {
            voice_processing: false,
            input_device: None,
            capture_channels: 1,
            block_frames: 1280,
        }
    }
}
impl DeviceOptions {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            (1..=3).contains(&self.capture_channels),
            "capture channels must be 1..3"
        );
        ensure!(
            (128..=8192).contains(&self.block_frames),
            "block frames must be 128..8192"
        );
        ensure!(
            self.input_device
                .as_ref()
                .is_none_or(|d| d.len() <= 4096 && !d.contains('\0')),
            "invalid input device"
        );
        Ok(())
    }
}
fn yes() -> bool {
    true
}

#[derive(Clone, Debug, PartialEq, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub struct Dsp {
    pub automatic_gain: bool,
    pub gain: f32,
    pub highpass_enabled: bool,
    pub highpass_hz: f32,
    pub soft_limiter: bool,
}
impl Default for Dsp {
    fn default() -> Self {
        Self {
            automatic_gain: false,
            gain: 4.0,
            highpass_enabled: true,
            highpass_hz: 50.0,
            soft_limiter: true,
        }
    }
}
impl Dsp {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            self.gain.is_finite() && (0.501..=50.0).contains(&self.gain),
            "gain must be finite and between 0.501 and 50"
        );
        ensure!(
            self.highpass_hz.is_finite() && (50.0..=500.0).contains(&self.highpass_hz),
            "high-pass frequency must be between 50 and 500 Hz"
        );
        Ok(())
    }
}

type Create = unsafe extern "C" fn(
    i32,
    u32,
    u32,
    bool,
    f32,
    bool,
    f32,
    bool,
    *mut c_char,
    usize,
) -> *mut c_void;
type Lifecycle = unsafe extern "C" fn(*mut c_void, *mut c_char, usize) -> i32;
type CreateConfigured = unsafe extern "C" fn(
    i32,
    u32,
    u32,
    bool,
    f32,
    bool,
    f32,
    bool,
    bool,
    *const c_char,
    u32,
    *mut c_char,
    usize,
) -> *mut c_void;
type ListDevices = unsafe extern "C" fn(*mut c_char, usize) -> i32;
type Authorization = unsafe extern "C" fn() -> i32;
type Transliterate = unsafe extern "C" fn(*const c_char, *mut c_char, usize) -> i32;
type SetDsp = unsafe extern "C" fn(*mut c_void, f32, bool, f32, bool);
type Read = unsafe extern "C" fn(
    *mut c_void,
    *mut i16,
    u32,
    *mut u32,
    *mut f32,
    u32,
    *mut c_char,
    usize,
) -> i32;
type Destroy = unsafe extern "C" fn(*mut c_void);
type Counter = unsafe extern "C" fn(*mut c_void) -> u64;

struct NativeSymbols {
    // ABI destroy can intentionally retain a handle if callbacks cannot stop.
    // Pin its code for the process lifetime, including after Host is dropped.
    _library: &'static Library,
    create: Create,
    create_configured: Option<CreateConfigured>,
    list_devices: Option<ListDevices>,
    authorization: Option<Authorization>,
    transliterate: Option<Transliterate>,
    set_dsp: Option<SetDsp>,
    pending_dsp: Mutex<Option<Dsp>>,
    diagnostics: Mutex<Value>,
    source_rate: Option<unsafe extern "C" fn(*mut c_void) -> f64>,
    agc: Option<unsafe extern "C" fn(*mut c_void) -> bool>,
    first_tap: Option<Counter>,
    first_pcm: Option<Counter>,
    prepare: Option<Lifecycle>,
    pause: Option<Lifecycle>,
    resume: Option<Lifecycle>,
    start: Lifecycle,
    stop: Lifecycle,
    read: Read,
    destroy: Destroy,
    dropped: Counter,
    faults: Counter,
    busy: AtomicBool,
    quarantined: AtomicBool,
    path: PathBuf,
}

#[derive(Clone)]
pub struct NativeAudio(Arc<NativeSymbols>, Arc<worker::Worker>);

impl NativeAudio {
    /// Load only a caller-selected build of the project's native C ABI library.
    pub fn load(path: impl AsRef<Path>) -> Result<Self> {
        let path = std::fs::canonicalize(path).context("native audio library not found")?;
        // SAFETY: the explicit library path is trusted application code. ABI is
        // checked before use; all opaque handles stay on their owning thread.
        unsafe {
            let library = Library::new(&path).context("load native audio library")?;
            let version =
                *library.get::<unsafe extern "C" fn() -> u32>(b"vm_audio_abi_version\0")?;
            ensure!(
                version() == 2,
                "unsupported native audio ABI; rebuild from native/audio"
            );
            let symbols = Arc::new(NativeSymbols {
                create: *library.get(b"vm_audio_create\0")?,
                create_configured: library
                    .get::<CreateConfigured>(b"vm_audio_create_configured\0")
                    .ok()
                    .map(|v| *v),
                list_devices: library
                    .get::<ListDevices>(b"vm_audio_list_devices\0")
                    .ok()
                    .map(|v| *v),
                authorization: library
                    .get::<Authorization>(b"vm_audio_microphone_authorization\0")
                    .ok()
                    .map(|v| *v),
                transliterate: library
                    .get::<Transliterate>(b"vm_platform_transliterate\0")
                    .ok()
                    .map(|v| *v),
                set_dsp: library
                    .get::<SetDsp>(b"vm_audio_set_dsp\0")
                    .ok()
                    .map(|v| *v),
                pending_dsp: Mutex::new(None),
                diagnostics: Mutex::new(json!({})),
                source_rate: library
                    .get::<unsafe extern "C" fn(*mut c_void) -> f64>(
                        b"vm_audio_source_sample_rate\0",
                    )
                    .ok()
                    .map(|v| *v),
                agc: library
                    .get::<unsafe extern "C" fn(*mut c_void) -> bool>(b"vm_audio_agc_enabled\0")
                    .ok()
                    .map(|v| *v),
                first_tap: library
                    .get::<Counter>(b"vm_audio_first_tap_latency_ns\0")
                    .ok()
                    .map(|v| *v),
                first_pcm: library
                    .get::<Counter>(b"vm_audio_first_pcm_latency_ns\0")
                    .ok()
                    .map(|v| *v),
                prepare: library
                    .get::<Lifecycle>(b"vm_audio_prepare\0")
                    .ok()
                    .map(|v| *v),
                pause: library
                    .get::<Lifecycle>(b"vm_audio_pause\0")
                    .ok()
                    .map(|v| *v),
                resume: library
                    .get::<Lifecycle>(b"vm_audio_resume\0")
                    .ok()
                    .map(|v| *v),
                start: *library.get(b"vm_audio_start\0")?,
                stop: *library.get(b"vm_audio_stop\0")?,
                read: *library.get(b"vm_audio_read\0")?,
                destroy: *library.get(b"vm_audio_destroy\0")?,
                dropped: *library.get(b"vm_audio_dropped_blocks\0")?,
                faults: *library.get(b"vm_audio_runtime_fault_count\0")?,
                _library: Box::leak(Box::new(library)),
                busy: AtomicBool::new(false),
                quarantined: AtomicBool::new(false),
                path,
            });
            let worker = worker::Worker::new(symbols.clone())?;
            Ok(Self(symbols, Arc::new(worker)))
        }
    }

    /// Prepare a stopped graph only. No tap, consumer, or microphone capture
    /// starts here; an explicit capture request is still required.
    pub fn prepare_idle(&self, source: &Source) -> Result<bool> {
        if self.busy()
            || self.quarantined()
            || !self.0.supports_reuse()
            || self.microphone_authorization()? != 3
        {
            return Ok(false);
        }
        let (dsp, device) = match source {
            Source::Native { dsp } => (dsp.clone(), None),
            Source::ConfiguredNative { dsp, device } => {
                device.validate()?;
                (dsp.clone(), Some(device.clone()))
            }
            _ => return Ok(false),
        };
        dsp.validate()?;
        self.1.prepare(dsp, device)
    }

    pub fn diagnostics(&self) -> Value {
        self.0.diagnostics.lock().unwrap().clone()
    }
    pub fn busy(&self) -> bool {
        self.0.busy.load(Ordering::Acquire)
    }
    /// Only the application microphone-preview lane calls this. The audio
    /// owner consumes updates between reads, never inside the render callback.
    pub fn preview_dsp(&self, dsp: Dsp) -> Result<()> {
        dsp.validate()?;
        ensure!(self.0.set_dsp.is_some(), "native DSP extension unavailable");
        *self.0.pending_dsp.lock().unwrap() = Some(dsp);
        Ok(())
    }
    pub fn list_devices(&self) -> Result<serde_json::Value> {
        self.0.devices()
    }
    pub fn microphone_authorization(&self) -> Result<i32> {
        let call = self
            .0
            .authorization
            .context("native permission extension unavailable")?;
        // SAFETY: read-only OS status call, no stream or permission prompt.
        let status = unsafe { call() };
        ensure!(
            (0..=3).contains(&status),
            "cannot query microphone permission"
        );
        Ok(status)
    }
    pub fn transliterate(&self, text: &str) -> Option<String> {
        if text.len() > 4096 {
            return None;
        }
        let call = self.0.transliterate?;
        let input = CString::new(text).ok()?;
        let mut output = vec![0u8; 32 * 1024];
        // SAFETY: bounded UTF-8 string and writable buffer live for this call.
        if unsafe { call(input.as_ptr(), output.as_mut_ptr().cast(), output.len()) } != 0 {
            return None;
        }
        let end = output.iter().position(|b| *b == 0)?;
        String::from_utf8(output[..end].to_vec()).ok()
    }
    pub fn quarantined(&self) -> bool {
        self.0.quarantined.load(Ordering::Acquire)
    }
    pub fn path(&self) -> &Path {
        &self.0.path
    }

    fn claim(&self) -> Result<NativeLease> {
        self.0
            .busy
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .map_err(|_| {
                anyhow::anyhow!("previous native audio call or cleanup is still in flight")
            })?;
        *self.0.pending_dsp.lock().unwrap() = None;
        Ok(NativeLease(self.0.clone()))
    }
}
impl NativeSymbols {
    fn supports_reuse(&self) -> bool {
        self.prepare.is_some() && self.pause.is_some() && self.resume.is_some()
    }
    fn devices(&self) -> Result<Value> {
        let call = self
            .list_devices
            .context("native device extension unavailable; rebuild library")?;
        let mut buffer = vec![0u8; 256 * 1024];
        // SAFETY: writable buffer is valid for the synchronous ABI call.
        ensure!(
            unsafe { call(buffer.as_mut_ptr().cast(), buffer.len()) } == 0,
            "cannot list input devices"
        );
        let end = buffer
            .iter()
            .position(|b| *b == 0)
            .context("invalid device response")?;
        Ok(serde_json::from_slice(&buffer[..end])?)
    }
    fn begin_session(&self) {
        let mut state = self.diagnostics.lock().unwrap();
        let last = state.get("last_session").cloned().unwrap_or(Value::Null);
        *state = json!({"phase":"starting","last_session":last});
    }
}
struct NativeLease(Arc<NativeSymbols>);
impl Drop for NativeLease {
    fn drop(&mut self) {
        if !self.0.quarantined.load(Ordering::Acquire) {
            self.0.busy.store(false, Ordering::Release);
        }
    }
}
struct Handle<'a> {
    raw: *mut c_void,
    symbols: &'a NativeSymbols,
}
impl Drop for Handle<'_> {
    fn drop(&mut self) {
        // SAFETY: only this thread touches the handle. Prove callbacks stopped
        // before calling the void destroy ABI; otherwise keep the lease busy
        // and its code pinned until process exit, avoiding another active graph.
        let mut error = [0 as c_char; 512];
        let stopped = unsafe { (self.symbols.stop)(self.raw, error.as_mut_ptr(), error.len()) };
        observe_native(self.symbols, self.raw);
        finish_diagnostics(self.symbols, stopped == 0);
        if stopped == 0 {
            unsafe { (self.symbols.destroy)(self.raw) }
        } else {
            self.symbols.quarantined.store(true, Ordering::Release);
            self.symbols.busy.store(true, Ordering::Release);
        }
    }
}

fn finish_diagnostics(symbols: &NativeSymbols, success: bool) {
    let mut status = symbols.diagnostics.lock().unwrap();
    if status["phase"] == "active" || status["phase"] == "starting" {
        let mut last = status.clone();
        last.as_object_mut().unwrap().remove("last_session");
        status["last_session"] = last;
    }
    status["phase"] = json!(if success { "inactive" } else { "failed" });
}

// Called exclusively by the owner while its opaque native handle is alive.
fn observe_native(symbols: &NativeSymbols, raw: *mut c_void) {
    let mut state = symbols.diagnostics.lock().unwrap();
    unsafe {
        state["queue_dropped_blocks"] = json!((symbols.dropped)(raw));
        state["runtime_fault_count"] = json!((symbols.faults)(raw));
        if let Some(read) = symbols.source_rate {
            state["source_sample_rate_hz"] = json!(read(raw));
        }
        if let Some(read) = symbols.agc {
            state["agc_enabled_observed"] = json!(read(raw));
        }
        if let Some(read) = symbols.first_tap {
            state["startup_timing_ms"]["first_tap"] = json!(read(raw) as f64 / 1_000_000.0);
        }
        if let Some(read) = symbols.first_pcm {
            let elapsed = read(raw);
            state["startup_timing_ms"]["first_pcm"] = json!(elapsed as f64 / 1_000_000.0);
            state["first_pcm_observed"] = json!(elapsed > 0);
        }
    }
}

pub(crate) fn spawn(
    source: Source,
    tx: mpsc::Sender<Input>,
    stop: CancellationToken,
    cancel: CancellationToken,
    native: Option<NativeAudio>,
) -> Result<()> {
    match source {
        Source::Stream => {}
        Source::Wav { path, paced } => {
            let runtime = tokio::runtime::Handle::current();
            std::thread::Builder::new()
                .name("vocal-more-wav-source".into())
                .spawn(move || {
                    let result = replay(&path, paced, &tx, &stop, &cancel, &runtime);
                    if let Err(error) = result {
                        let _ = tx.blocking_send(Input::Fault(error.to_string()));
                    }
                })?;
        }
        source @ (Source::Native { .. } | Source::ConfiguredNative { .. }) => {
            let (dsp, device) = match source {
                Source::Native { dsp } => (dsp, None),
                Source::ConfiguredNative { dsp, device } => {
                    device.validate()?;
                    (dsp, Some(device))
                }
                _ => unreachable!(),
            };
            dsp.validate()?;
            let native = native.context("native input requires --native-library")?;
            let lease = native.claim()?;
            native.1.capture(worker::Capture {
                lease,
                dsp,
                device,
                tx,
                stop,
                cancel,
            })?;
        }
    }
    Ok(())
}

fn replay(
    path: &Path,
    paced: bool,
    tx: &mpsc::Sender<Input>,
    stop: &CancellationToken,
    cancel: &CancellationToken,
    runtime: &tokio::runtime::Handle,
) -> Result<()> {
    let mut reader = hound::WavReader::open(path).context("open WAV input")?;
    let spec = reader.spec();
    ensure!(
        spec.channels == 1
            && spec.sample_rate == crate::SAMPLE_RATE
            && spec.bits_per_sample == 16
            && spec.sample_format == hound::SampleFormat::Int,
        "WAV input must be 16 kHz mono PCM16; resample explicitly before replay"
    );
    tx.blocking_send(Input::Ready).context("session closed")?;
    let mut samples = reader.samples::<i16>();
    loop {
        if cancel.is_cancelled() {
            return Ok(());
        }
        if stop.is_cancelled() {
            break;
        }
        let mut pcm = Vec::with_capacity(crate::BLOCK_BYTES);
        for _ in 0..crate::BLOCK_FRAMES {
            match samples.next() {
                Some(sample) => pcm.extend_from_slice(&sample?.to_le_bytes()),
                None => break,
            }
        }
        if pcm.is_empty() {
            break;
        }
        let duration = Duration::from_secs_f64(pcm.len() as f64 / (crate::SAMPLE_RATE * 2) as f64);
        tx.blocking_send(Input::Pcm(Bytes::from(pcm)))
            .context("session closed")?;
        if paced {
            runtime.block_on(async {
                tokio::select! {
                    biased;
                    _ = cancel.cancelled() => {},
                    _ = stop.cancelled() => {},
                    _ = tokio::time::sleep(duration) => {},
                }
            });
        }
    }
    tx.blocking_send(Input::Finish).context("session closed")?;
    Ok(())
}

fn create_handle<'a>(
    symbols: &'a NativeSymbols,
    dsp: &Dsp,
    device: Option<&DeviceOptions>,
) -> Result<Handle<'a>> {
    let mut error = [0 as c_char; 512];
    let device_name = device
        .and_then(|d| d.input_device.as_ref())
        .map(|d| CString::new(d.as_bytes()))
        .transpose()?;
    // SAFETY: every pointer points into storage valid for the synchronous call;
    // the returned opaque handle is used/destroyed on this same ordinary thread.
    let raw = unsafe {
        if let Some(device) = device {
            let create = symbols
                .create_configured
                .context("configured native capture extension unavailable")?;
            create(
                crate::SAMPLE_RATE as i32,
                device.block_frames,
                32,
                dsp.automatic_gain,
                dsp.gain,
                dsp.highpass_enabled,
                dsp.highpass_hz,
                dsp.soft_limiter,
                device.voice_processing,
                device_name
                    .as_ref()
                    .map_or(std::ptr::null(), |n| n.as_ptr()),
                device.capture_channels,
                error.as_mut_ptr(),
                error.len(),
            )
        } else {
            (symbols.create)(
                crate::SAMPLE_RATE as i32,
                crate::BLOCK_FRAMES as u32,
                32,
                dsp.automatic_gain,
                dsp.gain,
                dsp.highpass_enabled,
                dsp.highpass_hz,
                dsp.soft_limiter,
                error.as_mut_ptr(),
                error.len(),
            )
        }
    };
    ensure!(!raw.is_null(), "native audio create failed");
    Ok(Handle { raw, symbols })
}

fn capture(handle: &Handle<'_>, request: &worker::Capture, resume: bool) -> Result<bool> {
    let worker::Capture {
        dsp,
        device,
        tx,
        stop,
        cancel,
        ..
    } = request;
    let device = device.as_ref();
    let symbols = handle.symbols;
    let mut error = [0 as c_char; 512];
    if cancel.is_cancelled() {
        return Ok(false);
    }
    let start = if resume {
        symbols.resume.unwrap()
    } else {
        symbols.start
    };
    let started = std::time::Instant::now();
    let code = unsafe { start(handle.raw, error.as_mut_ptr(), error.len()) };
    ensure!(
        code == 0,
        "native audio start failed; check microphone permission and default input"
    );
    if cancel.is_cancelled() {
        return Ok(false);
    }
    {
        let mut state = symbols.diagnostics.lock().unwrap();
        state["phase"] = json!("active");
        state["start_verified"] = json!(true);
        state["voice_processing_enabled_observed"] =
            json!(device.is_none_or(|d| d.voice_processing));
        state["diagnostics_fresh"] = json!(true);
        state["native_backend"] = json!("objective_cpp");
        state["warm_reused"] = json!(resume);
        state["startup_timing_ms"] = json!({"native_start":started.elapsed().as_secs_f64()*1000.0});
    }
    observe_native(symbols, handle.raw);
    {
        let mut state = symbols.diagnostics.lock().unwrap();
        let voice = device.is_none_or(|d| d.voice_processing);
        state["gain_control_verified"] =
            json!(!voice || state["agc_enabled_observed"].as_bool() == Some(dsp.automatic_gain));
        state["requested_gain_mode"] = json!(if dsp.automatic_gain {
            "automatic"
        } else {
            "manual"
        });
    }
    tx.blocking_send(Input::Ready).context("session closed")?;
    let mut stopping = false;
    let mut dsp_changed = false;
    let mut pcm = vec![0i16; device.map_or(crate::BLOCK_FRAMES, |d| d.block_frames as usize)];
    loop {
        if cancel.is_cancelled() {
            return Ok(false);
        }
        if let Some(dsp) = symbols.pending_dsp.lock().unwrap().take()
            && let Some(update) = symbols.set_dsp
        {
            dsp_changed = true;
            // SAFETY: this is the same thread that owns and destroys the handle.
            unsafe {
                update(
                    handle.raw,
                    dsp.gain,
                    dsp.highpass_enabled,
                    dsp.highpass_hz,
                    dsp.soft_limiter,
                )
            };
        }
        if stop.is_cancelled() && !stopping {
            let stop = if symbols.supports_reuse() {
                symbols.pause.unwrap()
            } else {
                symbols.stop
            };
            let code = unsafe { stop(handle.raw, error.as_mut_ptr(), error.len()) };
            ensure!(code == 0, "native audio stop/pause failed");
            stopping = true;
        }
        let mut frames = 0u32;
        let mut rms = 0.0f32;
        let code = unsafe {
            (symbols.read)(
                handle.raw,
                pcm.as_mut_ptr(),
                pcm.len() as u32,
                &mut frames,
                &mut rms,
                50,
                error.as_mut_ptr(),
                error.len(),
            )
        };
        match code {
            0 => continue,
            1 => {
                observe_native(symbols, handle.raw);
                ensure!(
                    frames as usize <= pcm.len(),
                    "native audio returned invalid frame count"
                );
                let mut bytes = Vec::with_capacity(frames as usize * 2);
                for sample in &pcm[..frames as usize] {
                    bytes.extend_from_slice(&sample.to_le_bytes());
                }
                if cancel.is_cancelled() {
                    return Ok(false);
                }
                for chunk in bytes.chunks(crate::BLOCK_BYTES) {
                    tx.blocking_send(Input::Pcm(Bytes::copy_from_slice(chunk)))
                        .context("session closed")?;
                }
            }
            2 => break,
            _ => bail!("native audio read failed"),
        }
    }
    let drops = unsafe { (symbols.dropped)(handle.raw) };
    let faults = unsafe { (symbols.faults)(handle.raw) };
    ensure!(
        drops == 0 && faults == 0,
        "native audio reported {drops} dropped blocks and {faults} runtime faults"
    );
    Ok(!dsp_changed)
}
