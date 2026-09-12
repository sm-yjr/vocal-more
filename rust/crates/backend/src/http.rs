// SPDX-License-Identifier: GPL-3.0-only
use crate::{
    catalog::asr_model,
    dictionary,
    provider::{MAX_TEXT_BYTES, Provider, safe_code},
    text::{self, PromptKind},
    wave::{WaveChunks, join_segments, wav_bytes},
};
use anyhow::{Context, Result, bail, ensure};
use base64::{Engine, engine::general_purpose::STANDARD};
use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{path::Path, sync::Arc, time::Duration};
use tokio_util::sync::CancellationToken;

const MAX_EVENT_BYTES: usize = 1024 * 1024;
const MULTIMODAL: &str = "/api/v1/services/aigc/multimodal-generation/generation";
pub type TextCallback = Arc<dyn Fn(&str) + Send + Sync>;

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Completion {
    pub text: String,
    pub model: String,
    pub usage: Value,
    pub inline_polished: bool,
    pub parts: Vec<Value>,
}

impl Provider {
    pub async fn probe_model(&self, model: &str, cancel: &CancellationToken) -> Result<()> {
        let client = reqwest::Client::builder()
            .use_preconfigured_tls(vocal_more_core::protocol::tls_config()?)
            .redirect(reqwest::redirect::Policy::none())
            .timeout(Duration::from_secs(10))
            .build()?;
        let payload = json!({"model":model,"input":{"messages":[{"role":"user","content":[{"text":"Reply with OK."}]}]},
            "parameters":{"enable_thinking":false,"max_tokens":1}});
        let mut request = client
            .post(format!("{}{MULTIMODAL}", self.endpoints.http))
            .json(&payload);
        if let Some(key) = self.api_key()? {
            request = request.bearer_auth(key);
        }
        tokio::select! {
            _=cancel.cancelled()=>bail!("model check cancelled"),
            response=request.send()=>{
                let response=response.map_err(|_|anyhow::anyhow!("Model check connection or response timed out"))?;
                // Availability is an HTTP admission check. The intentional
                // one-token output may end with finish_reason=length.
                ensure!(response.status().is_success(),"provider HTTP {}",response.status().as_u16());Ok(())
            }
        }
    }
    pub fn fallback_model(&self) -> String {
        if self.model_info()["transport"] != "realtime_ws" {
            return self.model().into();
        }
        if let Some(model) = self.model().strip_suffix("-realtime")
            && asr_model(model).is_some_and(|m| m["transport"] == "omni_offline")
        {
            return model.into();
        }
        self.model_info()["fallback_model"]
            .as_str()
            .unwrap_or("qwen3-asr-flash")
            .into()
    }

    pub async fn transcribe_file(
        &self,
        path: &Path,
        model: &str,
        cancel: &CancellationToken,
        partial: Option<TextCallback>,
    ) -> Result<Completion> {
        let info = asr_model(model).context("unknown file transcription model")?;
        ensure!(
            info["transport"] == "omni_offline" || info["transport"] == "short_file",
            "model requires realtime transport"
        );
        let mut chunks = WaveChunks::open(path)?;
        let mut result = Completion {
            model: model.into(),
            inline_polished: info["handles_inline_polish"] == true
                && self.config.get("enable_polish") == true,
            ..Default::default()
        };
        let mut texts = Vec::new();
        while !cancel.is_cancelled() {
            let Some(pcm) = chunks.next_chunk()? else {
                break;
            };
            let mut completed = if info["transport"] == "omni_offline" {
                let payload = self.omni_request(model, &pcm)?;
                self.request_json(
                    "/compatible-mode/v1/chat/completions",
                    payload,
                    true,
                    true,
                    cancel,
                    partial.clone(),
                )
                .await?
            } else {
                let payload = self.short_file_request(model, &pcm)?;
                self.request_json(MULTIMODAL, payload, false, false, cancel, None)
                    .await?
            };
            completed.model = model.into();
            result.parts.push(json!({"model":model,"audio_seconds":pcm.len() as f64/32000.0,"usage":completed.usage}));
            result.usage = completed.usage;
            texts.push(completed.text);
            ensure!(
                texts.iter().map(String::len).sum::<usize>() <= MAX_TEXT_BYTES,
                "transcription exceeds size limit"
            );
        }
        ensure!(!cancel.is_cancelled(), "file transcription cancelled");
        result.text = join_segments(&texts);
        if result.inline_polished {
            result.text = text::structured(&result.text, &self.config);
        }
        Ok(result)
    }

