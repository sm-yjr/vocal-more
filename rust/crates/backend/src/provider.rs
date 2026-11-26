// SPDX-License-Identifier: GPL-3.0-only
//! DashScope wire protocols; no Python SDK and no process-global API key.
use crate::{
    catalog::asr_model,
    config::Config,
    context::{self, CursorContext},
    dictionary::{self, Entry},
    text::{self, PromptKind},
};
use anyhow::{Context, Result, bail, ensure};
use base64::Engine;
use bytes::Bytes;
use futures_util::SinkExt;
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, VecDeque},
    future::pending,
    sync::Arc,
    time::Duration,
};
use tokio::{
    sync::mpsc,
    task::JoinSet,
    time::{Instant, timeout},
};
use tokio_util::sync::CancellationToken;
use vocal_more_core::{
    protocol::{self, Socket},
    runtime::{ExternalAsr, NetworkInput, NetworkReporter},
};

pub const MAX_TEXT_BYTES: usize = 256 * 1024;
const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const SEND_TIMEOUT: Duration = Duration::from_secs(10);

#[derive(Clone)]
pub struct Endpoints {
    pub(crate) http: String,
    realtime: Option<String>,
    recognition: String,
    pub(crate) fixture: bool,
    rollover_audio_bytes: Option<u64>,
}
impl Default for Endpoints {
    fn default() -> Self {
        Self {
            http: "https://dashscope.aliyuncs.com".into(),
            realtime: None,
            recognition: "wss://dashscope.aliyuncs.com/api-ws/v1/inference".into(),
            fixture: false,
            rollover_audio_bytes: None,
        }
    }
}
impl Endpoints {
    /// Test-only endpoint routing is explicit and never carries an API key.
    pub fn loopback(http: &str, websocket: &str) -> Result<Self> {
        for (raw, scheme) in [(http, "http"), (websocket, "ws")] {
            let url = url::Url::parse(raw)?;
            ensure!(
                url.scheme() == scheme
                    && matches!(url.host_str(), Some("127.0.0.1" | "[::1]"))
                    && url.username().is_empty()
                    && url.password().is_none()
                    && url.query().is_none()
                    && url.fragment().is_none(),
                "fixture endpoints must use literal loopback addresses"
            );
        }
        Ok(Self {
            http: http.trim_end_matches('/').into(),
            realtime: Some(websocket.into()),
            recognition: websocket.into(),
            fixture: true,
            rollover_audio_bytes: None,
        })
    }
    #[doc(hidden)]
    pub fn with_fixture_rollover_bytes(mut self, bytes: u64) -> Result<Self> {
        ensure!(self.fixture, "rollover override is fixture-only");
        ensure!(bytes >= 3_200, "rollover override is too small");
        self.rollover_audio_bytes = Some(bytes);
        Ok(self)
    }
}

#[derive(Clone)]
pub struct Provider {
    pub config: Config,
    pub entries: Vec<Entry>,
    pub context: String,
    pub(crate) scene: String,
    pub(crate) cursor_context: Option<CursorContext>,
    pub(crate) recent_turns: Vec<String>,
    pub endpoints: Endpoints,
    screen_context: bool,
    key: Arc<str>,
}

