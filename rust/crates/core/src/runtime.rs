// SPDX-License-Identifier: GPL-3.0-only
use crate::{
    audio::{self, NativeAudio, Source},
    protocol::{self, RealtimeConfig, Transcript},
    recording::{Recording, RecordingStore, RecordingWriter},
};
use anyhow::{Context, Result, bail, ensure};
use bytes::Bytes;
use serde::{Deserialize, Serialize};
use std::{
    future::pending,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
    time::Duration,
};
use tokio::{
    sync::{mpsc, watch},
    task::JoinHandle,
    time::{Instant, timeout},
};
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const SEND_TIMEOUT: Duration = Duration::from_secs(10);
const COMPLETION_TIMEOUT: Duration = Duration::from_secs(120);
const SOURCE_START_TIMEOUT: Duration = Duration::from_secs(3);
const NATIVE_STOP_TIMEOUT: Duration = Duration::from_millis(500);

#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    #[default]
    Idle,
    Starting,
    Recording,
    Finishing,
    Committing,
    Completed,
    Cancelled,
    Failed,
}
impl Phase {
    pub fn terminal(&self) -> bool {
        matches!(
            self,
            Self::Completed | Self::Cancelled | Self::Failed | Self::Idle
        )
    }
}

#[derive(Clone, Default, Debug, Serialize, Deserialize)]
pub struct StartupTiming {
    pub source_ready_ms: Option<f64>,
    pub first_pcm_ms: Option<f64>,
    pub asr_ready_ms: Option<f64>,
}

#[derive(Clone, Default, Debug, Serialize, Deserialize)]
pub struct Status {
    pub generation: u64,
    pub phase: Phase,
    pub recording_id: Option<Uuid>,
    /// In-process sealed/terminal handoff; omitted from RPC status frames.
    #[serde(skip)]
    pub recording: Option<Arc<Recording>>,
    pub pcm_bytes: u64,
    pub input_queue_high_watermark: usize,
    pub asr_queue_high_watermark: usize,
    pub asr_ready: bool,
    #[serde(default)]
    pub startup_timing_ms: StartupTiming,
    pub native_busy: bool,
    pub native_quarantined: bool,
    pub transcript: String,
    pub partial_text: String,
    pub raw_transcript: String,
    pub audio_rms: f64,
    pub usage: serde_json::Value,
    pub error: Option<String>,
}

#[derive(Clone, Default, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct StartRequest {
    #[serde(default)]
    pub source: Source,
    #[serde(default)]
    pub asr: Option<RealtimeConfig>,
}

pub(crate) enum Input {
    Ready,
    Pcm(Bytes),
    Finish,
    Fault(String),
}
pub enum NetworkInput {
    Pcm(Bytes),
    Finish,
}

/// An application transport plugs into the same bounded capture/archive owner.
/// The core does not depend on provider SDKs or application configuration.
pub type ExternalAsrRunner = Box<
    dyn FnOnce(
            mpsc::Receiver<NetworkInput>,
            CancellationToken,
            NetworkReporter,
        ) -> futures_util::future::BoxFuture<'static, Result<String>>
        + Send,
>;

pub struct ExternalAsr {
    pub model: String,
    pub audio_limit_bytes: Option<u64>,
    pub run: ExternalAsrRunner,
}

#[derive(Clone)]
pub struct NetworkReporter {
    started_at: Instant,
    ready: Arc<AtomicBool>,
    state: watch::Sender<Status>,
}

impl NetworkReporter {
    pub fn ready(&self) {
        self.ready.store(true, Ordering::Release);
        self.state.send_modify(|s| {
            s.asr_ready = true;
            s.startup_timing_ms
                .asr_ready_ms
                .get_or_insert(self.started_at.elapsed().as_secs_f64() * 1000.0);
        });
    }
    pub fn partial(&self, text: &str) {
        if text.len() <= 256 * 1024 {
            self.state.send_modify(|s| s.partial_text = text.into());
        }
    }
    pub fn transcript(&self, text: &str) {
        if text.len() <= 256 * 1024 {
            self.state.send_modify(|s| s.raw_transcript = text.into());
        }
    }
    pub fn usage(&self, usage: serde_json::Value) {
        self.state.send_modify(|s| s.usage = usage);
    }
}

