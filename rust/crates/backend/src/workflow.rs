// SPDX-License-Identifier: GPL-3.0-only
use crate::{
    billing,
    catalog::asr_model,
    dictionary,
    http::{Completion, TextCallback},
    provider::Provider,
    text,
};
use anyhow::{Result, ensure};
use serde::Serialize;
use serde_json::Value;
use std::{path::Path, sync::Arc};
use tokio_util::sync::CancellationToken;
use vocal_more_core::runtime::{Phase, Status};

pub type StageCallback = Arc<dyn Fn(&str) + Send + Sync>;
#[derive(Clone, Debug, Serialize)]
pub struct Outcome {
    pub raw_text: String,
    pub final_text: String,
    pub paste_text: Option<String>,
    pub billing: Value,
    pub warnings: Vec<String>,
}
pub fn prefix_tail<'a>(full: &'a str, streamed: &str) -> Option<&'a str> {
    let prefix: Vec<_> = streamed.chars().filter(|c| !c.is_whitespace()).collect();
    if prefix.is_empty() {
        return Some(full);
    }
    let mut consumed = 0;
    for (index, ch) in full.char_indices() {
        if consumed >= prefix.len() {
            return Some(&full[index..]);
        }
        if ch.is_whitespace() {
            continue;
        }
        if ch != prefix[consumed] {
            return None;
        }
        consumed += 1;
    }
    (consumed >= prefix.len()).then_some("")
}
pub fn completion_billing(completion: &Completion, seconds: f64) -> Vec<Value> {
    if completion.parts.is_empty() {
        vec![billing::asr(&completion.model, seconds, &completion.usage)]
    } else {
        completion
            .parts
            .iter()
            .map(|p| {
                billing::asr(
                    p["model"].as_str().unwrap_or(&completion.model),
                    p["audio_seconds"].as_f64().unwrap_or(0.0),
                    &p["usage"],
                )
            })
            .collect()
    }
}
/// This predicate must cover every network branch in `finish`. Eligible calls
/// only format the already committed realtime transcript locally.
pub fn finishes_locally(provider: &Provider, status: &Status, streamed: &str) -> bool {
    provider.model_info()["transport"] == "realtime_ws"
        && status.phase == Phase::Completed
        && status.pcm_bytes >= 3200
        && !status.transcript.trim().is_empty()
        && !(provider.config.get("enable_polish") == true
            && !asr_model(provider.model()).is_some_and(|m| m["pipeline"] != "cascade")
            && streamed.is_empty())
}

pub async fn finish(
    provider: Provider,
    status: Status,
    path: &Path,
    streamed: &str,
    cancel: &CancellationToken,
    stage: StageCallback,
    partial: Option<TextCallback>,
) -> Result<Outcome> {
    let _timing = vocal_more_core::diagnostics::Timing::new("workflow");
    ensure!(!cancel.is_cancelled(), "recording cancelled");
    ensure!(status.pcm_bytes >= 3200, "Recording is too short");
    let seconds = status.pcm_bytes as f64 / 32000.0;
    stage("transcribing");
    let realtime = provider.model_info()["transport"] == "realtime_ws";
    let completion =
        if realtime && status.phase == Phase::Completed && !status.transcript.trim().is_empty() {
            Completion {
                text: status.transcript,
                model: provider.model().into(),
                usage: status.usage,
                inline_polished: provider.model_info()["handles_inline_polish"] == true
                    && provider.config.get("enable_polish") == true,
                ..Default::default()
            }
        } else {
            let model = provider.fallback_model();
            provider
                .transcribe_file(path, &model, cancel, partial.clone())
                .await?
        };
    ensure!(!cancel.is_cancelled(), "recording cancelled");
    let raw = completion.text.trim().to_owned();
    ensure!(!raw.is_empty(), "Empty transcription");
    let mut final_text = dictionary::normalize_text(&raw, &provider.entries);
    let mut bills = completion_billing(&completion, seconds);
    let mut warnings = vec![];
    // Python uses the selected pipeline to decide whether a second text model
    // is needed, including after an HTTP fallback from that realtime model.
    let single_pass = asr_model(provider.model()).is_some_and(|m| m["pipeline"] != "cascade");
    if provider.config.get("enable_polish") == true && !single_pass && streamed.is_empty() {
        stage("polishing");
        match provider.polish(&raw, cancel, partial).await {
            Ok(polished) => {
                final_text = polished.text;
                bills.push(billing::polish(
                    &polished.model,
                    provider.config.get("llm.enable_thinking") == true,
                    &polished.usage,
                ));
            }
            Err(error) => {
                ensure!(!cancel.is_cancelled(), "recording cancelled");
                warnings.push(format!("Text polishing failed: {error}"));
            }
        }
    }
    final_text = text::bilingual(&final_text);
    if provider.config.get("llm.polish_mode") == "prompt"
        && provider.config.get("enable_polish") == true
    {
        final_text = text::sanitize_prompt(&final_text);
    }
    let paste_text = if provider.config.get("auto_paste") != true {
        None
    } else if streamed.is_empty() {
        Some(final_text.clone())
    } else {
        match prefix_tail(&raw, streamed) {
            Some(tail) => {
                let tail = text::bilingual(&dictionary::normalize_text(tail, &provider.entries));
                (!tail.trim().is_empty()).then(|| format!(" {}", tail.trim()))
            }
            None => {
                warnings.push("Streaming paste could not be aligned with the final transcription; the remaining text was not pasted to avoid duplicates.".into());
                None
            }
        }
    };
    Ok(Outcome {
        raw_text: raw,
        final_text,
        paste_text,
        billing: billing::merge(&bills),
        warnings,
    })
}