impl Provider {
    pub fn new(
        config: Config,
        entries: Vec<Entry>,
        context: &str,
        endpoints: Endpoints,
        environment_key: Option<&str>,
    ) -> Self {
        let key = config
            .get("api_key")
            .as_str()
            .filter(|k| !k.is_empty())
            .or(environment_key)
            .unwrap_or("")
            .to_owned();
        Self {
            config,
            entries,
            context: context.trim().to_string(),
            scene: String::new(),
            cursor_context: None,
            recent_turns: Vec::new(),
            endpoints,
            screen_context: false,
            key: Arc::from(key),
        }
    }
    pub fn model(&self) -> &str {
        self.config.get("asr.model").as_str().unwrap()
    }
    pub fn model_info(&self) -> &'static Value {
        asr_model(self.model()).expect("normalized ASR model")
    }
    pub fn workspace_host(&self) -> Option<String> {
        if self.endpoints.fixture {
            return None;
        }
        let raw = self
            .config
            .get("asr.realtime_url")
            .as_str()
            .filter(|s| !s.is_empty())?;
        let url = url::Url::parse(raw).ok()?;
        let host = url.host_str()?;
        host.ends_with(".maas.aliyuncs.com")
            .then(|| host.to_string())
    }
    pub fn recognition_url(&self) -> String {
        if let Some(host) = self.workspace_host() {
            format!("wss://{host}/api-ws/v1/inference")
        } else {
            self.endpoints.recognition.clone()
        }
    }
    pub fn http_base_url(&self) -> String {
        if let Some(host) = self.workspace_host() {
            format!("https://{host}")
        } else {
            self.endpoints.http.clone()
        }
    }
    pub fn needs_second_stage_polish(&self) -> bool {
        if self.config.get("enable_polish") != true {
            return false;
        }
        let pipeline = self.model_info()["pipeline"].as_str().unwrap_or("");
        if pipeline == "cascade" {
            return true;
        }
        if pipeline != "hybrid_asr" {
            return false;
        }
        let llm = &self.config.0["llm"];
        let has_override = llm["prompt_overrides"].as_object().is_some_and(|map| {
            map.values().any(|item| {
                item["enabled"] == true
                    && item["prompt"]
                        .as_str()
                        .is_some_and(|p| !p.trim().is_empty())
            })
        });
        llm["polish_mode"] != "dictation"
            || llm["output_language"] != "auto"
            || llm["level"] != "minimal"
            || llm["tone"] != "neutral"
            || llm["persona"] != "default"
            || llm["structured"] == true
            || has_override
    }
    pub fn wants_response(&self) -> bool {
        self.model_info()["always_request_response"] == true
            || (self.model_info()["handles_inline_polish"] == true
                && (self.config.get("enable_polish") == true || !self.scene.is_empty()))
    }
    pub fn supports_screen_context(&self) -> bool {
        self.model_info()["supports_screen_context"] == true
    }
    pub fn enable_screen_context(&mut self) -> Result<()> {
        ensure!(
            self.supports_screen_context(),
            "selected model does not support screen context"
        );
        self.screen_context = true;
        Ok(())
    }
    pub fn set_cursor_context(&mut self, cursor: Option<CursorContext>) {
        self.cursor_context = cursor;
    }
    pub fn set_recent_turns(&mut self, turns: Vec<String>) {
        self.recent_turns = turns;
    }
    fn apply_context_update(&mut self, value: Value) -> bool {
        if let Some(obj) = value.as_object()
            && obj.contains_key("cursor")
        {
            let mut updated = false;
            if let Ok(ctx) = serde_json::from_value::<CursorContext>(obj["cursor"].clone()) {
                self.cursor_context = Some(ctx);
                updated = true;
            }
            if let Some(turns) = obj.get("recent_turns").and_then(Value::as_array) {
                self.recent_turns = turns
                    .iter()
                    .filter_map(Value::as_str)
                    .map(str::to_owned)
                    .collect();
                updated = true;
            }
            return updated;
        }
        if let Ok(ctx) = serde_json::from_value::<CursorContext>(value) {
            self.cursor_context = Some(ctx);
            return true;
        }
        false
    }
    pub fn corpus(&self) -> String {
        if self.config.get("asr.use_dictionary_corpus") != true {
            return String::new();
        }
        dictionary::corpus(
            &self.entries,
            self.config
                .get("asr.extra_corpus_terms")
                .as_array()
                .unwrap(),
        )
    }
    pub fn build_vocabulary(&self, allow_super: bool) -> serde_json::Map<String, Value> {
        let mut vocabulary = serde_json::Map::new();
        let mut super_count = 0_usize;
        let max_super = if allow_super { 50 } else { 0 };
        let use_dict = self.config.get("asr.use_dictionary_corpus") == true;

        // 1. Dictionary canonical terms (top 35 get super hotword weight 50 on 3.1 ASR)
        if use_dict {
            for entry in &self.entries {
                let term = entry.term.trim();
                if vocabulary.len() >= 2000 {
                    break;
                }
                if !context::is_valid_hotword(term) || vocabulary.contains_key(term) {
                    continue;
                }
                let weight = if super_count < max_super.min(35) {
                    super_count += 1;
                    50
                } else {
                    4
                };
                vocabulary.insert(term.into(), json!(weight));
            }
        }

        // 2. Dynamic symbols/identifiers extracted from pre-dictation AX cursor context
        if let Some(cursor) = &self.cursor_context {
            for term in context::extract_dynamic_hotwords(cursor, 60) {
                if vocabulary.len() >= 2000 {
                    break;
                }
                if vocabulary.contains_key(&term) || !context::is_valid_hotword(&term) {
                    continue;
                }
                let weight = if super_count < max_super {
                    super_count += 1;
                    50
                } else {
                    4
                };
                vocabulary.insert(term, json!(weight));
            }
        }

        // 3. Extra user-supplied corpus terms
        if use_dict {
            for raw in self
                .config
                .get("asr.extra_corpus_terms")
                .as_array()
                .into_iter()
                .flatten()
            {
                if vocabulary.len() >= 2000 {
                    break;
                }
                let term = crate::config::py_string(raw);
                let term = term.trim();
                if !context::is_valid_hotword(term) || vocabulary.contains_key(term) {
                    continue;
                }
                vocabulary.insert(term.into(), json!(4));
            }
        }

        vocabulary
    }
    pub fn recognition_context_turns(&self) -> Vec<Value> {
        context::build_recognition_context_turns(
            &self.scene,
            &self.context,
            self.cursor_context.as_ref(),
            &self.recent_turns,
        )
    }
    pub fn recognition_continue_context(&self, task: &str) -> Option<Value> {
        if self.model_info()["supports_continue_context"] != true {
            return None;
        }
        let turns = self.recognition_context_turns();
        if turns.is_empty() {
            return None;
        }
        Some(json!({
            "header": {
                "action": "continue-task",
                "task_id": task,
                "streaming": "duplex"
            },
            "payload": {
                "input": {
                    "context": turns
                }
            }
        }))
    }
    pub(crate) fn api_key(&self) -> Result<Option<&str>> {
        if self.endpoints.fixture {
            return Ok(None);
        }
        ensure!(!self.key.is_empty(), "DashScope API key not configured");
        Ok(Some(&self.key))
    }
    pub fn external(self) -> Result<ExternalAsr> {
        ensure!(
            self.model_info()["transport"] == "realtime_ws",
            "selected model requires file transcription"
        );
        self.api_key()?;
        let model = self.model().to_string();
        // Context duration is a provider limitation; after the limit is reached
        // the application continues archiving and performs file-based recovery.
        let audio_limit_bytes = None;
        Ok(ExternalAsr {
            model,
            audio_limit_bytes,
            run: Box::new(move |input, cancel, reporter| {
                Box::pin(self.realtime(input, cancel, reporter))
            }),
        })
    }

    pub(crate) fn prompt(&self, kind: PromptKind) -> String {
        let appendix = context::build_prompt_context_appendix(
            self.cursor_context.as_ref(),
            &self.recent_turns,
        );
        let full_context = [self.context.as_str(), appendix.as_str()]
            .into_iter()
            .filter(|part| !part.is_empty())
            .collect::<Vec<_>>()
            .join("\n\n");
        text::build_prompt_with_scene(
            &self.config,
            kind,
            &dictionary::format_prompt(&self.entries),
            &full_context,
            &self.scene,
        )
    }

    pub fn session_update(&self) -> Value {
        let info = self.model_info();
        let mut session = json!({"modalities":["text"], "voice":null,"input_audio_format":"pcm16", "output_audio_format":"pcm16", "input_audio_transcription":{"model":null},"turn_detection":null});
        if self.screen_context {
            session["video"] = json!({"input":{"representation_compact":"normal"}});
        }
        if info["protocol"] == "realtime_conversation" {
            session["voice"] = info
                .get("voice")
                .cloned()
                .unwrap_or_else(|| json!("longanqian"));
            session["input_audio_transcription"] = Value::Null;
            let kind = if self.config.get("enable_polish") == true {
                PromptKind::Inline
            } else {
                PromptKind::Native
            };
            session["instructions"] = json!(self.prompt(kind));
        } else if info
            .get("input_audio_transcription_model")
            .is_some_and(|m| !m.is_null())
        {
            session["voice"] = info.get("voice").cloned().unwrap_or_else(|| json!("Tina"));
            session["input_audio_transcription"] =
                json!({"model":info["input_audio_transcription_model"]});
            session["enable_search"] = json!(false);
            if self.wants_response() {
                let kind = if self.config.get("enable_polish") == true {
                    PromptKind::Inline
                } else {
                    PromptKind::Native
                };
                session["instructions"] = json!(self.prompt(kind));
            }
        } else {
            let mut params = json!({});
            if self.config.get("asr.language") != "auto" {
                params["language"] = self.config.get("asr.language").clone();
            }
            let corpus = self.corpus();
            if !corpus.is_empty() {
                params["corpus"] = json!({"text":corpus});
            }
            session["input_audio_transcription"] = params;
            session["input_audio_format"] = json!("pcm");
            session["sample_rate"] = json!(16000);
        }
        json!({"event_id":event_id(),"type":"session.update","session":session})
    }

    pub fn recognition_start(&self, task: &str) -> Value {
        let info = self.model_info();
        let is_message_asr = info["supports_native_disfluency_removal"] == true;
        let mut parameters = if is_message_asr {
            let native_polish =
                self.config.get("enable_polish") == true && !self.needs_second_stage_polish();
            let mut params = json!({
                "format": "pcm",
                "sample_rate": 16000,
                "heartbeat": true,
                "intermediate_result_enabled": true,
                "disfluency_removal_enabled": native_polish
            });
            if info["supports_vad_tuning"] == true {
                params["vad_model"] = json!("near_meeting_16k");
                let gain_mode = self.config.get("audio.gain_mode").as_str().unwrap_or("");
                let gain = self.config.get("audio.gain").as_f64().unwrap_or(1.0);
                let highpass_freq = self
                    .config
                    .get("audio.highpass_freq")
                    .as_f64()
                    .unwrap_or(200.0);
                if gain_mode == "manual" && gain >= 6.0 && highpass_freq < 260.0 {
                    params["speech_noise_threshold"] = json!(-0.25);
                } else if highpass_freq >= 260.0 {
                    params["speech_noise_threshold"] = json!(0.2);
                }
            }
            params
        } else {
            json!({
                "format": "pcm",
                "sample_rate": 16000,
                "semantic_punctuation_enabled": true,
                "heartbeat": true
            })
        };
        if info["supports_instant_hotwords"] == true {
            if !is_message_asr {
                parameters["semantic_punctuation_enabled"] = json!(false);
                parameters["punctuation_prediction_enabled"] = json!(true);
            }
            let vocabulary = self.build_vocabulary(info["supports_super_hotwords"] == true);
            if !vocabulary.is_empty() {
                parameters["vocabulary"] = Value::Object(vocabulary);
            }
        }
        if self.config.get("asr.language") != "auto" {
            parameters["language_hints"] = json!([self.config.get("asr.language")]);
        }
        let turns = self.recognition_context_turns();
        let input = if turns.is_empty() {
            json!({})
        } else {
            json!({"context": turns})
        };
        json!({"header":{"action":"run-task","task_id":task,"streaming":"duplex"},"payload":{"task_group":"audio","task":"asr","function":"recognition","model":self.model(),"parameters":parameters,"input":input}})
    }

    pub async fn probe_realtime_model(
        &self,
        model: &str,
        cancel: &CancellationToken,
    ) -> Result<()> {
        let mut provider = self.clone();
        provider.config.apply_update("asr.model", &json!(model))?;
        ensure!(
            provider.model_info()["transport"] == "realtime_ws",
            "model does not use realtime transport"
        );
        let recognition = provider.model_info()["protocol"] == "audio_recognition";
        let recognition_url = provider.recognition_url();
        let endpoint = if recognition {
            recognition_url.as_str()
        } else {
            provider.endpoints.realtime.as_deref().unwrap_or_else(|| {
                provider
                    .config
                    .get("asr.realtime_url")
                    .as_str()
                    .filter(|value| !value.is_empty())
                    .unwrap_or("wss://dashscope.aliyuncs.com/api-ws/v1/realtime")
            })
        };
        let mut url = url::Url::parse(endpoint)?;
        if !recognition {
            url.query_pairs_mut().append_pair("model", model);
        }
        let proxy = provider
            .config
            .get("network.proxy_url")
            .as_str()
            .filter(|value| !value.is_empty());
        let mut socket = tokio::select! {
            biased;
            _ = cancel.cancelled() => bail!("model check cancelled"),
            result = timeout(
                CONNECT_TIMEOUT,
                protocol::connect_url_with_proxy(url, provider.api_key()?, proxy),
            ) => result.context("model check timed out")??,
        };
        let task = uuid::Uuid::new_v4().simple().to_string();
        send_json(
            &mut socket,
            if recognition {
                provider.recognition_start(&task)
            } else {
                provider.session_update()
            },
        )
        .await?;
        tokio::select! {
            biased;
            _ = cancel.cancelled() => bail!("model check cancelled"),
            result = timeout(CONNECT_TIMEOUT, async {
                loop {
                    let event = protocol::receive(&mut socket).await?;
                    check_error(&event)?;
                    if (recognition
                        && event["header"]["event"] == "task-started"
                        && event["header"]["task_id"] == task)
                        || (!recognition && event["type"] == "session.updated")
                    {
                        break;
                    }
                }
                Ok::<_, anyhow::Error>(())
            }) => result.context("model check timed out")??,
        }
        let _ = socket.close(None).await;
        Ok(())
    }

    async fn realtime(
        self,
        input: mpsc::Receiver<NetworkInput>,
        cancel: CancellationToken,
        reporter: NetworkReporter,
    ) -> Result<String> {
        if self.model_info()["rollover_audio_seconds"]
            .as_u64()
            .is_some_and(|seconds| seconds > 0)
        {
            self.realtime_segmented(input, cancel, reporter).await
        } else {
            Ok(self.realtime_once(input, cancel, reporter).await?.text)
        }
    }

    async fn realtime_segmented(
        self,
        mut input: mpsc::Receiver<NetworkInput>,
        cancel: CancellationToken,
        reporter: NetworkReporter,
    ) -> Result<String> {
        let rollover_bytes = self.endpoints.rollover_audio_bytes.unwrap_or(
            self.model_info()["rollover_audio_seconds"]
                .as_u64()
                .context("rollover duration missing")?
                * 32_000,
        );
        let vad_rollover_enabled = rollover_bytes >= ROLLOVER_VAD_MIN_LIMIT_BYTES;
        let mut segments = JoinSet::new();
        let mut results = BTreeMap::new();
        let mut index = 0_usize;
        let mut segment_bytes = 0_u64;
        let mut trailing_silence_bytes = 0_u64;
        let mut overlap_ring: Vec<u8> = Vec::with_capacity(if vad_rollover_enabled {
            ROLLOVER_OVERLAP_BYTES
        } else {
            0
        });
        let mut latest_segment_tail: Option<String> = None;
        let mut input_finished = false;
        let (mut segment_tx, segment_rx) = mpsc::channel(vocal_more_core::AUDIO_QUEUE_BLOCKS);
        let provider = self.clone();
        let segment_cancel = cancel.clone();
        let segment_reporter = reporter.segment(0);
        segments.spawn(async move {
            (
                0,
                provider
                    .realtime_once(segment_rx, segment_cancel, segment_reporter)
                    .await,
            )
        });

        loop {
            tokio::select! {
                biased;
                _ = cancel.cancelled() => bail!("ASR cancelled"),
                completed = segments.join_next(), if !segments.is_empty() => {
                    let Some(completed) = completed else { continue };
                    let (completed_index, result) = completed.context("realtime segment worker failed")?;
                    let result = result?;
                    let trimmed = result.text.trim();
                    if !trimmed.is_empty() {
                        let chars: Vec<char> = trimmed.chars().collect();
                        let start = chars.len().saturating_sub(200);
                        latest_segment_tail = Some(chars[start..].iter().collect());
                    }
                    results.insert(completed_index, result);
                    if input_finished && segments.is_empty() {
                        break;
                    }
                },
                next = input.recv(), if !input_finished => {
                    match next {
                        Some(NetworkInput::Pcm(pcm)) => {
                            let is_silent = vad_rollover_enabled
                                && pcm_rms_i16(&pcm) < ROLLOVER_SILENCE_RMS_THRESHOLD;
                            if is_silent {
                                trailing_silence_bytes =
                                    trailing_silence_bytes.saturating_add(pcm.len() as u64);
                            } else {
                                trailing_silence_bytes = 0;
                            }

                            let soft_cut = should_soft_rollover(
                                segment_bytes,
                                rollover_bytes,
                                trailing_silence_bytes,
                            );
                            let hard_cut = should_rollover(segment_bytes, pcm.len(), rollover_bytes);

                            if soft_cut || hard_cut {
                                segment_tx.send(NetworkInput::Finish).await
                                    .context("realtime rollover segment closed")?;
                                index += 1;
                                let (next_tx, next_rx) = mpsc::channel(vocal_more_core::AUDIO_QUEUE_BLOCKS);
                                segment_tx = next_tx;
                                segment_bytes = 0;
                                trailing_silence_bytes = 0;
                                let mut provider = self.clone();
                                if let Some(tail) = &latest_segment_tail {
                                    provider.recent_turns.push(tail.clone());
                                }
                                let segment_cancel = cancel.clone();
                                let segment_reporter = reporter.segment(index);
                                let segment_index = index;
                                segments.spawn(async move {
                                    (segment_index, provider.realtime_once(next_rx, segment_cancel, segment_reporter).await)
                                });
                                // On a forced hard cut (continuous speech with no pause),
                                // replay the last 1.5s into the next segment so words at the
                                // boundary are never chopped in half.
                                if hard_cut && !soft_cut && vad_rollover_enabled && !overlap_ring.is_empty() {
                                    let overlap = Bytes::copy_from_slice(&overlap_ring);
                                    segment_bytes += overlap.len() as u64;
                                    segment_tx.send(NetworkInput::Pcm(overlap)).await
                                        .context("realtime segment overlap queue closed")?;
                                }
                                overlap_ring.clear();
                            }

                            if vad_rollover_enabled {
                                push_overlap(&mut overlap_ring, &pcm, ROLLOVER_OVERLAP_BYTES);
                            }
                            segment_bytes += pcm.len() as u64;
                            segment_tx.send(NetworkInput::Pcm(pcm)).await
                                .context("realtime segment audio queue closed")?;
                        }
                        Some(NetworkInput::Image(jpeg)) => {
                            segment_tx.send(NetworkInput::Image(jpeg)).await
                                .context("realtime segment image queue closed")?;
                        }
                        Some(NetworkInput::Context(ctx)) => {
                            let _ = segment_tx.send(NetworkInput::Context(ctx)).await;
                        }
                        Some(NetworkInput::Finish) => {
                            segment_tx.send(NetworkInput::Finish).await
                                .context("realtime final segment closed")?;
                            input_finished = true;
                        }
                        None => bail!("audio source closed before commit"),
                    }
                },
            }
        }
        let mut text = String::new();
        let mut raw = String::new();
        let mut usage = json!({});
        for (_, result) in results {
            append_segment_text(&mut text, &result.text);
            append_segment_text(&mut raw, &result.raw);
            merge_usage(&mut usage, &result.usage);
        }
        ensure!(
            !text.trim().is_empty(),
            "ASR returned an empty segmented response"
        );
        let final_reporter = reporter.segment(index);
        final_reporter.transcript(&raw);
        final_reporter.usage(usage);
        Ok(text)
    }

    async fn realtime_once(
        mut self,
        mut input: mpsc::Receiver<NetworkInput>,
        cancel: CancellationToken,
        reporter: NetworkReporter,
    ) -> Result<RealtimeResult> {
        tokio::select! {
            biased;
            _ = cancel.cancelled() => bail!("ASR cancelled"),
            result = async {
                let recognition = self.model_info()["protocol"] == "audio_recognition";
                let recognition_url = self.recognition_url();
                let endpoint = if recognition { recognition_url.as_str() } else {
                    self.endpoints.realtime.as_deref().unwrap_or_else(|| {
                        self.config.get("asr.realtime_url").as_str().filter(|s| !s.is_empty()).unwrap_or("wss://dashscope.aliyuncs.com/api-ws/v1/realtime")
                    })
                };
                let mut url = url::Url::parse(endpoint)?;
                if !recognition { url.query_pairs_mut().append_pair("model",self.model()); }
                let proxy = self.config.get("network.proxy_url").as_str().filter(|value| !value.is_empty());
                let mut socket = timeout(CONNECT_TIMEOUT, protocol::connect_url_with_proxy(url,self.api_key()?,proxy)).await.context("ASR connection timed out")??;
                // Drain any cursor context that arrived while TLS/WS handshake was
                // in flight so it can enrich the initial run-task / session.update
                // (including dynamic hotwords) with zero added startup latency.
                let mut prebuffered: VecDeque<NetworkInput> = VecDeque::new();
                while let Ok(item) = input.try_recv() {
                    match item {
                        NetworkInput::Context(value) => {
                            self.apply_context_update(value);
                        }
                        other => prebuffered.push_back(other),
                    }
                }
                let task = uuid::Uuid::new_v4().simple().to_string();
                send_json(&mut socket, if recognition { self.recognition_start(&task) } else { self.session_update() }).await?;
                timeout(CONNECT_TIMEOUT, async {
                    loop {
                        let event = protocol::receive(&mut socket).await?;
                        check_error(&event)?;
                        if recognition {
                            if event["header"]["event"] == "task-started" && event["header"]["task_id"] == task { break; }
                        } else if event["type"] == "session.updated" { break; }
                    }
                    Ok::<_,anyhow::Error>(())
                }).await.context("ASR session admission timed out")??;
                reporter.ready();
                let mut accumulator = RealtimeText::new(self.wants_response(),recognition);
                let mut pending_pcm = Vec::with_capacity(3200);
                let mut pending_image: Option<Bytes> = None;
                let mut audio_event_sent = false;
                let mut committed = false; let mut total_bytes = 0_u64;
                let mut completion_deadline = None;
                let mut response_start_deadline = None;
                let mut heartbeat = tokio::time::interval(Duration::from_secs(15)); heartbeat.tick().await;
                loop {
                    enum Event { Input(Option<NetworkInput>), Server(Value), Heartbeat, Timeout }
                    let event = if !committed && let Some(item) = prebuffered.pop_front() {
                        Event::Input(Some(item))
                    } else {
                        tokio::select! {
                            value = input.recv(), if !committed => Event::Input(value),
                            value = protocol::receive(&mut socket) => Event::Server(value?),
                            _ = heartbeat.tick() => Event::Heartbeat,
                            _ = async { match completion_deadline { Some(d) => tokio::time::sleep_until(d).await, None => pending().await } } => Event::Timeout,
                            _ = async { match response_start_deadline { Some(d) => tokio::time::sleep_until(d).await, None => pending().await } }, if !accumulator.response_started => Event::Timeout,
                        }
                    };
                    match event {
                        Event::Timeout => bail!("ASR result deadline exceeded; recover from recorded audio"),
                        Event::Heartbeat => {
                            timeout(SEND_TIMEOUT,socket.send(tokio_tungstenite::tungstenite::Message::Ping(Bytes::new()))).await.context("ASR heartbeat timed out")??;
                        },
                        Event::Input(Some(NetworkInput::Pcm(pcm))) => {
                            total_bytes += pcm.len() as u64;
                            let is_omni_realtime = self.model().starts_with("qwen3.5-omni")
                                || self.model().starts_with("qwen3.8-omni");
                            if !self.endpoints.fixture && is_omni_realtime {
                                // Mirrors protocol::RealtimeConfig::cloud_audio_limit_bytes:
                                // Qwen3.5 Flash holds 480s; Plus and Qwen3.8 hold 600s.
                                let seconds =
                                    if self.model() == "qwen3.5-omni-flash-realtime" { 480 } else { 600 };
                                ensure!(total_bytes <= seconds * 32000, "ASR context duration exceeded; recover from recorded audio");
                            }
                            pending_pcm.extend_from_slice(&pcm);
                            while pending_pcm.len() >= 3200 {
                                send_pcm(&mut socket,&pending_pcm[..3200],recognition).await?;
                                pending_pcm.drain(..3200);
                                audio_event_sent = true;
                                if let Some(jpeg) = pending_image.take() {
                                    send_image(&mut socket, &jpeg, recognition, self.supports_screen_context()).await?;
                                }
                            }
                        },
                        Event::Input(Some(NetworkInput::Image(jpeg))) => {
                            ensure!(self.supports_screen_context(), "selected model does not support screen context");
                            ensure!(!recognition, "screen context is unavailable for recognition protocol models");
                            if audio_event_sent {
                                send_image(&mut socket, &jpeg, recognition, true).await?;
                            } else {
                                // The provider requires at least one audio append before
                                // an image append. Keep only the newest pre-audio frame.
                                pending_image = Some(jpeg);
                            }
                        },
                        Event::Input(Some(NetworkInput::Context(value))) => {
                            if self.apply_context_update(value)
                                && recognition
                                && let Some(update) = self.recognition_continue_context(&task)
                            {
                                send_json(&mut socket, update).await?;
                            }
                        },
                        Event::Input(Some(NetworkInput::Finish)) => {
                            if !pending_pcm.is_empty() {
                                if recognition && pending_pcm.len() < 1280 { pending_pcm.resize(1280,0); }
                                send_pcm(&mut socket,&pending_pcm,recognition).await?;
                                pending_pcm.clear();
                            }
                            if recognition { send_json(&mut socket,json!({"header":{"action":"finish-task","task_id":task,"streaming":"duplex"},"payload":{"input":{}}})).await?; }
                            else {
                                send_json(&mut socket,protocol::command("input_audio_buffer.commit")).await?;
                                if self.wants_response() {
                                    let mut create = protocol::command("response.create");
                                    if self.model_info()["protocol"] == "realtime_conversation" { create["response"] = json!({"modalities":["text"]}); }
                                    send_json(&mut socket,create).await?;
                                }
                            }
                            committed = true;
                            let extra_minutes = ((total_bytes as f64 / 32000.0) - 60.0).max(0.0) / 60.0;
                            completion_deadline = Some(Instant::now() + Duration::from_secs_f64((30.0 + extra_minutes*4.0).min(90.0)));
                            if self.wants_response() { response_start_deadline = Some(Instant::now() + Duration::from_secs_f64((3.0 + extra_minutes*1.5).min(20.0) + 6.0)); }
                        },
                        Event::Input(None) => bail!("audio source closed before commit"),
                        Event::Server(event) => {
                            if recognition && event["header"].get("task_id").is_some_and(|id| id != &json!(task)) { continue; }
                            accumulator.consume(&event,committed)?;
                            reporter.partial(&accumulator.partial);
                            reporter.transcript(&accumulator.raw);
                            if !accumulator.usage.is_null() { reporter.usage(accumulator.usage.clone()); }
                            if committed && accumulator.completed {
                                let result = accumulator.result().to_owned();
                                let _timing = vocal_more_core::diagnostics::Timing::new("provider_close");
                                let _ = timeout(Duration::from_millis(250),socket.close(None)).await;
                                return Ok(RealtimeResult {
                                    text: result,
                                    raw: accumulator.raw,
                                    usage: accumulator.usage,
                                });
                            }
                        },
                    }
                }
            } => result,
        }
    }
}