struct Active {
    generation: u64,
    source: Source,
    input: mpsc::Sender<Input>,
    cancel: CancellationToken,
    stop: CancellationToken,
    // The acceptance barrier makes "cancel accepted" mutually exclusive with
    // publishing a successful terminal commit.
    committing: Arc<Mutex<bool>>,
    input_high_watermark: Arc<AtomicUsize>,
    asr_ready: Arc<AtomicBool>,
    state: watch::Receiver<Status>,
    task: JoinHandle<()>,
    finishing: bool,
}

pub struct Host {
    store: RecordingStore,
    native: Option<NativeAudio>,
    api_key: Option<Arc<str>>,
    next_generation: u64,
    active: Option<Active>,
}

impl Drop for Host {
    fn drop(&mut self) {
        if let Some(active) = &self.active {
            // Async owners should still call shutdown() to wait for persistence.
            // A dropped owner must not leave a stream/capture running forever.
            if let Ok(committing) = active.committing.lock()
                && !*committing
            {
                active.cancel.cancel();
            }
            active.stop.cancel();
        }
    }
}

impl Host {
    pub fn new(
        store: RecordingStore,
        native: Option<NativeAudio>,
        api_key: Option<String>,
    ) -> Self {
        Self {
            store,
            native,
            api_key: api_key.map(Arc::from),
            next_generation: 0,
            active: None,
        }
    }

    pub fn store(&self) -> &RecordingStore {
        &self.store
    }
    pub fn native_loaded(&self) -> bool {
        self.native.is_some()
    }

    pub fn status(&self) -> Status {
        let mut status = self
            .active
            .as_ref()
            .map(|a| a.state.borrow().clone())
            .unwrap_or_default();
        if let Some(active) = &self.active {
            status.input_queue_high_watermark = active.input_high_watermark.load(Ordering::Relaxed);
            status.asr_ready = active.asr_ready.load(Ordering::Acquire);
        }
        status.native_busy = self.native.as_ref().is_some_and(NativeAudio::busy);
        status.native_quarantined = self.native.as_ref().is_some_and(NativeAudio::quarantined);
        status
    }

    pub async fn start(&mut self, request: StartRequest) -> Result<Status> {
        self.start_inner(request, None).await
    }

    pub async fn start_external(&mut self, source: Source, asr: ExternalAsr) -> Result<Status> {
        ensure!(
            !asr.model.is_empty() && asr.model.len() <= 256,
            "invalid ASR model"
        );
        self.start_inner(StartRequest { source, asr: None }, Some(asr))
            .await
    }

    pub fn subscribe(&self) -> Option<watch::Receiver<Status>> {
        self.active.as_ref().map(|active| active.state.clone())
    }

    async fn start_inner(
        &mut self,
        request: StartRequest,
        external: Option<ExternalAsr>,
    ) -> Result<Status> {
        let started_at = Instant::now();
        if let Some(active) = &self.active {
            ensure!(
                active.state.borrow().phase.terminal(),
                "a session is already active"
            );
        }
        if let Some(asr) = &request.asr {
            asr.validate()?;
        }
        if let Some(dsp) = request.source.native_dsp() {
            dsp.validate()?;
            let native = self
                .native
                .as_ref()
                .context("native input requires --native-library")?;
            ensure!(
                !native.quarantined(),
                "native cleanup failed; restart host before capturing again"
            );
            ensure!(!native.busy(), "native cleanup is still in flight");
        }
        if let Some(active) = self.active.take() {
            active
                .task
                .await
                .context("previous session worker failed")?;
        }
        self.next_generation = self
            .next_generation
            .checked_add(1)
            .context("session generation overflow")?;
        let generation = self.next_generation;
        let model = request
            .asr
            .as_ref()
            .map(|a| a.model.as_str())
            .or_else(|| external.as_ref().map(|a| a.model.as_str()))
            .unwrap_or("local-recording");
        let writer = self.store.reserve(generation, model)?;
        let initial = Status {
            generation,
            phase: Phase::Starting,
            recording_id: Some(writer.id()),
            ..Default::default()
        };
        let (state_tx, state_rx) = watch::channel(initial);
        let (input_tx, input_rx) = mpsc::channel(crate::AUDIO_QUEUE_BLOCKS);
        let cancel = CancellationToken::new();
        let stop = CancellationToken::new();
        let committing = Arc::new(Mutex::new(false));
        let asr_ready = Arc::new(AtomicBool::new(false));
        let input_high_watermark = Arc::new(AtomicUsize::new(0));
        let context = Session {
            started_at,
            source: request.source.clone(),
            asr: request.asr,
            external,
            api_key: self.api_key.clone(),
            native: self.native.clone(),
            input_tx: input_tx.clone(),
            input_rx,
            cancel: cancel.clone(),
            stop: stop.clone(),
            committing: committing.clone(),
            asr_ready: asr_ready.clone(),
            input_high_watermark: input_high_watermark.clone(),
            state: state_tx,
            writer,
        };
        let task = tokio::spawn(context.run());
        self.active = Some(Active {
            generation,
            source: request.source,
            input: input_tx,
            cancel,
            stop,
            committing,
            input_high_watermark,
            asr_ready,
            state: state_rx,
            task,
            finishing: false,
        });
        Ok(self.status())
    }

