// SPDX-License-Identifier: GPL-3.0-only
//! One command owner, bounded messages, independent cancellable network jobs.
use crate::{
    billing,
    catalog::CONTRACT,
    config::{Config, ConfigRepository},
    dictionary::{self, Dictionary},
    history::{History, PreparedHistory},
    http::Completion,
    learning::{self, Decision},
    learning_store::{Job, LearningStore},
    observation::{Observation, Progress},
    provider::{Endpoints, Provider},
    text,
    workflow::{self, Outcome},
};
use anyhow::{Context, Result, bail, ensure};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    path::PathBuf,
    sync::Arc,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tokio::{
    sync::{broadcast, mpsc, oneshot},
    task::JoinSet,
};
use tokio_util::sync::CancellationToken;
use uuid::Uuid;
use vocal_more_core::{
    audio::{DeviceOptions, Dsp, NativeAudio, Source},
    recording::RecordingStore,
    runtime::{Host, Phase, StartRequest, Status},
};

#[derive(Clone)]
pub struct Options {
    pub data_dir: PathBuf,
    pub native: Option<NativeAudio>,
    pub endpoints: Endpoints,
    pub environment_key: Option<String>,
    pub allow_test_sources: bool,
    pub import_from: Option<PathBuf>,
}
impl Options {
    pub fn new(data_dir: PathBuf) -> Self {
        Self {
            data_dir,
            native: None,
            endpoints: Endpoints::default(),
            environment_key: None,
            allow_test_sources: false,
            import_from: None,
        }
    }
}
#[derive(Clone)]
pub struct Application {
    commands: mpsc::Sender<Command>,
    events: broadcast::Sender<Value>,
}
struct Command {
    method: String,
    params: Value,
    reply: oneshot::Sender<Result<Value>>,
}
enum Internal {
    Core(u64, Status),
    Finished(u64, Result<Outcome>),
    MicStop(u64),
    Stage(u64, String),
    Partial(u64, String),
    Retry(String, Result<Completion>),
    Compact(Result<Value>),
    CompactReady,
    Models(Value),
    Learning(Box<Job>, Result<Decision>),
    WakeLearning,
    ObservationPoll(String),
}
struct Active {
    generation: u64,
    core_generation: u64,
    cancel: CancellationToken,
    provider: Provider,
    mode: String,
    microphone_test: bool,
    mic_started: bool,
    core_done: bool,
    prepared: Option<(Outcome, PreparedHistory)>,
    streamed: String,
    partial: String,
}
struct State {
    cached_devices: Value,
    options: Options,
    config: ConfigRepository,
    dictionary: Dictionary,
    learning: LearningStore,
    host: Host,
    preview: Host,
    _preview_dir: tempfile::TempDir,
    preview_path: Option<PathBuf>,
    history: History,
    active: Option<Active>,
    state: String,
    mode: String,
    generation: u64,
    pressed_at: Option<Instant>,
    latched: bool,
    events: broadcast::Sender<Value>,
    internal: mpsc::Sender<Internal>,
    tasks: JoinSet<()>,
    shutdown: CancellationToken,
    retry: Option<(String, CancellationToken)>,
    compacting: bool,
    compact_again: bool,
    checking_models: bool,
    learning_busy: bool,
    pending_pastes: HashMap<String, Value>,
    platform_status: Value,
    claimed_paste: Option<Value>,
    last_result: Value,
    paste_blocked: bool,
    observation: Option<Observation>,
    started_at: Instant,
}
fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}
fn required<'a>(params: &'a Value, key: &str) -> Result<&'a str> {
    params[key]
        .as_str()
        .filter(|s| !s.is_empty())
        .with_context(|| format!("{key} is required"))
}
fn dsp(config: &Config) -> Dsp {
    Dsp {
        automatic_gain: config.get("audio.gain_mode") == "automatic",
        gain: config.get("audio.gain").as_f64().unwrap() as f32,
        highpass_enabled: config.get("audio.highpass_filter") == true,
        highpass_hz: config.get("audio.highpass_freq").as_f64().unwrap() as f32,
        soft_limiter: config.get("audio.soft_limiter") == true,
    }
}