struct RealtimeResult {
    text: String,
    raw: String,
    usage: Value,
}

fn merge_usage(total: &mut Value, next: &Value) {
    let (Some(total), Some(next)) = (total.as_object_mut(), next.as_object()) else {
        return;
    };
    for (key, value) in next {
        if let Some(number) = value.as_u64() {
            let current = total.get(key).and_then(Value::as_u64).unwrap_or(0);
            total.insert(key.clone(), json!(current.saturating_add(number)));
        } else if value.is_object() {
            let target = total.entry(key.clone()).or_insert_with(|| json!({}));
            merge_usage(target, value);
        }
    }
}

const ROLLOVER_VAD_MIN_LIMIT_BYTES: u64 = 60 * 32_000;
const ROLLOVER_SILENCE_WINDOW_BYTES: u64 = 30 * 32_000;
const ROLLOVER_SILENCE_MIN_BYTES: u64 = 9_600; // 300ms @ 16kHz 16-bit mono
const ROLLOVER_SILENCE_RMS_THRESHOLD: f32 = 220.0; // ~ -43.5 dBFS
const ROLLOVER_OVERLAP_BYTES: usize = 48_000; // 1.5s overlap on forced hard cut

fn pcm_rms_i16(pcm: &[u8]) -> f32 {
    let (samples, _) = pcm.as_chunks::<2>();
    if samples.is_empty() {
        return 0.0;
    }
    let sum_sq: f64 = samples
        .iter()
        .map(|pair| {
            let v = i16::from_le_bytes(*pair) as f64;
            v * v
        })
        .sum();
    (sum_sq / samples.len() as f64).sqrt() as f32
}