    fn active_for(&mut self, generation: u64) -> Result<&mut Active> {
        let active = self.active.as_mut().context("no active session")?;
        ensure!(generation == active.generation, "stale session generation");
        ensure!(
            !active.task.is_finished() && !active.state.borrow().phase.terminal(),
            "session has already ended"
        );
        ensure!(
            !active.cancel.is_cancelled(),
            "session is cancelling or closing"
        );
        Ok(active)
    }

    pub fn append(&mut self, generation: u64, pcm: Bytes) -> Result<()> {
        ensure!(
            !pcm.is_empty() && pcm.len() <= crate::BLOCK_BYTES && pcm.len().is_multiple_of(2),
            "PCM block must contain 1–640 complete mono PCM16 frames"
        );
        let active = self.active_for(generation)?;
        ensure!(
            matches!(active.source, Source::Stream),
            "append is only available for stream input"
        );
        ensure!(!active.finishing, "session input is already finished");
        active
            .input
            .try_send(Input::Pcm(pcm))
            .map_err(|error| match error {
                mpsc::error::TrySendError::Full(_) => anyhow::anyhow!(
                    "input queue full; block was not accepted; retry with backpressure"
                ),
                mpsc::error::TrySendError::Closed(_) => anyhow::anyhow!("session input is closed"),
            })?;
        active.input_high_watermark.fetch_max(
            crate::AUDIO_QUEUE_BLOCKS - active.input.capacity(),
            Ordering::Relaxed,
        );
        Ok(())
    }

    pub fn finish(&mut self, generation: u64) -> Result<()> {
        let active = self.active_for(generation)?;
        if active.finishing {
            return Ok(());
        }
        if matches!(active.source, Source::Stream) {
            active
                .input
                .try_send(Input::Finish)
                .context("input queue full or closed; finish was not accepted")?;
        } else {
            active.stop.cancel();
        }
        active.finishing = true;
        Ok(())
    }

    pub fn cancel(&mut self, generation: u64) -> Result<()> {
        let active = self.active.as_mut().context("no active session")?;
        ensure!(generation == active.generation, "stale session generation");
        if active.state.borrow().phase == Phase::Cancelled {
            return Ok(());
        }
        let committing = active
            .committing
            .lock()
            .map_err(|_| anyhow::anyhow!("commit barrier poisoned"))?;
        ensure!(
            !*committing && !active.task.is_finished(),
            "session has entered terminal commit; cancellation was not accepted"
        );
        active.cancel.cancel();
        active.stop.cancel();
        Ok(())
    }