    pub fn short_file_request(&self, model: &str, pcm: &[u8]) -> Result<Value> {
        let audio = format!("data:audio/wav;base64,{}", STANDARD.encode(wav_bytes(pcm)?));
        let corpus = self.corpus();
        let mut messages = Vec::new();
        if !corpus.is_empty() {
            messages.push(json!({"role":"system","content":[{"text":corpus}]}));
        }
        messages.push(json!({"role":"user","content":[{"audio":audio}]}));
        let mut options = json!({"enable_itn":true});
        if self.config.get("asr.language") != "auto" {
            options["language"] = self.config.get("asr.language").clone();
        }
        Ok(
            json!({"model":model,"input":{"messages":messages},"parameters":{"result_format":"message","asr_options":options}}),
        )
    }

    pub fn omni_request(&self, model: &str, pcm: &[u8]) -> Result<Value> {
        let audio = format!("data:;base64,{}", STANDARD.encode(wav_bytes(pcm)?));
        let prompt = if self.config.get("enable_polish") == true {
            text::build_prompt(
                &self.config,
                PromptKind::Inline,
                &dictionary::format_prompt(&self.entries),
                &self.context,
            )
        } else {
            "请将以下音频准确转录为文字，直接输出转录结果。".into()
        };
        Ok(
            json!({"model":model,"messages":[{"role":"system","content":prompt},{"role":"user","content":[{"type":"input_audio","input_audio":{"data":audio,"format":"wav"}}]}],"modalities":["text"],"stream":true,"stream_options":{"include_usage":true}}),
        )
    }

    pub fn polish_request(&self, text: &str, _stream: bool) -> Value {
        let llm = &self.config.0["llm"];
        let system = text::build_prompt(
            &self.config,
            PromptKind::System,
            &dictionary::format_prompt(&self.entries),
            &self.context,
        );
        json!({"model":llm["model"],"input":{"messages":[{"role":"system","content":[{"text":system}]},{"role":"user","content":[{"text":text}]}]},
            "parameters":{"enable_thinking":llm["enable_thinking"],"temperature":llm["temperature"],"max_tokens":llm["max_tokens"],"incremental_output":true}})
    }

    pub async fn polish(
        &self,
        text: &str,
        cancel: &CancellationToken,
        partial: Option<TextCallback>,
    ) -> Result<Completion> {
        let normalized = dictionary::normalize_text(text, &self.entries);
        if normalized.trim().is_empty() {
            return Ok(Completion {
                text: normalized,
                ..Default::default()
            });
        }
        let mut response = self
            .request_json(
                MULTIMODAL,
                self.polish_request(&normalized, true),
                true,
                false,
                cancel,
                partial,
            )
            .await?;
        response.text = text::structured(&response.text, &self.config);
        response.model = self.config.get("llm.model").as_str().unwrap().into();
        Ok(response)
    }