fn push_overlap(ring: &mut Vec<u8>, pcm: &[u8], cap: usize) {
    if cap == 0 || pcm.is_empty() {
        return;
    }
    if pcm.len() >= cap {
        ring.clear();
        ring.extend_from_slice(&pcm[pcm.len() - cap..]);
    } else {
        ring.extend_from_slice(pcm);
        if ring.len() > cap {
            let excess = ring.len() - cap;
            ring.drain(..excess);
        }
    }
}

fn should_rollover(current_bytes: u64, incoming_bytes: usize, limit_bytes: u64) -> bool {
    current_bytes > 0 && current_bytes.saturating_add(incoming_bytes as u64) > limit_bytes
}

fn should_soft_rollover(current_bytes: u64, limit_bytes: u64, silence_bytes: u64) -> bool {
    limit_bytes >= ROLLOVER_VAD_MIN_LIMIT_BYTES
        && current_bytes >= limit_bytes.saturating_sub(ROLLOVER_SILENCE_WINDOW_BYTES)
        && silence_bytes >= ROLLOVER_SILENCE_MIN_BYTES
}

fn is_boundary_meaningful(ch: char) -> bool {
    ch.is_alphanumeric()
        || matches!(
            ch as u32,
            0x3400..=0x4DBF | 0x4E00..=0x9FFF | 0xF900..=0xFAFF | 0x3040..=0x30FF | 0xAC00..=0xD7AF
        )
}