    pub async fn shutdown(&mut self) -> Result<()> {
        let Some(active) = self.active.take() else {
            return Ok(());
        };
        {
            let committing = active
                .committing
                .lock()
                .map_err(|_| anyhow::anyhow!("commit barrier poisoned"))?;
            if !*committing {
                active.cancel.cancel();
            }
        }
        active.stop.cancel();
        let mut task = active.task;
        match timeout(Duration::from_secs(5), &mut task).await {
            Ok(result) => result.context("session worker failed during shutdown")?,
            Err(_) => {
                task.abort();
                bail!(
                    "shutdown deadline exceeded; unfinished recording remains recoverable on next launch"
                )
            }
        }
        Ok(())
    }
}

struct Session {
    started_at: Instant,
    source: Source,
    asr: Option<RealtimeConfig>,
    external: Option<ExternalAsr>,
    api_key: Option<Arc<str>>,
    native: Option<NativeAudio>,
    input_tx: mpsc::Sender<Input>,
    input_rx: mpsc::Receiver<Input>,
    cancel: CancellationToken,
    stop: CancellationToken,
    committing: Arc<Mutex<bool>>,
    asr_ready: Arc<AtomicBool>,
    input_high_watermark: Arc<AtomicUsize>,
    state: watch::Sender<Status>,
    writer: RecordingWriter,
}

impl Session {
    async fn run(mut self) {
        let source_cancel = CancellationToken::new();
        let network_cancel = self.cancel.child_token();
        let (network_tx, network_rx) = mpsc::channel(crate::AUDIO_QUEUE_BLOCKS);
        let recover_network_failure = self.external.is_some();
        let cloud_audio_limit = self
            .external
            .as_ref()
            .and_then(|a| a.audio_limit_bytes)
            .or_else(|| {
                self.asr
                    .as_ref()
                    .and_then(RealtimeConfig::cloud_audio_limit_bytes)
            });
        let reporter = NetworkReporter {
            started_at: self.started_at,
            ready: self.asr_ready.clone(),
            state: self.state.clone(),
        };
        let mut network = if let Some(external) = self.external.take() {
            Some(tokio::spawn((external.run)(
                network_rx,
                network_cancel.clone(),
                reporter,
            )))
        } else {
            self.asr.clone().map(|config| {
                tokio::spawn(run_network(
                    config,
                    self.api_key.clone(),
                    network_rx,
                    network_cancel.clone(),
                    reporter,
                ))
            })
        };
        // Queue admission is synchronous; connection readiness is not capture
        // admission. Start both now and retain the beginning in the bounded
        // sender queue/archive while the provider performs its handshake.
        let mut outcome = async {
            audio::spawn(
                self.source.clone(),
                self.input_tx.clone(),
                self.stop.clone(),
                source_cancel.clone(),
                self.native.clone(),
            )?;
            self.drive(
                &network_tx,
                &mut network,
                cloud_audio_limit,
                recover_network_failure,
            )
            .await
        }
        .await;
        // Stop admissions and wake any producer waiting on the bounded queue.
        source_cancel.cancel();
        self.stop.cancel();
        self.input_rx.close();
        network_cancel.cancel();
        if let Some(network) = network {
            network.abort();
            let _ = network.await;
        }

        // Accepted queued PCM is retained even if the transport failed. This is
        // a bounded drain (160 blocks), never an unbounded retry or network call.
        while let Ok(input) = self.input_rx.try_recv() {
            if let Input::Pcm(pcm) = input
                && let Err(error) = self.writer.append(&pcm).await
            {
                outcome = Err(error);
                break;
            }
        }
        let cancelled = {
            let mut committing = self.committing.lock().expect("commit barrier");
            *committing = true;
            self.cancel.is_cancelled()
        };
        self.cancel.cancel();
        let (phase, status, text, error) = if cancelled {
            (Phase::Cancelled, "cancelled", String::new(), None)
        } else {
            match outcome {
                Ok(text) => (Phase::Completed, "completed", text, None),
                Err(error) => (
                    Phase::Failed,
                    "failed",
                    String::new(),
                    Some(error.to_string()),
                ),
            }
        };
        self.state.send_modify(|s| {
            s.phase = Phase::Committing;
            s.pcm_bytes = self.writer.pcm_bytes();
            // Internal preparation only; public terminal text stays empty until
            // finish() durably commits. The application must not publish early.
            s.recording = self.writer.sealed_record().map(|mut record| {
                record.status = status.into();
                record.transcript = text.clone();
                record.error = error.clone();
                Arc::new(record)
            });
        });
        match self.writer.finish(status, text, error.clone()).await {
            Ok(record) => self.state.send_modify(|s| {
                s.recording = Some(Arc::new(record.clone()));
                s.phase = phase;
                s.pcm_bytes = record.pcm_bytes;
                s.transcript = record.transcript;
                s.error = error;
            }),
            Err(error) => self.state.send_modify(|s| {
                s.phase = Phase::Failed;
                s.transcript.clear();
                s.error = Some(format!("recording commit failed: {error}"));
            }),
        }
    }