impl Application {
    pub async fn open(mut options: Options) -> Result<Self> {
        if let Some(source) = &options.import_from {
            crate::migration::import_python(source, &options.data_dir).await?;
        }
        std::fs::create_dir_all(&options.data_dir)?;
        let store = RecordingStore::open(options.data_dir.join("recordings")).await?;
        let history = History::open(store.clone()).await?;
        let mut config = ConfigRepository::open(&options.data_dir.join("config.yaml"))?;
        if !config.path.exists() {
            crate::persistence::write_yaml(&config.path, &config.config.0)?;
        }
        // Match Python startup: environment credentials initialize the live
        // config; later explicit UI edits own that value until the next launch.
        if let Some(key) = options
            .environment_key
            .take()
            .filter(|s| !s.trim().is_empty())
        {
            config.config.apply_update("api_key", &json!(key))?;
        }
        let mut dictionary = Dictionary::open(&options.data_dir.join("dictionary.yaml"))?;
        let mut learning =
            LearningStore::open(&options.data_dir.join("dictionary-learning.sqlite3"), now())?;
        learning.recover_undos(&mut dictionary, now())?;
        let preview_dir = tempfile::tempdir_in(&options.data_dir)?;
        let preview_store = RecordingStore::open(preview_dir.path().join("recordings")).await?;
        let (commands, mut receiver) = mpsc::channel::<Command>(64);
        let (events, _) = broadcast::channel(256);
        let (internal, mut incoming) = mpsc::channel(128);
        let cached_devices = options
            .native
            .as_ref()
            .map(NativeAudio::list_devices)
            .transpose()?
            .unwrap_or(json!([]));
        let mut state = State {
            cached_devices,
            host: Host::new(store, options.native.clone(), None),
            preview: Host::new(preview_store, options.native.clone(), None),
            mode: config.config.get("default_mode").as_str().unwrap().into(),
            config,
            dictionary,
            learning,
            options,
            _preview_dir: preview_dir,
            preview_path: None,
            history,
            active: None,
            state: "idle".into(),
            generation: 0,
            pressed_at: None,
            latched: false,
            events: events.clone(),
            internal,
            tasks: JoinSet::new(),
            shutdown: CancellationToken::new(),
            retry: None,
            compacting: false,
            compact_again: false,
            checking_models: false,
            learning_busy: false,
            pending_pastes: HashMap::new(),
            platform_status: json!({}),
            claimed_paste: None,
            last_result: Value::Null,
            paste_blocked: false,
            observation: None,
            started_at: Instant::now(),
        };
        state.prepare_audio();
        state.schedule_compaction();
        state.schedule_learning(Duration::ZERO);
        tokio::spawn(async move {
            loop {
                tokio::select! {
                    command=receiver.recv()=>{
                        let Some(command)=command else {break};
                        let closing=command.method=="shutdown";
                        let result=state.dispatch(&command.method,command.params).await;
                        let _=command.reply.send(result);
                        if closing {break}
                    },
                    message=incoming.recv()=>{if let Some(message)=message && let Err(error)=state.handle_internal(message).await {state.emit("error",json!({"message":error.to_string()}));}},
                    _=state.tasks.join_next(),if !state.tasks.is_empty()=>{},
                }
            }
            state.close().await;
        });
        Ok(Self { commands, events })
    }
    pub fn subscribe(&self) -> broadcast::Receiver<Value> {
        self.events.subscribe()
    }
    pub async fn call(&self, method: &str, params: Value) -> Result<Value> {
        ensure!(params.is_object(), "params must be an object");
        let (reply, result) = oneshot::channel();
        self.commands
            .send(Command {
                method: method.into(),
                params,
                reply,
            })
            .await
            .context("backend is closed")?;
        result.await.context("backend stopped before replying")?
    }
}
impl State {
    fn emit(&self, method: &str, params: Value) {
        let _ = self
            .events
            .send(json!({"jsonrpc":"2.0","method":method,"params":params}));
    }
    fn change_state(&mut self, state: &str) {
        if self.state != state {
            self.state = state.into();
            self.emit("state_changed",json!({"state":state,"generation":self.generation,"current_mode":self.mode,"latched":self.latched,"microphone_test":self.active.as_ref().is_some_and(|a|a.microphone_test)}));
            self.emit("audio_input_status", self.input_status());
        }
    }
    fn provider(&self, context: &str) -> Provider {
        Provider::new(
            self.config.config.clone(),
            self.dictionary.entries.clone(),
            context,
            self.options.endpoints.clone(),
            self.options.environment_key.as_deref(),
        )
    }
    fn public_config(&self) -> Value {
        self.config.config.public()
    }
    fn config_changed(&mut self) {
        if self.active.is_none() {
            self.mode = self
                .config
                .config
                .get("default_mode")
                .as_str()
                .unwrap()
                .into();
        }
        self.emit(
            "config_changed",
            json!({"config":self.public_config(),"api_key_set":self.key_present()}),
        );
        self.emit("audio_input_status", self.input_status());
        self.prepare_audio();
        self.schedule_learning(Duration::ZERO);
        if self
            .observation
            .as_ref()
            .is_some_and(|o| !self.learning_allowed(&json!({"app_bundle_id":o.app_bundle_id()})))
        {
            self.end_observation(None);
        }
    }
    fn key_present(&self) -> bool {
        self.options.endpoints.fixture
            || !self
                .config
                .config
                .get("api_key")
                .as_str()
                .unwrap_or("")
                .trim()
                .is_empty()
            || self
                .options
                .environment_key
                .as_ref()
                .is_some_and(|s| !s.trim().is_empty())
    }
    fn devices(&self) -> Result<Value> {
        Ok(self.cached_devices.clone())
    }
    fn prepare_audio(&self) {
        if self.active.is_none()
            && let Some(native) = &self.options.native
        {
            let _ = native.prepare_idle(&self.native_source());
        }
    }
    fn input_status(&self) -> Value {
        let cfg = self
            .active
            .as_ref()
            .map(|a| &a.provider.config)
            .unwrap_or(&self.config.config);
        let devices = self.devices().unwrap_or(json!([]));
        let selected = cfg.get("audio.input_device");
        let device = devices.as_array().and_then(|list| {
            list.iter().find(|d| {
                if selected.is_null() {
                    d["is_default"] == true
                } else {
                    d["name"] == *selected || d["uid"] == *selected
                }
            })
        });
        let native = self
            .options
            .native
            .as_ref()
            .map(NativeAudio::diagnostics)
            .unwrap_or(json!({}));
        let active = native["phase"] == "active" && self.active.is_some();
        let voice = cfg.get("audio.capture_backend") == "voice_processing";
        let automatic = cfg.get("audio.gain_mode") == "automatic";
        let channels = device
            .and_then(|d| d["max_input_channels"].as_u64())
            .unwrap_or(1)
            .min(cfg.get("audio.capture_channels").as_u64().unwrap_or(1))
            .max(1);
        let permission = self
            .options
            .native
            .as_ref()
            .and_then(|n| n.microphone_authorization().ok());
        let software = active && !(voice && automatic);
        let phase = if self.state == "starting" {
            "starting"
        } else if active {
            "active"
        } else if self
            .options
            .native
            .as_ref()
            .is_some_and(NativeAudio::quarantined)
        {
            "failed"
        } else {
            "planned"
        };
        let timing = if self.active.as_ref().is_some_and(|a| a.microphone_test) {
            self.preview.status()
        } else {
            self.host.status()
        };
        let mut status = json!({"session_startup_timing_ms":timing.startup_timing_ms,"warm_prepared":native["warm_prepared"],"phase":phase,"device_name":device.map(|d|d["name"].clone()).unwrap_or(json!("")),
            "system_default":selected.is_null() || device.is_some_and(|d|d["is_default"]==true),
            "max_input_channels":device.map(|d|d["max_input_channels"].clone()).unwrap_or(json!(1)),"capture_channels":channels,
            "processing_mode":if voice {"macos_voice_processing"}else if channels>1 {"vocal_more_array"}else{"standard"},
            "processing_active":active,"array_processing_active":active && !voice && channels>1,
            "echo_cancellation":if voice {if active {"active"}else{"ready"}}else{"unavailable"},
            "requested_gain_mode":cfg.get("audio.gain_mode"),"gain_control":if automatic {if voice {"apple_agc"}else{"software_fallback"}}else{"software"},
            "gain_control_verified":active && (!voice || native["agc_enabled_observed"].as_bool()==Some(automatic)),
            "microphone_permission":match permission{Some(3)=>"authorized",Some(0)=>"not_determined",Some(1)=>"restricted",Some(2)=>"denied",_=>"unknown"},
            "native_backend":"objective_cpp","output_sample_rate_hz":16000,"output_channels":1,"capture_block_frames":cfg.get("audio.blocksize"),
            "software_gain_effective":if software {cfg.get("audio.gain").clone()}else{Value::Null},
            "soft_limiter_effective":active.then_some(software && cfg.get("audio.soft_limiter")==true),
            "highpass_effective":active.then_some(cfg.get("audio.highpass_filter")==true),"fallback_reason":null,
            "last_session":native.get("last_session").cloned().unwrap_or(Value::Null)});
        if active {
            for key in [
                "voice_processing_enabled_observed",
                "agc_enabled_observed",
                "start_verified",
                "warm_reused",
                "diagnostics_fresh",
                "source_sample_rate_hz",
                "first_pcm_observed",
                "startup_timing_ms",
                "queue_dropped_blocks",
                "runtime_fault_count",
            ] {
                status[key] = native.get(key).cloned().unwrap_or(Value::Null);
            }
        }
        status
    }