    pub async fn request_json(
        &self,
        path: &str,
        payload: Value,
        stream: bool,
        compatible: bool,
        cancel: &CancellationToken,
        partial: Option<TextCallback>,
    ) -> Result<Completion> {
        ensure!(
            matches!(path, MULTIMODAL | "/compatible-mode/v1/chat/completions"),
            "unsupported provider API path"
        );
        let client = reqwest::Client::builder()
            .use_preconfigured_tls(vocal_more_core::protocol::tls_config()?)
            .redirect(reqwest::redirect::Policy::none())
            .connect_timeout(Duration::from_secs(10))
            .read_timeout(Duration::from_secs(90))
            .timeout(Duration::from_secs(120))
            .pool_max_idle_per_host(0)
            .build()?;
        let mut request = client
            .post(format!("{}{path}", self.endpoints.http))
            .json(&payload);
        if let Some(key) = self.api_key()? {
            request = request.bearer_auth(key);
        }
        if stream && !compatible {
            request = request.header("X-DashScope-SSE", "enable");
        }
        tokio::select! {
            biased;
            _ = cancel.cancelled() => bail!("provider request cancelled"),
            result = async {
                let response = request.send().await.map_err(|_|anyhow::anyhow!("provider connection or response timed out"))?;
                ensure!(response.status().is_success(),"provider HTTP {}",response.status().as_u16());
                let sse = response.headers().get("content-type").and_then(|v|v.to_str().ok()).is_some_and(|v| v.contains("text/event-stream"));
                let mut bytes = response.bytes_stream();
                let mut result = Completion::default();
                if !sse {
                    let mut body = Vec::new();
                    while let Some(chunk) = bytes.next().await {
                        let chunk = chunk.map_err(|_|anyhow::anyhow!("provider response interrupted"))?;
                        ensure!(body.len()+chunk.len() <= MAX_EVENT_BYTES,"provider response exceeds size limit"); body.extend_from_slice(&chunk);
                    }
                    let value: Value = serde_json::from_slice(&body).context("provider returned invalid JSON")?;
                    check_http_error(&value)?;
                    let choice = choices(&value,compatible);
                    result.text = extract_content(&choice["message"]["content"]);
                    result.usage = value["usage"].clone();
                } else {
                    let mut decoder = SseDecoder::default(); let mut finished = false;
                    while let Some(chunk) = bytes.next().await {
                        let chunk = chunk.map_err(|_|anyhow::anyhow!("provider stream interrupted"))?;
                        for event in decoder.push(&chunk)? {
                            let Some(value) = event else { finished = true; continue; };
                            check_http_error(&value)?;
                            let choice = choices(&value,compatible);
                            let text = extract_content(if compatible { &choice["delta"]["content"] } else { &choice["message"]["content"] });
                            ensure!(result.text.len()+text.len() <= MAX_TEXT_BYTES,"provider text exceeds size limit");
                            result.text.push_str(&text);
                            if !text.is_empty() && let Some(callback) = &partial { callback(&result.text); }
                            if let Some(usage) = value.get("usage").filter(|u| !u.is_null()) { result.usage = usage.clone(); }
                            if let Some(reason) = choice["finish_reason"].as_str().filter(|r| !r.is_empty() && *r != "null") {
                                ensure!(reason == "stop", "provider output did not complete: {}",safe_code(&json!(reason)));
                                finished = true;
                            }
                        }
                    }
                    decoder.finish()?;
                    ensure!(finished,"provider stream closed before completion");
                }
                ensure!(result.text.len() <= MAX_TEXT_BYTES,"provider text exceeds size limit");
                result.text = result.text.trim().into();
                Ok(result)
            } => result,
        }
    }
}

fn check_http_error(value: &Value) -> Result<()> {
    if value.get("error").is_some() {
        bail!("provider error: {}", safe_code(&value["error"]["code"]));
    }
    if value.get("code").is_some_and(|c| !c.is_null() && c != "") {
        bail!("provider error: {}", safe_code(&value["code"]));
    }
    Ok(())
}
fn choices(value: &Value, compatible: bool) -> &Value {
    if compatible {
        &value["choices"][0]
    } else {
        &value["output"]["choices"][0]
    }
}
fn extract_content(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Array(items) => items
            .iter()
            .map(|item| {
                item.as_str()
                    .or_else(|| item["text"].as_str())
                    .unwrap_or("")
            })
            .collect(),
        _ => String::new(),
    }
}

#[derive(Default)]
pub struct SseDecoder {
    pending: Vec<u8>,
    event: String,
}
impl SseDecoder {
    pub fn push(&mut self, bytes: &[u8]) -> Result<Vec<Option<Value>>> {
        // A TCP read can contain many small events; limit each event, not the
        // size of the caller's read. Preserve UTF-8 split across packet borders.
        let mut result = Vec::new();
        for byte in bytes {
            if *byte != b'\n' {
                ensure!(
                    self.pending.len() + self.event.len() < MAX_EVENT_BYTES,
                    "SSE event exceeds size limit"
                );
                self.pending.push(*byte);
                continue;
            }
            let line = std::str::from_utf8(&self.pending)
                .context("invalid UTF-8 in SSE event")?
                .trim_end_matches('\r');
            if line.is_empty() {
                let data = self.event.trim();
                if data == "[DONE]" {
                    result.push(None);
                } else if !data.is_empty() {
                    result.push(Some(
                        serde_json::from_str(data).context("invalid SSE JSON")?,
                    ));
                }
                self.event.clear();
            } else if let Some(data) = line.strip_prefix("data:") {
                self.event.push_str(data.strip_prefix(' ').unwrap_or(data));
                self.event.push('\n');
            }
            self.pending.clear();
        }
        Ok(result)
    }
    pub fn finish(&self) -> Result<()> {
        ensure!(
            self.pending.is_empty() && self.event.trim().is_empty(),
            "truncated SSE event"
        );
        Ok(())
    }
}