    async fn drive(
        &mut self,
        network_tx: &mpsc::Sender<NetworkInput>,
        network: &mut Option<JoinHandle<Result<String>>>,
        cloud_audio_limit: Option<u64>,
        recover_network_failure: bool,
    ) -> Result<String> {
        let native = self.source.is_native();
        let mut network_error = None;
        let mut ready = matches!(self.source, Source::Stream);
        let mut finished = false;
        let start_deadline = Instant::now() + SOURCE_START_TIMEOUT;
        let mut audio_deadline = start_deadline;
        let mut stop_deadline = None;
        if ready {
            self.state.send_modify(|s| s.phase = Phase::Recording);
        }
        loop {
            enum Event {
                Input(Option<Input>),
                Network(Result<String>),
                Stop,
                Timeout,
                Cancel,
                Stalled,
            }
            let event = tokio::select! {
                biased;
                _ = self.cancel.cancelled() => Event::Cancel,
                _ = tokio::time::sleep_until(audio_deadline), if native && ready && !finished && stop_deadline.is_none() => Event::Stalled,
                value = self.input_rx.recv(), if !finished => Event::Input(value),
                value = async {
                    match network.as_mut() { Some(task) => task.await.context("realtime worker failed")?, None => pending().await }
                } => Event::Network(value),
                _ = self.stop.cancelled(), if native && stop_deadline.is_none() && !finished => Event::Stop,
                _ = tokio::time::sleep_until(start_deadline), if !ready => Event::Timeout,
                _ = async { match stop_deadline { Some(deadline) => tokio::time::sleep_until(deadline).await, None => pending().await } } => Event::Timeout,
            };
            match event {
                Event::Cancel => bail!("session cancelled"),
                Event::Stalled => bail!(
                    "native audio produced no PCM for 3 seconds; capture cleanup remains isolated"
                ),
                Event::Timeout => bail!(if ready {
                    "native drain deadline exceeded; capture cleanup remains isolated"
                } else {
                    "audio source startup deadline exceeded"
                }),
                Event::Stop => {
                    stop_deadline = Some(Instant::now() + NATIVE_STOP_TIMEOUT);
                }
                Event::Network(result) => {
                    *network = None;
                    if !finished && recover_network_failure {
                        let error = result.err().unwrap_or_else(|| {
                            anyhow::anyhow!("provider completed before audio input finished")
                        });
                        self.state
                            .send_modify(|s| s.error = Some(format!("realtime degraded: {error}")));
                        network_error = Some(error);
                        continue;
                    }
                    ensure!(
                        finished || result.is_err(),
                        "provider completed before audio input finished"
                    );
                    return result;
                }
                Event::Input(Some(Input::Ready)) => {
                    audio_deadline = Instant::now() + SOURCE_START_TIMEOUT;
                    ready = true;
                    self.state.send_modify(|s| {
                        s.startup_timing_ms.source_ready_ms =
                            Some(self.started_at.elapsed().as_secs_f64() * 1000.0);
                        if !native {
                            s.phase = Phase::Recording;
                        }
                    });
                }
                Event::Input(Some(Input::Pcm(pcm))) => {
                    audio_deadline = Instant::now() + SOURCE_START_TIMEOUT;
                    ensure!(
                        pcm.len() <= crate::BLOCK_BYTES && pcm.len().is_multiple_of(2),
                        "source produced invalid PCM block"
                    );
                    self.input_high_watermark
                        .fetch_max(self.input_rx.len(), Ordering::Relaxed);
                    let energy = pcm
                        .chunks_exact(2)
                        .map(|p| (i16::from_le_bytes([p[0], p[1]]) as f64 / 32768.0).powi(2))
                        .sum::<f64>();
                    let rms = (energy / (pcm.len() / 2).max(1) as f64).sqrt();
                    self.state.send_modify(|s| {
                        s.audio_rms = rms;
                        s.startup_timing_ms
                            .first_pcm_ms
                            .get_or_insert(self.started_at.elapsed().as_secs_f64() * 1000.0);
                        if s.phase == Phase::Starting {
                            s.phase = Phase::Recording;
                        }
                    });
                    // Audio readiness describes the observed input, not disk
                    // fsync completion. The bounded source queue retains input
                    // while first-use archive creation runs on the I/O pool.
                    self.writer.append(&pcm).await?;
                    self.state
                        .send_modify(|s| s.pcm_bytes = self.writer.pcm_bytes());
                    if network.is_some() {
                        ensure!(
                            cloud_audio_limit.is_none_or(|limit| self.writer.pcm_bytes() <= limit),
                            "cloud model audio context limit reached; audio saved; split into shorter sessions"
                        );
                        if matches!(self.source, Source::Wav { .. }) {
                            self.send_network(network_tx, NetworkInput::Pcm(pcm))
                                .await?;
                        } else if let Err(error) = network_tx.try_send(NetworkInput::Pcm(pcm)) {
                            if matches!(error, mpsc::error::TrySendError::Closed(_)) {
                                // Disk creation may overlap a provider failure.
                                // Preserve that failure rather than replacing it
                                // with the sender's secondary channel error.
                                let result =
                                    network.take().unwrap().await.context("ASR worker failed")?;
                                let failure = result.err().unwrap_or_else(|| {
                                    anyhow::anyhow!(
                                        "provider completed before audio input finished"
                                    )
                                });
                                if recover_network_failure {
                                    self.state.send_modify(|s| {
                                        s.error = Some(format!("realtime degraded: {failure}"))
                                    });
                                    network_error = Some(failure);
                                    continue;
                                }
                                return Err(failure);
                            }
                            if recover_network_failure {
                                if let Some(task) = network.take() {
                                    task.abort();
                                }
                                network_error = Some(anyhow::anyhow!(
                                    "ASR queue full or closed; retry from archived audio"
                                ));
                                self.state.send_modify(|s| {
                                    s.error = Some("realtime degraded; audio retained".into())
                                });
                            } else {
                                return Err(error).context("ASR queue full or closed; recording stopped with recoverable audio");
                            }
                        }
                        self.state.send_modify(|s| {
                            s.asr_queue_high_watermark = s
                                .asr_queue_high_watermark
                                .max(crate::AUDIO_QUEUE_BLOCKS - network_tx.capacity())
                        });
                    }
                }
                Event::Input(Some(Input::Finish)) => {
                    finished = true;
                    stop_deadline = None;
                    self.input_rx.close();
                    self.state.send_modify(|s| s.phase = Phase::Finishing);
                    if network.is_none() {
                        return network_error.map_or_else(|| Ok(String::new()), Err);
                    }
                    ensure!(
                        self.writer.pcm_bytes() >= 3_200,
                        "realtime recording must contain at least 100 ms of audio"
                    );
                    self.send_network(network_tx, NetworkInput::Finish).await?;
                    // No producer can append after this FIFO boundary. Overlap
                    // the durable WAV seal with provider response latency.
                    self.writer.seal_audio().await?;
                }
                Event::Input(Some(Input::Fault(error))) => bail!(error),
                Event::Input(None) => bail!("audio source closed without finish"),
            }
        }
    }