    fn environment(&self) -> Value {
        let permission = self
            .options
            .native
            .as_ref()
            .and_then(|n| n.microphone_authorization().ok());
        let count = self
            .devices()
            .ok()
            .and_then(|v| v.as_array().map(Vec::len))
            .unwrap_or(0);
        let platform = |key: &str| match self.platform_status[key].as_bool() {
            Some(true) => "ok",
            Some(false) => "error",
            None => "unknown",
        };
        json!([{"key":"api_key","status":if self.key_present(){"ok"}else{"error"},"details":if self.key_present(){"configured"}else{"API key is missing"}},
            {"key":"microphone_permission","status":match permission{Some(3)=>"ok",Some(1|2)=>"error",_=>"unknown"},
                "details":match permission{Some(3)=>"authorized",Some(0)=>"not_determined",Some(1)=>"restricted",Some(2)=>"denied",_=>"unavailable"}},
            {"key":"input_device","status":if permission!=Some(3){"unknown"}else if count>0{"ok"}else{"error"},"details":if permission!=Some(3){"visibility_limited".into()}else{format!("{count} available")}},
            {"key":"accessibility","status":platform("accessibility"),"details":if self.platform_status["accessibility"]==true{"trusted"}else{"missing"}},
            {"key":"hotkey_listener","status":platform("hotkey_listener"),"details":if self.platform_status["hotkey_listener"]==true{"running"}else{"not started"}}])
    }
    fn learning_records(&self) -> Result<Value> {
        Ok(json!(self.learning.list(100)?.into_iter().map(|j|json!({"id":j.id,"status":j.status,"created_at":j.created_at,"updated_at":j.updated_at,
            "term":j.result.as_ref().map(|d|d.term.clone()).unwrap_or_default(),"aliases":j.result.as_ref().map(|d|d.aliases.clone()).unwrap_or_default(),
            "confidence":j.result.as_ref().map(|d|d.confidence),"reason_code":j.result.as_ref().map(|d|d.reason_code.clone()).unwrap_or_default(),
            "error":j.error,"observation_id":j.observation_id,"candidate_index":j.candidate_index,"candidate_count":j.candidate_count,
            "model":j.model,"prompt_version":j.prompt_version})).collect::<Vec<_>>()))
    }
    fn snapshot(&self) -> Result<Value> {
        Ok(
            json!({"version":crate::PRODUCT_VERSION,"runtime":"rust","state":self.state,"current_mode":self.mode,"generation":self.generation,
            "config":self.public_config(),"api_key_set":self.key_present(),"asr_models":CONTRACT["asr_models"],"llm_models":CONTRACT["llm_models"],
            "polish_prompt_presets":CONTRACT["prompt_presets"],"devices":self.devices()?,"dictionary":self.dictionary.entries,
            "dictionary_learning_records":self.learning_records()?,"recordings":self.history.list(),"recording_storage":self.history.storage_summary(),
            "audio_input_status":self.input_status(),"environment_checks":self.environment(),"backend_data_dir":self.options.data_dir,
            "pending_pastes":self.pending_pastes.values().collect::<Vec<_>>(),"last_result":self.last_result}),
        )
    }
    fn active_host(&mut self) -> &mut Host {
        if self.active.as_ref().is_some_and(|a| a.microphone_test) {
            &mut self.preview
        } else {
            &mut self.host
        }
    }
    fn source(&self, params: &Value) -> Result<Source> {
        if let Some(source) = params.get("source") {
            ensure!(self.options.allow_test_sources, "test sources are disabled");
            return Ok(serde_json::from_value(source.clone())?);
        }
        let native = self
            .options
            .native
            .as_ref()
            .context("Native audio library is unavailable")?;
        if native.microphone_authorization()? != 3 {
            self.emit(
                "microphone_permission_required",
                json!({"retry_after_grant":true}),
            );
            bail!(
                "Microphone permission is required; press the shortcut again after granting access"
            );
        }
        Ok(self.native_source())
    }
    fn native_source(&self) -> Source {
        let cfg = &self.config.config;
        Source::ConfiguredNative {
            dsp: dsp(cfg),
            device: DeviceOptions {
                voice_processing: cfg.get("audio.capture_backend") == "voice_processing",
                input_device: cfg.get("audio.input_device").as_str().map(str::to_owned),
                capture_channels: cfg.get("audio.capture_channels").as_u64().unwrap() as u32,
                block_frames: cfg.get("audio.blocksize").as_u64().unwrap() as u32,
            },
        }
    }
    async fn start(&mut self, params: &Value, microphone_test: bool) -> Result<Value> {
        ensure!(
            self.active.is_none(),
            "a recording or microphone test is already active"
        );
        ensure!(
            microphone_test || self.key_present(),
            "DashScope API key is missing"
        );
        let source = self.source(params)?;
        let mut provider = self.provider(params["context"].as_str().unwrap_or(""));
        if let Some(intent) = params["intent"]
            .as_str()
            .filter(|s| ["dictation", "prompt"].contains(s))
        {
            provider
                .config
                .apply_update("llm.polish_mode", &json!(intent))?;
        }
        self.pending_pastes.clear();
        self.claimed_paste = None;
        self.paste_blocked = false;
        self.preview_path = None;
        self.generation += 1;
        let generation = self.generation;
        let core = if microphone_test {
            self.preview
                .start(StartRequest { source, asr: None })
                .await?
        } else if provider.model_info()["transport"] == "realtime_ws" {
            self.host
                .start_external(source, provider.clone().external()?)
                .await?
        } else {
            self.host.start(StartRequest { source, asr: None }).await?
        };
        self.active = Some(Active {
            generation,
            core_generation: core.generation,
            cancel: self.shutdown.child_token(),
            provider,
            mode: self.mode.clone(),
            microphone_test,
            mic_started: false,
            core_done: false,
            prepared: None,
            streamed: String::new(),
            partial: String::new(),
        });
        self.change_state("starting");
        let active = self.active.as_ref().unwrap();
        if active.provider.config.get("enable_polish") == true
            && active.provider.config.get("llm.polish_mode") == "prompt"
        {
            self.emit(
                "prompt_hint",
                crate::coach::assess(
                    "",
                    active.provider.config.get("ui.language").as_str().unwrap(),
                ),
            );
        }
        let mut status = self
            .active_host()
            .subscribe()
            .context("capture status unavailable")?;
        let internal = self.internal.clone();
        self.tasks.spawn(async move {
            loop {
                let value = status.borrow_and_update().clone();
                let terminal = value.phase.terminal();
                if internal
                    .send(Internal::Core(generation, value))
                    .await
                    .is_err()
                    || terminal
                {
                    break;
                }
                if status.changed().await.is_err() {
                    break;
                }
            }
        });
        Ok(json!({"ok":true,"generation":generation,"recording_id":core.recording_id}))
    }
    fn finish(&mut self) -> Result<Value> {
        let Some(active) = &self.active else {
            return Ok(json!({"ok":true}));
        };
        if !active.core_done {
            let generation = active.core_generation;
            self.active_host().finish(generation)?;
            self.change_state("processing");
        }
        self.pressed_at = None;
        self.latched = false;
        Ok(json!({"ok":true}))
    }
    fn cancel(&mut self) -> Result<Value> {
        self.pending_pastes.clear();
        self.claimed_paste = None;
        self.pressed_at = None;
        self.latched = false;
        if let Some(active) = &self.active {
            active.cancel.cancel();
            let generation = active.core_generation;
            if !active.core_done {
                let _ = self.active_host().cancel(generation);
            }
            self.change_state("cancelling");
        }
        Ok(json!({"ok":true}))
    }
    fn idle(&mut self) {
        self.active = None;
        self.pressed_at = None;
        self.latched = false;
        self.mode = self
            .config
            .config
            .get("default_mode")
            .as_str()
            .unwrap()
            .into();
        self.change_state("idle");
        self.schedule_compaction();
    }
    fn schedule_compaction(&mut self) {
        if self.compacting {
            self.compact_again = true;
            return;
        }
        if !self.history.list().iter().skip(3).any(|r| {
            matches!(r["status"].as_str(), Some("success" | "failed"))
                && r["storage_format"] == "wav"
        }) {
            return;
        }
        self.compacting = true;
        let cancel = self.shutdown.child_token();
        let internal = self.internal.clone();
        self.tasks.spawn(async move {
            tokio::select! {
                _ = cancel.cancelled() => {},
                _ = tokio::time::sleep(Duration::from_millis(250)) => {
                    let _ = internal.send(Internal::CompactReady).await;
                }
            }
        });
    }
    fn begin_compaction(&mut self) {
        if self.active.is_some() {
            // New input has priority. The next idle transition will reschedule.
            self.compacting = false;
            self.compact_again = false;
            return;
        }
        let history = self.history.clone();
        let cancel = self.shutdown.child_token();
        let internal = self.internal.clone();
        self.tasks.spawn(async move {
            let result = history.compact(3, 30, &cancel).await;
            let _ = internal.send(Internal::Compact(result)).await;
        });
    }