/// Joins two consecutive segment transcripts, deduplicating any short overlapping
/// boundary phrase introduced by a forced 1.5s audio overlap.
pub(crate) fn append_segment_text(acc: &mut String, next: &str) {
    let next = next.trim();
    if next.is_empty() {
        return;
    }
    if acc.is_empty() {
        acc.push_str(next);
        return;
    }
    let acc_tokens: Vec<char> = acc
        .chars()
        .filter(|ch| is_boundary_meaningful(*ch))
        .flat_map(char::to_lowercase)
        .collect();
    let acc_tail = &acc_tokens[acc_tokens.len().saturating_sub(28)..];

    let mut next_tokens: Vec<(char, usize)> = Vec::new();
    for (idx, ch) in next.char_indices() {
        if is_boundary_meaningful(ch) {
            let end = idx + ch.len_utf8();
            for lower in ch.to_lowercase() {
                next_tokens.push((lower, end));
            }
            if next_tokens.len() >= 28 {
                break;
            }
        }
    }

    let max_k = acc_tail.len().min(next_tokens.len());
    let mut matched_trim: Option<usize> = None;
    for k in (2..=max_k).rev() {
        let suffix = &acc_tail[acc_tail.len() - k..];
        if suffix
            .iter()
            .zip(next_tokens[..k].iter())
            .all(|(a, (b, _))| a == b)
        {
            let has_non_ascii = suffix.iter().any(|ch| !ch.is_ascii());
            // For pure ASCII require at least 5 chars so short tokens like "in" never falsely match.
            if has_non_ascii || k >= 5 {
                matched_trim = Some(next_tokens[k - 1].1);
                break;
            }
        }
    }

    if let Some(trim_at) = matched_trim {
        let remainder = next[trim_at..].trim_start();
        if !remainder.is_empty() {
            if remainder.chars().next().is_some_and(is_boundary_meaningful) {
                while acc.ends_with(['。', '，', ',', '.']) {
                    acc.pop();
                }
            }
            let needs_space = acc
                .chars()
                .last()
                .is_some_and(|c| c.is_ascii_alphanumeric())
                && remainder
                    .chars()
                    .next()
                    .is_some_and(|c| c.is_ascii_alphanumeric());
            if needs_space {
                acc.push(' ');
            }
            acc.push_str(remainder);
        }
    } else {
        acc.push('\n');
        acc.push_str(next);
    }
}

