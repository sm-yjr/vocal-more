// SPDX-License-Identifier: GPL-3.0-only
//! DashScope wire protocols; no Python SDK and no process-global API key.
use crate::{
    catalog::asr_model,
    config::Config,
    dictionary::{self, Entry},
    text::{self, PromptKind},
};
use anyhow::{Context, Result, bail, ensure};
use bytes::Bytes;
use futures_util::SinkExt;
use serde_json::{Value, json};
use std::{future::pending, sync::Arc, time::Duration};
use tokio::{
    sync::mpsc,
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
}
impl Default for Endpoints {
    fn default() -> Self {
        Self {
            http: "https://dashscope.aliyuncs.com".into(),
            realtime: None,
            recognition: "wss://dashscope.aliyuncs.com/api-ws/v1/inference".into(),
            fixture: false,
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
        })
    }
}

#[derive(Clone)]
pub struct Provider {
    pub config: Config,
    pub entries: Vec<Entry>,
    pub context: String,
    pub endpoints: Endpoints,
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
            endpoints,
            key: Arc::from(key),
        }
    }
    pub fn model(&self) -> &str {
        self.config.get("asr.model").as_str().unwrap()
    }
    pub fn model_info(&self) -> &'static Value {
        asr_model(self.model()).expect("normalized ASR model")
    }
    pub fn wants_response(&self) -> bool {
        self.model_info()["always_request_response"] == true
            || (self.config.get("enable_polish") == true
                && self.model_info()["handles_inline_polish"] == true)
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

    pub fn session_update(&self) -> Value {
        let info = self.model_info();
        let mut session = json!({"modalities":["text"], "voice":null,"input_audio_format":"pcm16", "output_audio_format":"pcm16", "input_audio_transcription":{"model":null},"turn_detection":null});
        let dict = dictionary::format_prompt(&self.entries);
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
            session["instructions"] =
                json!(text::build_prompt(&self.config, kind, &dict, &self.context));
        } else if info
            .get("input_audio_transcription_model")
            .is_some_and(|m| !m.is_null())
        {
            session["voice"] = info.get("voice").cloned().unwrap_or_else(|| json!("Tina"));
            session["input_audio_transcription"] =
                json!({"model":info["input_audio_transcription_model"]});
            session["enable_search"] = json!(false);
            if self.wants_response() {
                session["instructions"] = json!(text::build_prompt(
                    &self.config,
                    PromptKind::Inline,
                    &dict,
                    &self.context
                ));
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
        let mut parameters = json!({"format":"pcm","sample_rate":16000,"semantic_punctuation_enabled":true,"heartbeat":true});
        if self.model_info()["supports_instant_hotwords"] == true {
            parameters["semantic_punctuation_enabled"] = json!(false);
            parameters["punctuation_prediction_enabled"] = json!(true);
            let mut vocabulary = serde_json::Map::new();
            for term in self
                .corpus()
                .lines()
                .map(str::trim)
                .filter(|s| !s.is_empty())
                .take(2000)
            {
                vocabulary.insert(term.into(), json!(4));
            }
            if !vocabulary.is_empty() {
                parameters["vocabulary"] = Value::Object(vocabulary);
            }
        }
        if self.config.get("asr.language") != "auto" {
            parameters["language_hints"] = json!([self.config.get("asr.language")]);
        }
        let input = if self.context.is_empty() {
            json!({})
        } else {
            json!({"context":[{"role":"user","content":[{"type":"input_text","text":self.context.chars().take(400).collect::<String>()}]}]})
        };
        json!({"header":{"action":"run-task","task_id":task,"streaming":"duplex"},"payload":{"task_group":"audio","task":"asr","function":"recognition","model":self.model(),"parameters":parameters,"input":input}})
    }

    async fn realtime(
        self,
        mut input: mpsc::Receiver<NetworkInput>,
        cancel: CancellationToken,
        reporter: NetworkReporter,
    ) -> Result<String> {
        tokio::select! {
            biased;
            _ = cancel.cancelled() => bail!("ASR cancelled"),
            result = async {
                let recognition = self.model_info()["protocol"] == "audio_recognition";
                let endpoint = if recognition { &self.endpoints.recognition } else {
                    self.endpoints.realtime.as_deref().unwrap_or_else(|| {
                        self.config.get("asr.realtime_url").as_str().filter(|s| !s.is_empty()).unwrap_or("wss://dashscope.aliyuncs.com/api-ws/v1/realtime")
                    })
                };
                let mut url = url::Url::parse(endpoint)?;
                if !recognition { url.query_pairs_mut().append_pair("model",self.model()); }
                let mut socket = timeout(CONNECT_TIMEOUT, protocol::connect_url(url,self.api_key()?)).await.context("ASR connection timed out")??;
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
                let mut committed = false; let mut total_bytes = 0_u64;
                let mut completion_deadline = None;
                let mut response_start_deadline = None;
                let mut heartbeat = tokio::time::interval(Duration::from_secs(15)); heartbeat.tick().await;
                loop {
                    enum Event { Input(Option<NetworkInput>), Server(Value), Heartbeat, Timeout }
                    let event = tokio::select! {
                        value = input.recv(), if !committed => Event::Input(value),
                        value = protocol::receive(&mut socket) => Event::Server(value?),
                        _ = heartbeat.tick() => Event::Heartbeat,
                        _ = async { match completion_deadline { Some(d) => tokio::time::sleep_until(d).await, None => pending().await } } => Event::Timeout,
                        _ = async { match response_start_deadline { Some(d) => tokio::time::sleep_until(d).await, None => pending().await } }, if !accumulator.response_started => Event::Timeout,
                    };
                    match event {
                        Event::Timeout => bail!("ASR result deadline exceeded; recover from recorded audio"),
                        Event::Heartbeat => {
                            timeout(SEND_TIMEOUT,socket.send(tokio_tungstenite::tungstenite::Message::Ping(Bytes::new()))).await.context("ASR heartbeat timed out")??;
                        },
                        Event::Input(Some(NetworkInput::Pcm(pcm))) => {
                            total_bytes += pcm.len() as u64;
                            if !self.endpoints.fixture && self.model().starts_with("qwen3.5-omni") {
                                let seconds = if self.model().contains("flash") { 480 } else { 600 };
                                ensure!(total_bytes <= seconds * 32000, "ASR context duration exceeded; recover from recorded audio");
                            }
                            pending_pcm.extend_from_slice(&pcm);
                            while pending_pcm.len() >= 3200 {
                                send_pcm(&mut socket,&pending_pcm[..3200],recognition).await?;
                                pending_pcm.drain(..3200);
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
                                return Ok(result);
                            }
                        },
                    }
                }
            } => result,
        }
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