    fn paste(
        &mut self,
        text: String,
        raw: String,
        recording: Option<String>,
        provider: &Provider,
        streaming: bool,
    ) {
        if text.is_empty() || self.paste_blocked {
            return;
        }
        if self.pending_pastes.len() >= 64 {
            self.paste_blocked = true;
            self.pending_pastes.clear();
            self.emit("error",json!({"message":"Paste queue is full; copy the final result from the menu after processing"}));
            return;
        }
        let id = Uuid::new_v4().to_string();
        let data = json!({"token":id,"generation":self.generation,"text":text,"raw_text":raw,"recording_id":recording,
            "mode":self.mode,"streaming":streaming,"native_fast_paste":provider.config.get("native_fast_paste"),
            "restore_clipboard":provider.config.get("restore_clipboard"),"observe_correction":!streaming && provider.config.get("dictionary_learning.enabled")==true});
        self.pending_pastes.insert(id, data.clone());
        self.emit("paste_requested", data);
    }
    async fn dispatch(&mut self, method: &str, params: Value) -> Result<Value> {
        let method = if method == "ui_action" {
            ui_method(required(&params, "action")?)?
        } else {
            method
        };
        match method {
            "initialize" | "snapshot" => self.snapshot(),
            "status" => Ok(
                json!({"state":self.state,"generation":self.generation,"latched":self.latched,"current_mode":self.mode,"history_compacting":self.compacting,
                "core":if self.active.as_ref().is_some_and(|a|a.microphone_test){self.preview.status()}else{self.host.status()}}),
            ),
            "get_config" => Ok(self.public_config()),
            "reveal_api_key" => {
                // Only an explicit local UI action reveals the field. Public
                // snapshots, environment checks and diagnostics stay masked.
                let data = json!({"value":self.config.config.get("api_key")});
                self.emit("api_key_revealed", data);
                Ok(json!({"ok":true}))
            }
            "set_config" => {
                let key = required(&params, "key")?;
                self.config.update(key, &params["value"])?;
                self.preview_setting(key, &params["value"])?;
                self.config_changed();
                Ok(json!({"ok":true}))
            }
            "preview_config" => {
                self.preview_setting(required(&params, "key")?, &params["value"])?;
                Ok(json!({"ok":true}))
            }
            "sync_form_state" => {
                let mut form = params
                    .get("state")
                    .or_else(|| params.get("payload"))
                    .context("state is required")?
                    .clone();
                // Public UI snapshots intentionally mask the stored key. A
                // form flush of that unchanged blank field must not erase it;
                // an explicit set_config(api_key, "") still clears the key.
                if form["api_key"] == "" {
                    form.as_object_mut()
                        .context("state must be an object")?
                        .remove("api_key");
                }
                self.config.update_form(&form)?;
                self.config_changed();
                Ok(json!({"ok":true}))
            }
            "set_asr_model" => {
                self.config.update("asr.model", &params["model"])?;
                self.config_changed();
                Ok(json!({"ok":true}))
            }
            "set_device" => {
                self.config
                    .update("audio.input_device", &params["device"])?;
                if self.active.as_ref().is_some_and(|a| a.microphone_test) {
                    self.cancel()?;
                    self.emit(
                        "mic_test_error",
                        json!({"message":"Input device changed; start the microphone test again"}),
                    );
                }
                self.config_changed();
                Ok(json!({"ok":true}))
            }
            "set_active_hotkeys" => {
                self.config
                    .update("hotkey.active_hotkeys", &params["hotkeys"])?;
                self.config_changed();
                Ok(json!({"ok":true}))
            }
            "set_mode" => {
                ensure!(
                    ["walkie_talkie", "realtime_long"].contains(&required(&params, "mode")?),
                    "unknown mode"
                );
                self.config.update("default_mode", &params["mode"])?;
                self.config_changed();
                Ok(json!({"ok":true,"mode":params["mode"]}))
            }
            "list_devices" | "refresh_devices" => {
                self.cached_devices = self
                    .options
                    .native
                    .as_ref()
                    .map(NativeAudio::list_devices)
                    .transpose()?
                    .unwrap_or(json!([]));
                self.prepare_audio();
                let devices = self.devices()?;
                self.emit("devices_changed",json!({"devices":devices,"selected_device":self.config.config.get("audio.input_device"),"audio_input_status":self.input_status()}));
                Ok(devices)
            }
            "refresh_environment" => {
                self.prepare_audio();
                self.emit("environment_changed", self.environment());
                Ok(self.environment())
            }
            "platform_status" => {
                self.platform_status = params;
                Ok(json!({"ok":true}))
            }
            "start" => self.start(&params, false).await,
            "finish" | "stop" => self.finish(),
            "cancel" => self.cancel(),
            "toggle_recording" => {
                if self.state == "idle" {
                    let result = self.start(&params, false).await?;
                    self.latched = true;
                    Ok(result)
                } else {
                    self.finish()
                }
            }
            "hotkey_pressed" => {
                if self.state == "idle" {
                    self.latched = false;
                    self.pressed_at = Some(Instant::now());
                    self.start(&params, false).await
                } else if ["starting", "recording"].contains(&self.state.as_str()) && self.latched {
                    self.finish()
                } else {
                    Ok(json!({"ok":true,"ignored":true}))
                }
            }
            "hotkey_released" => {
                if let Some(pressed) = self.pressed_at.take()
                    && ["starting", "recording"].contains(&self.state.as_str())
                {
                    if self.mode == "walkie_talkie"
                        || pressed.elapsed() >= Duration::from_millis(350)
                    {
                        return self.finish();
                    }
                    self.latched = true;
                    self.emit(
                        "gesture_changed",
                        json!({"latched":true,"generation":self.generation}),
                    );
                }
                Ok(json!({"ok":true}))
            }
            "append" => {
                ensure!(
                    self.options.allow_test_sources,
                    "test PCM streaming is disabled"
                );
                let active = self.active.as_ref().context("no active session")?;
                ensure!(
                    params["generation"].as_u64() == Some(active.generation),
                    "stale session generation"
                );
                let generation = active.core_generation;
                let encoded = required(&params, "pcm_base64")?;
                ensure!(
                    encoded.len() <= 1712,
                    "encoded PCM block exceeds size limit"
                );
                self.active_host()
                    .append(generation, STANDARD.decode(encoded)?.into())?;
                Ok(json!({"accepted":true}))
            }
            "claim_paste" => {
                let data = self
                    .pending_pastes
                    .remove(required(&params, "token")?)
                    .unwrap_or(json!({"cancelled":true}));
                self.claimed_paste = (data["cancelled"] != true).then_some(data.clone());
                Ok(data)
            }
            "prepare_paste_observation" => {
                let Some(paste) = self.claimed_paste.take().filter(|p| {
                    p["token"] == params["token"]
                        && p["generation"].as_u64() == Some(self.generation)
                }) else {
                    return Ok(json!({"cancelled":true,"observation_id":null}));
                };
                if paste["observe_correction"] != true {
                    return Ok(json!({"cancelled":false,"observation_id":null}));
                }
                if let Some(observation) = &mut self.observation {
                    let progress = observation.poll(
                        &params["snapshot"],
                        &Value::Null,
                        self.started_at.elapsed().as_secs_f64(),
                        true,
                    );
                    self.observation_progress(progress)?;
                }
                if !self
                    .learning_allowed(&json!({"app_bundle_id":params["snapshot"]["app_bundle_id"]}))
                {
                    return Ok(json!({"observation_id":null}));
                }
                let id = Uuid::new_v4().to_string();
                self.observation = Observation::prepare(
                    id.clone(),
                    params["snapshot"].clone(),
                    &paste,
                    self.started_at.elapsed().as_secs_f64(),
                );
                if self.observation.is_some() {
                    self.schedule_observation(id.clone());
                    Ok(json!({"observation_id":id}))
                } else {
                    Ok(json!({"observation_id":null}))
                }
            }
            "poll_observation" | "finish_observation" => {
                if let Some(observation) = &mut self.observation
                    && params["observation_id"] == observation.id
                {
                    let progress = observation.poll(
                        &params["focused"],
                        &params["retained"],
                        self.started_at.elapsed().as_secs_f64(),
                        method == "finish_observation",
                    );
                    self.observation_progress(progress)?;
                }
                Ok(json!({"ok":true}))
            }
            "cancel_observation" => {
                self.end_observation(None);
                Ok(json!({"ok":true}))
            }
            "get_dictionary" => Ok(json!(self.dictionary.entries)),
            "add_dict_entry" => {
                self.dictionary
                    .add(required(&params, "term")?, &params["aliases"])?;
                self.emit("dictionary_changed", json!(self.dictionary.entries));
                Ok(json!({"ok":true}))
            }
            "remove_dict_entry" => {
                self.dictionary.remove(required(&params, "term")?)?;
                self.emit("dictionary_changed", json!(self.dictionary.entries));
                Ok(json!({"ok":true}))
            }
            "list_recordings" | "get_recordings" => {
                self.emit("recordings_changed",json!({"recordings":self.history.list(),"storage":self.history.storage_summary()}));
                Ok(json!(self.history.list()))
            }
            "storage_summary" => Ok(self.history.storage_summary()),
            "delete_recording" => {
                let id = required(&params, "id")?;
                ensure!(
                    !(self
                        .active
                        .as_ref()
                        .is_some_and(|a| !a.microphone_test && !a.core_done)
                        && self
                            .host
                            .status()
                            .recording_id
                            .is_some_and(|recording| recording.to_string() == id)),
                    "Recording is still being saved"
                );
                if let Some((active, cancel)) = &self.retry
                    && active == id
                {
                    cancel.cancel();
                }
                let deleted = self.history.delete(id)?;
                self.emit("recording_deleted", json!({"id":id}));
                Ok(json!({"ok":deleted}))
            }
            "play_recording" => {
                let id = required(&params, "id")?;
                let path = self.history.path(id).context("Recording file not found")?;
                self.emit("play_recording", json!({"id":id,"path":path}));
                Ok(json!({"id":id,"path":path}))
            }
            "stop_recording" => {
                self.emit("stop_recording", params);
                Ok(json!({"ok":true}))
            }
            "copy_transcript" => {
                let id = required(&params, "id")?;
                let record = self.history.get(id).context("Recording not found")?;
                let data = json!({"id":id,"text":record["transcript"].as_str().unwrap_or("")});
                self.emit("copy_transcript", data.clone());
                Ok(data)
            }
            "retry_transcription" => self.start_retry(required(&params, "id")?),
            "cancel_retry" => {
                if let Some((_, cancel)) = &self.retry {
                    cancel.cancel();
                }
                Ok(json!({"ok":true}))
            }
            "compact_recording_history" => {
                if self.compacting {
                    return Ok(json!({"ok":false,"status":"busy"}));
                }
                self.compacting = true;
                self.emit("recording_compaction_started", json!({}));
                let history = self.history.clone();
                let cancel = self.shutdown.child_token();
                let internal = self.internal.clone();
                self.tasks.spawn(async move {
                    let result = history.compact(3, 30, &cancel).await;
                    let _ = internal.send(Internal::Compact(result)).await;
                });
                Ok(json!({"ok":true}))
            }
            "start_mic_test" => self.start(&params, true).await,
            "stop_mic_test" => {
                if self.active.as_ref().is_some_and(|a| a.microphone_test) {
                    self.finish()
                } else {
                    Ok(json!({"ok":true}))
                }
            }
            "play_mic_test" => {
                let path = self
                    .preview_path
                    .as_ref()
                    .context("No microphone test recording is available")?;
                let bytes = tokio::fs::read(path).await?;
                ensure!(
                    bytes.len() <= 32000 * 6 + 44,
                    "microphone test exceeds playback limit"
                );
                let data = json!({"wav_base64":STANDARD.encode(bytes)});
                self.emit("mic_test_playback", data.clone());
                Ok(data)
            }
            "check_dashscope_models" => {
                self.start_model_check();
                Ok(json!({"ok":true}))
            }
            "get_dictionary_learning" => self.learning_records(),
            "submit_correction" => {
                let evidence = learning::evidence(params.get("evidence").unwrap_or(&params))?;
                ensure!(
                    self.learning_allowed(&evidence),
                    "dictionary learning is disabled or this app is excluded"
                );
                let observation = if learning::string(&evidence, "observation_id").is_empty() {
                    Uuid::new_v4().to_string()
                } else {
                    learning::string(&evidence, "observation_id").into()
                };
                let jobs = self
                    .learning
                    .enqueue(&learning::split(&evidence, &observation), now())?;
                self.learning_changed(None, "observation");
                self.schedule_learning(Duration::ZERO);
                Ok(json!({"ok":true,"queued":jobs.len(),"observation_id":observation}))
            }
            "approve_dictionary_learning"
            | "reject_dictionary_learning"
            | "undo_dictionary_learning" => {
                let id = required(&params, "id")?;
                let ok = match method {
                    "approve_dictionary_learning" => {
                        self.learning.approve(id, &mut self.dictionary, now())?
                    }
                    "reject_dictionary_learning" => self.learning.reject(id, now())?,
                    _ => self.learning.undo(id, &mut self.dictionary, now())?,
                };
                self.learning_changed(
                    Some(id),
                    if method == "undo_dictionary_learning" {
                        "undo"
                    } else {
                        "review"
                    },
                );
                Ok(json!({"ok":ok}))
            }
            "open_config_file" | "open_dict_file" => {
                let path = if method == "open_config_file" {
                    &self.config.path
                } else {
                    &self.dictionary.path
                };
                let data = json!({"path":path});
                self.emit("open_file", data.clone());
                Ok(data)
            }
            "open_accessibility_settings" => {
                self.emit("open_url",json!({"url":"x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"}));
                Ok(json!({"ok":true}))
            }
            "open_external" => {
                let url = url::Url::parse(required(&params, "url")?)?;
                ensure!(
                    url.scheme() == "https"
                        && url.host_str() == Some("dashscope.console.aliyun.com")
                        && url.port().is_none()
                        && url.username().is_empty()
                        && url.password().is_none(),
                    "external URL is not allowed"
                );
                self.emit("open_url", json!({"url":url.as_str()}));
                Ok(json!({"ok":true}))
            }
            "export_diagnostics" => {
                let path = PathBuf::from(required(&params, "path")?);
                let data = json!({"version":crate::PRODUCT_VERSION,"runtime":"rust","state":self.state,
                    "uptime_seconds":self.started_at.elapsed().as_secs(),"environment":self.environment(),
                    "storage":self.history.storage_summary(),"audio_input_status":self.input_status(),"asr_model":self.config.config.get("asr.model"),
                    "llm_model":self.config.config.get("llm.model"),"learning_enabled":self.config.config.get("dictionary_learning.enabled")});
                crate::persistence::atomic_write(&path, &serde_json::to_vec_pretty(&data)?)?;
                Ok(json!({"ok":true}))
            }
            "shutdown" => {
                self.close().await;
                Ok(json!({"ok":true,"closed":true}))
            }
            _ => bail!("unknown method: {method}"),
        }
    }
    fn preview_setting(&mut self, key: &str, value: &Value) -> Result<()> {
        ensure!(
            key.starts_with("audio.") || self.config.config.get(key) != &Value::Null,
            "unknown setting"
        );
        if !key.starts_with("audio.") {
            return Ok(());
        }
        let Some(active) = &mut self.active else {
            return Ok(());
        };
        if !active.microphone_test {
            return Ok(());
        }
        active.provider.config.apply_update(key, value)?;
        if [
            "audio.capture_backend",
            "audio.input_device",
            "audio.capture_channels",
            "audio.blocksize",
            "audio.gain_mode",
        ]
        .contains(&key)
        {
            self.cancel()?;
            self.emit("mic_test_error",json!({"message":"Audio capture settings changed; start the microphone test again"}));
        } else if let Some(native) = &self.options.native {
            native.preview_dsp(dsp(&active.provider.config))?;
        }
        Ok(())
    }
    fn start_retry(&mut self, id: &str) -> Result<Value> {
        if self.retry.is_some() {
            return Ok(json!({"ok":false,"status":"busy"}));
        }
        let record = self.history.get(id).context("Recording not found")?;
        let mut provider = self.provider("");
        provider
            .config
            .apply_update("asr.language", &record["language"])?;
        // Retry is the original offline transcription operation, independent of
        // the current microphone model and of live dictation polishing.
        provider
            .config
            .apply_update("enable_polish", &json!(false))?;
        self.history.pin(id);
        self.history.update(id, "pending", None, None, None)?;
        let cancel = self.shutdown.child_token();
        self.retry = Some((id.into(), cancel.clone()));
        let history = self.history.clone();
        let internal = self.internal.clone();
        let id = id.to_owned();
        self.emit("retry_started", json!({"id":id}));
        self.tasks.spawn(async move {
            let result = async {
                let wav = history.wav(&id, &cancel).await?;
                provider
                    .transcribe_file(&wav.path, "qwen3.5-omni-plus", &cancel, None)
                    .await
            }
            .await;
            let _ = internal.send(Internal::Retry(id, result)).await;
        });
        Ok(json!({"ok":true,"status":"accepted"}))
    }
    fn start_model_check(&mut self) {
        if self.checking_models {
            return;
        }
        self.checking_models = true;
        self.emit("model_check_started", json!({}));
        let provider = self.provider("");
        let cancel = self.shutdown.child_token();
        let internal = self.internal.clone();
        self.tasks.spawn(async move {
            let check=|family:&'static str,model:&'static str| {let provider=provider.clone();let cancel=cancel.clone();async move {
                let start=Instant::now();
                let result=tokio::time::timeout(Duration::from_secs(10),provider.probe_model(model,&cancel)).await;
                let error=match result{Ok(Ok(_))=>String::new(),Ok(Err(e))=>e.to_string(),Err(_)=>"Model check timed out".into()};
                json!({"family":family,"model":model,"status":if error.is_empty(){"ok"}else{"error"},"latency_ms":start.elapsed().as_millis(),"error":error})
            }};
            let (pro,lite)=tokio::join!(check("pro","qwen3.5-omni-plus"),check("lite","qwen3.5-omni-flash"));
            let _=internal.send(Internal::Models(json!([pro,lite]))).await;
        });
    }
    fn schedule_learning(&mut self, delay: Duration) {
        let internal = self.internal.clone();
        let cancel = self.shutdown.clone();
        self.tasks.spawn(async move {tokio::select!{_=cancel.cancelled()=>{},_=tokio::time::sleep(delay)=>{let _=internal.send(Internal::WakeLearning).await;}}});
    }
    fn learning_allowed(&self, evidence: &Value) -> bool {
        self.config.config.get("dictionary_learning.enabled") == true
            && self.key_present()
            && !self
                .config
                .config
                .get("dictionary_learning.excluded_bundle_ids")
                .as_array()
                .unwrap()
                .iter()
                .any(|id| id == &evidence["app_bundle_id"])
    }
    fn learning_changed(&self, id: Option<&str>, source: &str) {
        let job = id.and_then(|id| self.learning.get(id).ok().flatten());
        self.emit("dictionary_learning_changed",json!({"id":id,"source":source,"status":job.as_ref().map(|j|j.status.clone()),
            "term":job.as_ref().and_then(|j|j.result.as_ref()).map(|d|d.term.clone()),
            "aliases":job.as_ref().and_then(|j|j.result.as_ref()).map(|d|d.aliases.clone()),
            "records":self.learning_records().unwrap_or(json!([])),"dictionary":self.dictionary.entries}));
    }
    fn start_learning(&mut self) -> Result<()> {
        if self.learning_busy
            || self.config.config.get("dictionary_learning.enabled") != true
            || !self.key_present()
        {
            return Ok(());
        }
        let Some(job) = self.learning.claim(now())? else {
            if let Some(due) = self.learning.next_due()? {
                self.schedule_learning(Duration::from_secs_f64((due - now()).clamp(0.05, 60.0)));
            }
            return Ok(());
        };
        if !self.learning_allowed(&job.evidence) && job.apply_origin != "review" {
            self.learning.finish(
                &job.id,
                "ignored",
                &Decision::ignore("learning_disabled_or_excluded"),
                None,
                now(),
            )?;
            self.schedule_learning(Duration::ZERO);
            return Ok(());
        }
        self.learning_busy = true;
        let internal = self.internal.clone();
        let provider = self.provider("");
        let cancel = self.shutdown.child_token();
        self.tasks.spawn(async move {
            let decision = if let Some(decision) = &job.result {
                Ok(decision.clone())
            } else {
                provider.classify_correction(&job.evidence, &cancel).await
            };
            let _ = internal
                .send(Internal::Learning(Box::new(job), decision))
                .await;
        });
        Ok(())
    }
    fn finish_result(
        &mut self,
        generation: u64,
        result: Result<Outcome>,
        history_saved: bool,
    ) -> Result<()> {
        let Some(active) = self.active.as_ref().filter(|a| a.generation == generation) else {
            return Ok(());
        };
        let cancelled = active.cancel.is_cancelled();
        let provider = active.provider.clone();
        let id = self.host.status().recording_id.map(|id| id.to_string());
        if cancelled {
            if let Some(id) = &id {
                self.history
                    .update(id, "failed", None, Some("Recording cancelled"), None)?;
            }
        } else {
            match result {
                Ok(result) => {
                    if !history_saved && let Some(id) = &id {
                        self.history.update(
                            id,
                            "success",
                            Some(&result.raw_text),
                            None,
                            Some(result.billing.clone()),
                        )?;
                    }
                    for warning in &result.warnings {
                        self.emit(
                            "warning",
                            json!({"message":warning,"generation":generation}),
                        );
                    }
                    self.last_result = json!({"text":result.final_text,"raw_text":result.raw_text,"generation":generation});
                    self.emit("final_result",json!({"text":result.final_text,"raw_text":result.raw_text,"recording_id":id,"billing":result.billing,"generation":generation}));
                    if let Some(text) = result.paste_text {
                        self.paste(text, result.raw_text, id.clone(), &provider, false);
                    }
                }
                Err(error) => {
                    if let Some(id) = &id {
                        self.history
                            .update(id, "failed", None, Some(&error.to_string()), None)?;
                    }
                    self.emit(
                        "error",
                        json!({"message":error.to_string(),"generation":generation}),
                    );
                }
            }
        }
        self.idle();
        self.emit(
            "recordings_changed",
            json!({"recordings":self.history.list(),"storage":self.history.storage_summary()}),
        );
        Ok(())
    }
    async fn handle_internal(&mut self, message: Internal) -> Result<()> {
        match message {
            Internal::Core(generation, status) => {
                if let Err(error) = self.core_status(generation, status).await {
                    if self
                        .active
                        .as_ref()
                        .is_some_and(|a| a.generation == generation && a.core_done)
                    {
                        self.idle();
                    }
                    return Err(error);
                }
            }
            Internal::Finished(generation, result) => {
                if let Err(error) = self.finish_result(generation, result, false) {
                    if self
                        .active
                        .as_ref()
                        .is_some_and(|a| a.generation == generation)
                    {
                        self.idle();
                    }
                    return Err(error);
                }
            }
            Internal::Stage(generation, stage) => {
                if self
                    .active
                    .as_ref()
                    .is_some_and(|a| a.generation == generation && !a.cancel.is_cancelled())
                {
                    self.emit(
                        "processing_stage",
                        json!({"stage":stage,"generation":generation}),
                    );
                }
            }
            Internal::Partial(generation, text) => {
                if self
                    .active
                    .as_ref()
                    .is_some_and(|a| a.generation == generation && !a.cancel.is_cancelled())
                {
                    self.emit(
                        "partial_result",
                        json!({"text":text,"generation":generation}),
                    );
                }
            }
            Internal::MicStop(generation) => {
                if self.active.as_ref().is_some_and(|a| {
                    a.generation == generation && a.microphone_test && !a.core_done
                }) {
                    let _ = self.finish();
                }
            }
            Internal::Retry(id, result) => {
                self.history.unpin(&id);
                let Some((current, cancel)) = self.retry.take() else {
                    return Ok(());
                };
                if current != id {
                    self.retry = Some((current, cancel));
                    return Ok(());
                }
                if cancel.is_cancelled() || self.history.get(&id).is_none() {
                    self.history
                        .update(&id, "failed", None, Some("Retry cancelled"), None)?;
                    self.emit("retry_failed", json!({"id":id,"error":"Retry cancelled"}));
                } else {
                    match result {
                        Ok(completed) if !completed.text.trim().is_empty() => {
                            let bills = workflow::completion_billing(
                                &completed,
                                self.history
                                    .get(&id)
                                    .and_then(|r| r["duration_seconds"].as_f64())
                                    .unwrap_or(0.0),
                            );
                            let bill = billing::merge(&bills);
                            self.history.update(
                                &id,
                                "success",
                                Some(completed.text.trim()),
                                None,
                                Some(bill.clone()),
                            )?;
                            self.emit(
                                "retry_completed",
                                json!({"id":id,"transcript":completed.text.trim(),"billing":bill}),
                            );
                        }
                        result => {
                            let error = result
                                .err()
                                .map(|e| e.to_string())
                                .unwrap_or("Empty transcription".into());
                            self.history
                                .update(&id, "failed", None, Some(&error), None)?;
                            self.emit("retry_failed", json!({"id":id,"error":error}));
                        }
                    }
                }
                self.emit("recordings_changed",json!({"recordings":self.history.list(),"storage":self.history.storage_summary()}));
            }
            Internal::CompactReady => self.begin_compaction(),
            Internal::Compact(result) => {
                self.compacting = false;
                match result {Ok(result)=>self.emit("recording_compaction_complete",json!({"result":result,"storage":self.history.storage_summary(),"recordings":self.history.list()})),Err(error)=>self.emit("recording_compaction_failed",json!({"message":error.to_string()}))}
                if std::mem::take(&mut self.compact_again) {
                    self.schedule_compaction();
                }
            }
            Internal::Models(results) => {
                self.checking_models = false;
                self.emit("model_check_complete", results);
            }
            Internal::WakeLearning => self.start_learning()?,
            Internal::ObservationPoll(id) => {
                if let Some(observation) = &self.observation
                    && observation.id == id
                {
                    if observation.expired(self.started_at.elapsed().as_secs_f64()) {
                        let progress =
                            observation.deadline(self.started_at.elapsed().as_secs_f64());
                        self.observation_progress(progress)?;
                    } else {
                        self.emit(
                            "observation_poll",
                            json!({"observation_id":id,"target_id":observation.target_id()}),
                        );
                        // A lost frontend reply must still expire on a later
                        // tick, so there is at most one periodic owner here.
                        self.schedule_observation(id);
                    }
                }
            }
            Internal::Learning(job, result) => {
                self.learning_busy = false;
                if !self.learning_allowed(&job.evidence) && job.apply_origin != "review" {
                    self.learning.finish(
                        &job.id,
                        "ignored",
                        &Decision::ignore("learning_disabled_or_excluded"),
                        None,
                        now(),
                    )?;
                } else {
                    match result {
                        Ok(decision) => {
                            let decision = if job.apply_origin == "review" {
                                decision
                            } else {
                                let checked = learning::validate(
                                    &decision,
                                    &job.evidence,
                                    &self.dictionary.entries,
                                );
                                let native = self.options.native.clone();
                                self.learning.authorize(&job, checked, now(), |a, b| {
                                    learning::same_identity(a, b, native.as_ref())
                                })?
                            };
                            if decision.action == "add" || job.apply_origin == "review" {
                                if let Err(error) = self.learning.apply(
                                    &job,
                                    &decision,
                                    &mut self.dictionary,
                                    &job.apply_origin,
                                    now(),
                                ) {
                                    self.learning
                                        .failure(&job, &error.to_string(), true, now())?;
                                }
                            } else {
                                self.learning.finish(
                                    &job.id,
                                    if decision.action == "review" {
                                        "review"
                                    } else {
                                        "ignored"
                                    },
                                    &decision,
                                    None,
                                    now(),
                                )?;
                            }
                        }
                        Err(error) => {
                            let message = error.to_string();
                            let retryable = message.contains("timed out")
                                || ["408", "409", "425", "429", "500", "502", "503", "504"]
                                    .iter()
                                    .any(|s| message.contains(s));
                            self.learning.failure(&job, &message, retryable, now())?;
                        }
                    }
                }
                self.learning_changed(Some(&job.id), "automatic");
                if let Some(terms) = self.learning.claim_notification(&job.observation_id)?
                    && job.candidate_count > 1
                    && !terms.is_empty()
                {
                    self.emit(
                        "dictionary_learning_summary",
                        json!({"id":job.observation_id,"terms":terms}),
                    );
                }
                self.schedule_learning(Duration::ZERO);
            }
        }
        Ok(())
    }
    async fn core_status(&mut self, generation: u64, status: Status) -> Result<()> {
        let Some(active) = self
            .active
            .as_mut()
            .filter(|a| a.generation == generation && !a.core_done)
        else {
            return Ok(());
        };
        let microphone_test = active.microphone_test;
        let cancelled = active.cancel.is_cancelled();
        let provider = active.provider.clone();
        let mode = active.mode.clone();
        if !status.phase.terminal() {
            let first_preview =
                microphone_test && status.phase == Phase::Recording && !active.mic_started;
            if first_preview {
                active.mic_started = true;
            }
            let changed = status.partial_text != active.partial;
            if changed {
                active.partial = status.partial_text.clone();
            }
            let stream_enabled = !microphone_test
                && mode == "realtime_long"
                && provider.config.get("streaming_paste") == true
                && provider.config.get("auto_paste") == true
                && provider.config.get("llm.polish_mode") != "prompt"
                && provider.model_info()["handles_inline_polish"] != true;
            let stream = if stream_enabled
                && status.pcm_bytes >= 3200
                && status.raw_transcript.starts_with(&active.streamed)
                && status.raw_transcript != active.streamed
            {
                let tail = status.raw_transcript[active.streamed.len()..].to_owned();
                let separator = if active.streamed.is_empty() { "" } else { " " };
                let text = format!(
                    "{separator}{}",
                    text::bilingual(&dictionary::normalize_text(&tail, &provider.entries)).trim()
                );
                active.streamed = status.raw_transcript.clone();
                Some((text, tail))
            } else {
                None
            };
            if !cancelled {
                if status.phase == Phase::Recording && self.state == "starting" {
                    self.change_state("recording");
                } else if matches!(status.phase, Phase::Finishing | Phase::Committing) {
                    self.change_state("processing");
                }
                if first_preview {
                    self.emit("mic_test_started", json!({"generation":generation}));
                    let internal = self.internal.clone();
                    let cancel = self.active.as_ref().unwrap().cancel.clone();
                    self.tasks.spawn(async move {tokio::select!{_=cancel.cancelled()=>{},_=tokio::time::sleep(Duration::from_secs(5))=>{let _=internal.send(Internal::MicStop(generation)).await;}}});
                }
                self.emit(if microphone_test{"mic_test_level"}else{"audio_level"},json!({"rms":status.audio_rms,"generation":generation,
                    "waveform_level":crate::coach::waveform(status.audio_rms,provider.config.get("audio.waveform_ceiling_dbfs").as_f64().unwrap())}));
                if changed {
                    if provider.config.get("enable_polish") == true
                        && provider.config.get("llm.polish_mode") == "prompt"
                    {
                        self.emit(
                            "prompt_hint",
                            crate::coach::assess(
                                &status.partial_text,
                                provider.config.get("ui.language").as_str().unwrap(),
                            ),
                        );
                    }
                    self.emit(
                        "partial_result",
                        json!({"text":status.partial_text,"generation":generation}),
                    );
                }
                if let Some((text, raw)) = stream {
                    self.paste(
                        text,
                        raw,
                        status.recording_id.map(|id| id.to_string()),
                        &provider,
                        true,
                    );
                }
            }
            if !microphone_test
                && !cancelled
                && status.phase == Phase::Committing
                && let Some(record) = &status.recording
                && record.status == "completed"
            {
                let mut final_status = status.clone();
                final_status.phase = Phase::Completed;
                final_status.transcript = record.transcript.clone();
                let active = self.active.as_ref().unwrap();
                if active.prepared.is_none()
                    && workflow::finishes_locally(&provider, &final_status, &active.streamed)
                {
                    let path = self.host.store().directory().join(&record.filename);
                    let result = workflow::finish(
                        provider.clone(),
                        final_status,
                        &path,
                        &active.streamed,
                        &active.cancel,
                        Arc::new(|_| {}),
                        None,
                    )
                    .await?;
                    if let Some(prepared) = self.history.prepare_completed(
                        record,
                        &mode,
                        provider.config.get("asr.language").as_str().unwrap(),
                        &result.raw_text,
                        result.billing.clone(),
                    )? {
                        self.active.as_mut().unwrap().prepared = Some((result, prepared));
                    }
                }
            }
            return Ok(());
        }
        active.core_done = true;
        let cancel = active.cancel.clone();
        let streamed = active.streamed.clone();
        let prepared = active.prepared.take();
        if microphone_test {
            let id = status
                .recording_id
                .context("microphone test recording missing")?;
            if !cancelled && status.phase == Phase::Completed && status.pcm_bytes > 0 {
                self.preview_path =
                    Some(self.preview.store().directory().join(format!("{id}.wav")));
                self.emit("mic_test_complete",json!({"generation":generation,"duration_seconds":status.pcm_bytes as f64/32000.0}));
            } else if !cancelled {
                self.emit("mic_test_error",json!({"message":status.error.unwrap_or("No microphone audio captured".into())}));
            }
            cancel.cancel();
            self.idle();
            return Ok(());
        }
        let completed_record = match status.recording.as_ref() {
            Some(record) => Ok(record.as_ref().clone()),
            None => {
                self.host
                    .store()
                    .get(
                        status
                            .recording_id
                            .context("completed recording ID missing")?,
                    )
                    .await
            }
        };
        let record = match completed_record {
            Ok(record) => record,
            Err(error) => {
                self.emit("error", json!({"message":status.error.unwrap_or_else(|| error.to_string()),"generation":generation}));
                self.idle();
                return Ok(());
            }
        };
        if !cancelled
            && status.phase == Phase::Completed
            && let Some((result, prepared)) = prepared
        {
            if !self.history.commit_prepared(prepared)? {
                self.history.register_completed(
                    &record,
                    &mode,
                    provider.config.get("asr.language").as_str().unwrap(),
                    &result.raw_text,
                    result.billing.clone(),
                )?;
            }
            return self.finish_result(generation, Ok(result), true);
        }
        // Realtime text that needs no second provider can be completed with a
        // single history transaction, after the core's durable commit succeeds.
        let path = self.host.store().directory().join(&record.filename);
        if !cancelled && workflow::finishes_locally(&provider, &status, &streamed) {
            let result = workflow::finish(
                provider.clone(),
                status,
                &path,
                &streamed,
                &cancel,
                Arc::new(|_| {}),
                None,
            )
            .await?;
            self.history.register_completed(
                &record,
                &mode,
                provider.config.get("asr.language").as_str().unwrap(),
                &result.raw_text,
                result.billing.clone(),
            )?;
            return self.finish_result(generation, Ok(result), true);
        }
        self.history.register(
            &record,
            &mode,
            provider.config.get("asr.language").as_str().unwrap(),
        )?;
        if status.phase == Phase::Failed
            && status
                .error
                .as_deref()
                .is_some_and(|error| error.starts_with("recording commit failed:"))
        {
            return self.finish_result(
                generation,
                Err(anyhow::anyhow!(status.error.unwrap())),
                false,
            );
        }
        if cancelled || status.phase == Phase::Cancelled {
            self.history.update(
                &record.id.to_string(),
                "failed",
                None,
                Some("Recording cancelled"),
                None,
            )?;
            self.idle();
            return Ok(());
        }
        if status.pcm_bytes == 0 {
            self.history.update(
                &record.id.to_string(),
                "failed",
                None,
                status.error.as_deref(),
                None,
            )?;
            self.emit("error",json!({"message":status.error.unwrap_or("No audio captured".into()),"generation":generation}));
            self.idle();
            return Ok(());
        }
        self.change_state("processing");
        let internal = self.internal.clone();
        let stage = internal.clone();
        let partial = internal.clone();
        self.tasks.spawn(async move {
            let result = workflow::finish(
                provider,
                status,
                &path,
                &streamed,
                &cancel,
                Arc::new(move |value| {
                    let _ = stage.try_send(Internal::Stage(generation, value.into()));
                }),
                Some(Arc::new(move |value| {
                    let _ = partial.try_send(Internal::Partial(generation, value.into()));
                })),
            )
            .await;
            let _ = internal.send(Internal::Finished(generation, result)).await;
        });
        Ok(())
    }
    async fn close(&mut self) {
        self.shutdown.cancel();
        self.pending_pastes.clear();
        self.observation = None;
        self.claimed_paste = None;
        if let Some(active) = &self.active {
            active.cancel.cancel();
        }
        if let Some((_, cancel)) = &self.retry {
            cancel.cancel();
        }
        let _ = self.host.shutdown().await;
        let _ = self.preview.shutdown().await;
        if tokio::time::timeout(Duration::from_secs(2), async {
            while self.tasks.join_next().await.is_some() {}
        })
        .await
        .is_err()
        {
            self.tasks.abort_all();
            while self.tasks.join_next().await.is_some() {}
        }
    }
    fn schedule_observation(&mut self, id: String) {
        let internal = self.internal.clone();
        let cancel = self.shutdown.clone();
        self.tasks.spawn(async move {tokio::select!{_=cancel.cancelled()=>{},_=tokio::time::sleep(Duration::from_millis(100))=>{let _=internal.send(Internal::ObservationPoll(id)).await;}}});
    }
    fn end_observation(&mut self, id: Option<String>) {
        let id = id.or_else(|| self.observation.as_ref().map(|o| o.id.clone()));
        self.observation = None;
        self.emit("observation_ended", json!({"observation_id":id}));
    }
    fn observation_progress(&mut self, progress: Progress) -> Result<()> {
        if let Progress::Finished(evidence) = progress {
            self.end_observation(None);
            if let Some(evidence) = evidence
                && self.learning_allowed(&evidence)
            {
                let candidates =
                    learning::split(&evidence, learning::string(&evidence, "observation_id"));
                self.learning.enqueue(&candidates, now())?;
                self.learning_changed(None, "observation");
                self.schedule_learning(Duration::ZERO);
            }
        }
        Ok(())
    }
}