#[cfg(test)]
mod tests {
    use super::{
        ROLLOVER_SILENCE_MIN_BYTES, append_segment_text, merge_usage, pcm_rms_i16, push_overlap,
        should_rollover, should_soft_rollover,
    };
    use serde_json::json;

    #[test]
    fn rollover_preserves_the_exact_safe_boundary() {
        assert!(!should_rollover(0, 1280, 17_280_000));
        assert!(!should_rollover(17_278_720, 1280, 17_280_000));
        assert!(should_rollover(17_280_000, 1280, 17_280_000));
    }

    #[test]
    fn soft_rollover_waits_for_window_and_silence() {
        let limit = 540 * 32_000;
        let window_start = 510 * 32_000;
        assert!(!should_soft_rollover(
            window_start - 1,
            limit,
            ROLLOVER_SILENCE_MIN_BYTES
        ));
        assert!(!should_soft_rollover(
            window_start,
            limit,
            ROLLOVER_SILENCE_MIN_BYTES - 1
        ));
        assert!(should_soft_rollover(
            window_start,
            limit,
            ROLLOVER_SILENCE_MIN_BYTES
        ));
        // Fixture small limits skip soft rollover so byte boundaries stay exact.
        assert!(!should_soft_rollover(
            5_000,
            6_400,
            ROLLOVER_SILENCE_MIN_BYTES
        ));
    }