    async fn send_network(
        &mut self,
        tx: &mpsc::Sender<NetworkInput>,
        input: NetworkInput,
    ) -> Result<()> {
        tokio::select! {
            biased;
            _ = self.cancel.cancelled() => bail!("session cancelled"),
            result = tx.send(input) => result.context("ASR closed; recording stopped with recoverable audio"),
        }
    }
}

async fn run_network(
    config: RealtimeConfig,
    api_key: Option<Arc<str>>,
    mut input: mpsc::Receiver<NetworkInput>,
    cancel: CancellationToken,
    reporter: NetworkReporter,
) -> Result<String> {
    tokio::select! {
        biased;
        _ = cancel.cancelled() => bail!("realtime session cancelled"),
        result = async {
            let mut socket = timeout(CONNECT_TIMEOUT, protocol::connect(&config, api_key.as_deref())).await.context("realtime connect deadline exceeded")??;
            timeout(SEND_TIMEOUT, protocol::send(&mut socket, protocol::session_update(&config))).await.context("session.update send timeout")??;
            timeout(CONNECT_TIMEOUT, async {
                loop {
                    let value = protocol::receive(&mut socket).await?;
                    if value["type"] == "session.updated" { return Ok::<_, anyhow::Error>(()) }
                    Transcript::default().consume(&value, false)?;
                }
            }).await.context("session.updated deadline exceeded")??;
            reporter.ready();
            let mut pending_pcm = Vec::with_capacity(crate::NETWORK_FRAME_BYTES);
            let mut transcript = Transcript::default();
            let mut committed = false;
            let mut completion_deadline = None;
            let mut heartbeat = tokio::time::interval(Duration::from_secs(15));
            heartbeat.tick().await;
            loop {
                enum Event { Input(Option<NetworkInput>), Server(serde_json::Value), Heartbeat, Timeout }
                let event = tokio::select! {
                    value = input.recv(), if !committed => Event::Input(value),
                    value = protocol::receive(&mut socket) => Event::Server(value?),
                    _ = heartbeat.tick() => Event::Heartbeat,
                    _ = async { match completion_deadline { Some(deadline) => tokio::time::sleep_until(deadline).await, None => pending().await } } => Event::Timeout,
                };
                match event {
                    Event::Timeout => bail!("realtime response completion deadline exceeded"),
                    Event::Heartbeat => {
                        use futures_util::SinkExt;
                        timeout(SEND_TIMEOUT, socket.send(tokio_tungstenite::tungstenite::Message::Ping(Bytes::new()))).await.context("heartbeat timeout")??;
                    },
                    Event::Input(Some(NetworkInput::Pcm(pcm))) => {
                        let mut remaining = pcm.as_ref();
                        while !remaining.is_empty() {
                            let count = remaining.len().min(crate::NETWORK_FRAME_BYTES - pending_pcm.len());
                            pending_pcm.extend_from_slice(&remaining[..count]); remaining = &remaining[count..];
                            if pending_pcm.len() == crate::NETWORK_FRAME_BYTES {
                                timeout(SEND_TIMEOUT, protocol::send(&mut socket, protocol::append_event(&pending_pcm))).await.context("audio send timeout")??;
                                pending_pcm.clear();
                            }
                        }
                    },
                    Event::Input(Some(NetworkInput::Finish)) => {
                        if !pending_pcm.is_empty() {
                            timeout(SEND_TIMEOUT, protocol::send(&mut socket, protocol::append_event(&pending_pcm))).await.context("tail audio send timeout")??;
                            pending_pcm.clear();
                        }
                        timeout(SEND_TIMEOUT, async {
                            protocol::send(&mut socket, protocol::command("input_audio_buffer.commit")).await?;
                            protocol::send(&mut socket, protocol::command("response.create")).await
                        }).await.context("commit send timeout")??;
                        committed = true;
                        completion_deadline = Some(Instant::now() + COMPLETION_TIMEOUT);
                    },
                    Event::Input(None) => bail!("audio sender closed without commit"),
                    Event::Server(value) => {
                        if transcript.consume(&value, committed)? {
                            // Consumed sessions are never reused with prior audio/context.
                            let _ = timeout(Duration::from_millis(500), socket.close(None)).await;
                            return Ok(transcript.text().to_owned())
                        }
                    },
                }
            }
        } => result,
    }
}

pub async fn wait_terminal(host: &Host, deadline: Duration) -> Result<Status> {
    timeout(deadline, async {
        loop {
            let status = host.status();
            if status.phase.terminal() {
                return status;
            }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .context("session did not reach terminal state")
}

pub async fn records(host: &Host) -> Result<Vec<Recording>> {
    host.store.list().await
}