fn ui_method(action: &str) -> Result<&'static str> {
    Ok(match action {
        "revealApiKey" => "reveal_api_key",
        "setConfig" => "set_config",
        "previewConfig" => "preview_config",
        "syncFormState" => "sync_form_state",
        "setAsrModel" => "set_asr_model",
        "setDevice" => "set_device",
        "setActiveHotkeys" => "set_active_hotkeys",
        "addDictEntry" => "add_dict_entry",
        "removeDictEntry" => "remove_dict_entry",
        "approveDictionaryLearning" => "approve_dictionary_learning",
        "rejectDictionaryLearning" => "reject_dictionary_learning",
        "undoDictionaryLearning" => "undo_dictionary_learning",
        "refreshDevices" => "refresh_devices",
        "refreshEnvironment" => "refresh_environment",
        "checkDashScopeModels" => "check_dashscope_models",
        "openAccessibilitySettings" => "open_accessibility_settings",
        "openConfigFile" => "open_config_file",
        "openDictFile" => "open_dict_file",
        "openExternal" => "open_external",
        "getRecordings" => "get_recordings",
        "retryTranscription" => "retry_transcription",
        "deleteRecording" => "delete_recording",
        "playRecording" => "play_recording",
        "stopRecording" => "stop_recording",
        "copyTranscript" => "copy_transcript",
        "compactRecordingHistory" => "compact_recording_history",
        "startMicTest" => "start_mic_test",
        "stopMicTest" => "stop_mic_test",
        "playMicTest" => "play_mic_test",
        _ => bail!("unsupported settings action"),
    })
}