    #[test]
    fn rms_and_overlap_ring_handle_pcm_bounds() {
        let silent = vec![0u8; 640];
        assert_eq!(pcm_rms_i16(&silent), 0.0);
        let loud: Vec<u8> = (0..320).flat_map(|_| 4000_i16.to_le_bytes()).collect();
        assert!((pcm_rms_i16(&loud) - 4000.0).abs() < 1.0);

        let mut ring = Vec::new();
        push_overlap(&mut ring, &[1, 2, 3, 4], 6);
        push_overlap(&mut ring, &[5, 6, 7, 8], 6);
        assert_eq!(ring, vec![3, 4, 5, 6, 7, 8]);
    }

    #[test]
    fn segment_merge_deduplicates_overlap_and_preserves_distinct_segments() {
        let mut zh = "今天我们讨论语音识别延迟优化。".to_string();
        append_segment_text(&mut zh, "延迟优化的具体方案如下");
        assert_eq!(zh, "今天我们讨论语音识别延迟优化的具体方案如下");

        let mut en = "We should measure latency before".to_string();
        append_segment_text(&mut en, "before shipping the release");
        assert_eq!(en, "We should measure latency before shipping the release");

        let mut distinct = "第1段".to_string();
        append_segment_text(&mut distinct, "第2段");
        assert_eq!(distinct, "第1段\n第2段");
    }

    #[test]
    fn segmented_usage_is_summed_recursively() {
        let mut total = json!({});
        merge_usage(
            &mut total,
            &json!({"input_tokens":10,"input_tokens_details":{"audio_tokens":8}}),
        );
        merge_usage(
            &mut total,
            &json!({"input_tokens":12,"output_tokens":3,"input_tokens_details":{"audio_tokens":9}}),
        );
        assert_eq!(
            total,
            json!({"input_tokens":22,"output_tokens":3,"input_tokens_details":{"audio_tokens":17}})
        );
    }
}

fn event_id() -> String {
    format!("event_{}", uuid::Uuid::new_v4().simple())
}
async fn send_json(socket: &mut Socket, value: Value) -> Result<()> {
    timeout(SEND_TIMEOUT, protocol::send(socket, value))
        .await
        .context("ASR event send timed out")?
}
async fn send_pcm(socket: &mut Socket, pcm: &[u8], binary: bool) -> Result<()> {
    if binary {
        timeout(
            SEND_TIMEOUT,
            socket.send(tokio_tungstenite::tungstenite::Message::Binary(
                Bytes::copy_from_slice(pcm),
            )),
        )
        .await
        .context("ASR PCM send timed out")?
        .context("ASR PCM connection closed")
    } else {
        send_json(socket, protocol::append_event(pcm)).await
    }
}
async fn send_image(
    socket: &mut Socket,
    jpeg: &[u8],
    recognition: bool,
    supported: bool,
) -> Result<()> {
    ensure!(
        supported && !recognition,
        "screen context is unsupported by this model"
    );
    ensure!(
        !jpeg.is_empty() && jpeg.len() <= 190 * 1024 && jpeg.starts_with(&[0xff, 0xd8, 0xff]),
        "screen frame must be a JPEG no larger than 190 KiB"
    );
    send_json(
        socket,
        json!({
            "event_id": event_id(),
            "type": "input_image_buffer.append",
            "image": base64::engine::general_purpose::STANDARD.encode(jpeg),
        }),
    )
    .await
}

