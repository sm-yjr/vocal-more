// SPDX-License-Identifier: GPL-3.0-only
use crate::{
    billing, dictionary, guard,
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
        && !(provider.needs_second_stage_polish() && streamed.is_empty())
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
    let mut warnings = vec![];
    let realtime = provider.model_info()["transport"] == "realtime_ws";
    let bypass_raw = status.raw_transcript.trim().to_owned();
    let completion =
        if realtime && status.phase == Phase::Completed && !status.transcript.trim().is_empty() {
            let mut primary = status.transcript;
            if !bypass_raw.is_empty()
                && primary.trim() != bypass_raw
                && let Some(reason) =
                    guard::detect_answer_drift(&primary, &bypass_raw, &provider.config)
            {
                warnings.push(reason.warning().into());
                primary = bypass_raw.clone();
            }
            Completion {
                text: primary,
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
    ensure!(
        !guard::is_short_audio_hallucination(&raw, seconds),
        "No clear speech detected"
    );
    let mut final_text = dictionary::normalize_text(&raw, &provider.entries);
    let mut bills = completion_billing(&completion, seconds);
    // Dual-track routing: single-pass models (inline Omni, native ASR, or
    // hybrid_asr in default minimal dictation) skip the second LLM hop.
    // Cascade models and hybrid_asr with advanced polish/prompt/translation
    // settings route the accurate ASR transcript through the polish LLM.
    if provider.needs_second_stage_polish() && streamed.is_empty() {
        stage("polishing");
        match provider.polish(&raw, cancel, partial).await {
            Ok(polished) => {
                if let Some(reason) =
                    guard::detect_answer_drift(&polished.text, &raw, &provider.config)
                {
                    warnings.push(reason.warning().into());
                } else {
                    final_text = polished.text;
                }
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{config::Config, provider::Endpoints};
    use serde_json::json;

    #[tokio::test]
    async fn omni_assistant_reply_drift_falls_back_to_bypass_transcript() -> Result<()> {
        let mut config = Config::default();
        config.apply_update("asr.model", &json!("qwen3.8-omni-flash-realtime"))?;
        let provider = Provider::new(
            config,
            vec![],
            "",
            Endpoints::loopback("http://127.0.0.1:1", "ws://127.0.0.1:1")?,
            Some("fixture-key"),
        );
        let status = Status {
            phase: Phase::Completed,
            pcm_bytes: 64_000, // 2.0s
            transcript: "好的，为您解答：这个接口返回 500 通常是后端空指针异常导致的。".into(),
            raw_transcript: "这个接口为什么返回500？".into(),
            usage: json!({"input_tokens": 100, "output_tokens": 30}),
            ..Default::default()
        };
        let outcome = finish(
            provider,
            status,
            Path::new("/nonexistent.wav"),
            "",
            &CancellationToken::new(),
            Arc::new(|_| {}),
            None,
        )
        .await?;
        assert_eq!(outcome.final_text, "这个接口为什么返回 500？");
        assert!(!outcome.warnings.is_empty());
        Ok(())
    }

    #[tokio::test]
    async fn short_silence_hallucination_is_rejected_before_paste() -> Result<()> {
        let provider = Provider::new(
            Config::default(),
            vec![],
            "",
            Endpoints::loopback("http://127.0.0.1:1", "ws://127.0.0.1:1")?,
            Some("fixture-key"),
        );
        let status = Status {
            phase: Phase::Completed,
            pcm_bytes: 16_000, // 0.5s
            transcript: "字幕由 Amara.org 社区提供".into(),
            raw_transcript: "字幕由 Amara.org 社区提供".into(),
            ..Default::default()
        };
        let err = finish(
            provider,
            status,
            Path::new("/nonexistent.wav"),
            "",
            &CancellationToken::new(),
            Arc::new(|_| {}),
            None,
        )
        .await
        .unwrap_err();
        assert!(err.to_string().contains("No clear speech detected"));
        Ok(())
    }
}