pub(crate) fn safe_code(value: &Value) -> String {
    value
        .as_str()
        .unwrap_or("provider_error")
        .chars()
        .filter(|ch| ch.is_ascii_alphanumeric() || "_.-".contains(*ch))
        .take(80)
        .collect()
}
fn check_error(event: &Value) -> Result<()> {
    if event["type"] == "error"
        || event["type"] == "conversation.item.input_audio_transcription.failed"
    {
        bail!("ASR provider error: {}", safe_code(&event["error"]["code"]));
    }
    if event["header"]["event"] == "task-failed" {
        bail!(
            "ASR task failed: {}",
            safe_code(&event["header"]["error_code"])
        );
    }
    Ok(())
}

fn content_text(value: &Value) -> String {
    if let Some(s) = value.as_str() {
        return s.trim().into();
    }
    if let Some(items) = value.as_array() {
        return items.iter().map(content_text).collect();
    }
    if let Some(s) = value["text"]
        .as_str()
        .or_else(|| value["transcript"].as_str())
    {
        return s.trim().into();
    }
    if let Some(content) = value.get("content").or_else(|| value.get("output")) {
        return content_text(content);
    }
    String::new()
}
fn prefer_longer(current: &mut String, value: &Value) {
    let candidate = content_text(value);
    if !candidate.is_empty() && candidate.chars().count() >= current.trim().chars().count() {
        *current = candidate;
    }
}

#[derive(Default)]
struct RealtimeText {
    raw: String,
    response: String,
    partial: String,
    usage: Value,
    wants_response: bool,
    recognition: bool,
    response_started: bool,
    completed: bool,
    final_items: std::collections::HashSet<String>,
}
impl RealtimeText {
    fn new(wants_response: bool, recognition: bool) -> Self {
        Self {
            wants_response,
            recognition,
            ..Default::default()
        }
    }
    fn result(&self) -> &str {
        if self.wants_response && !self.recognition {
            self.response.trim()
        } else {
            self.raw.trim()
        }
    }
    fn consume(&mut self, event: &Value, committed: bool) -> Result<()> {
        check_error(event)?;
        if self.recognition {
            match event["header"]["event"].as_str().unwrap_or("") {
                "result-generated" => {
                    let sentence = &event["payload"]["output"]["sentence"];
                    if sentence["heartbeat"] == true {
                        return Ok(());
                    }
                    let text = sentence["text"].as_str().unwrap_or("");
                    if sentence["sentence_end"] == true {
                        let id = sentence
                            .get("sentence_id")
                            .or_else(|| sentence.get("begin_time"))
                            .map(Value::to_string);
                        if id.is_none_or(|id| self.final_items.insert(id)) {
                            self.raw.push_str(text);
                        }
                        self.partial = self.raw.clone();
                    } else {
                        self.partial = format!("{}{text}", self.raw);
                    }
                    if let Some(usage) = event["payload"].get("usage") {
                        self.usage = usage.clone();
                    }
                }
                "task-finished" => {
                    ensure!(committed, "ASR finished before audio commit");
                    if let Some(usage) = event["payload"].get("usage").filter(|u| !u.is_null()) {
                        self.usage = usage.clone();
                    }
                    self.completed = true;
                }
                _ => {}
            }
        } else {
            match event["type"].as_str().unwrap_or("") {
                "conversation.item.input_audio_transcription.text"
                | "conversation.item.input_audio_transcription.delta" => {
                    self.partial = format!(
                        "{}{}",
                        event["text"]
                            .as_str()
                            .unwrap_or_else(|| event["delta"].as_str().unwrap_or("")),
                        event["stash"].as_str().unwrap_or("")
                    );
                }
                "conversation.item.input_audio_transcription.completed" => {
                    let id = event.get("item_id").map(Value::to_string);
                    if id.is_none_or(|id| self.final_items.insert(id)) {
                        self.raw
                            .push_str(event["transcript"].as_str().unwrap_or(""));
                    }
                    self.partial = self.raw.clone();
                    if committed && !self.wants_response {
                        self.completed = true;
                    }
                }
                "session.finished" => {
                    if self.raw.is_empty() {
                        self.raw = event["transcript"].as_str().unwrap_or("").into();
                    }
                    if committed && !self.wants_response {
                        self.completed = true;
                    }
                }
                kind if committed && self.wants_response && kind.starts_with("response.") => {
                    self.response_started = true;
                    match kind {
                        "response.text.delta" | "response.audio_transcript.delta" => self
                            .response
                            .push_str(event["delta"].as_str().unwrap_or("")),
                        "response.text.done" | "response.audio_transcript.done" => {
                            prefer_longer(&mut self.response, event)
                        }
                        "response.content_part.done" => {
                            prefer_longer(&mut self.response, &event["part"])
                        }
                        "response.output_item.done" => {
                            prefer_longer(&mut self.response, &event["item"]);
                            if event["item"]["status"] == "completed" && !self.response.is_empty() {
                                self.completed = true;
                            }
                        }
                        "response.done" => {
                            let response = &event["response"];
                            ensure!(
                                response["status"] == "completed" || response["status"].is_null(),
                                "ASR response did not complete"
                            );
                            prefer_longer(&mut self.response, response);
                            ensure!(!self.response.is_empty(), "ASR returned an empty response");
                            self.completed = true;
                            self.usage = response["usage"].clone();
                        }
                        _ => {}
                    }
                    self.partial = self.response.clone();
                }
                _ => {}
            }
        }
        ensure!(
            self.raw.len() <= MAX_TEXT_BYTES
                && self.response.len() <= MAX_TEXT_BYTES
                && self.partial.len() <= MAX_TEXT_BYTES,
            "ASR text exceeds size limit"
        );
        Ok(())
    }
}
