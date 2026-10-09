// SPDX-License-Identifier: GPL-3.0-only
//! Opt-in, content-free latency diagnostics. No audio, text, paths or credentials.
use std::{
    sync::LazyLock,
    time::{Duration, Instant},
};
static ENABLED: LazyLock<bool> =
    LazyLock::new(|| std::env::var_os("VOCAL_MORE_TRACE_TIMINGS").is_some());
#[derive(Clone, Copy)]
pub enum Stage {
    HotkeyToMain,
    MainToBackend,
    BackendToMain,
    BackendRequest,
    CapsuleShow,
    AudioSourceReady,
    FirstPcm,
    AsrReady,
    FinishToAsrDone,
    DurableCommit,
    FinishToFinalResult,
    ModelProcessing,
    AxCapture,
    NativePasteWait,
    NativePastePost,
    StateUiUpdate,
    PreviewUiUpdate,
}
impl Stage {
    fn name(self) -> &'static str {
        match self {
            Self::HotkeyToMain => "hotkey_to_main",
            Self::MainToBackend => "main_to_backend",
            Self::BackendToMain => "backend_to_main",
            Self::BackendRequest => "backend_request",
            Self::CapsuleShow => "hotkey_to_capsule",
            Self::AudioSourceReady => "audio_source_ready",
            Self::FirstPcm => "first_pcm",
            Self::AsrReady => "asr_ready",
            Self::FinishToAsrDone => "finish_to_asr_done",
            Self::DurableCommit => "durable_commit",
            Self::FinishToFinalResult => "finish_to_final_result",
            Self::ModelProcessing => "model_processing",
            Self::AxCapture => "ax_capture",
            Self::NativePasteWait => "native_paste_wait",
            Self::NativePastePost => "native_paste_post",
            Self::StateUiUpdate => "state_ui_update",
            Self::PreviewUiUpdate => "preview_ui_update",
        }
    }
}
pub fn record(stage: Stage, elapsed: Duration) {
    if *ENABLED {
        eprintln!(
            "vocal_more_timing stage={} elapsed_ms={:.3}",
            stage.name(),
            elapsed.as_secs_f64() * 1000.0
        );
    }
}
pub struct Span {
    stage: Stage,
    start: Option<Instant>,
}
impl Span {
    pub fn new(stage: Stage) -> Self {
        Self {
            stage,
            start: (*ENABLED).then(Instant::now),
        }
    }
}
impl Drop for Span {
    fn drop(&mut self) {
        if let Some(start) = self.start {
            record(self.stage, start.elapsed());
        }
    }
}
pub struct Timing(&'static str, Option<Instant>);
impl Timing {
    pub fn new(name: &'static str) -> Self {
        Self(name, (*ENABLED).then(Instant::now))
    }
}
impl Drop for Timing {
    fn drop(&mut self) {
        if let Some(started) = self.1 {
            eprintln!(
                "[perf] {} ms={:.3}",
                self.0,
                started.elapsed().as_secs_f64() * 1000.0
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn timing_labels_are_fixed_and_content_free() {
        for stage in [
            Stage::HotkeyToMain,
            Stage::MainToBackend,
            Stage::BackendToMain,
            Stage::BackendRequest,
            Stage::CapsuleShow,
            Stage::AudioSourceReady,
            Stage::FirstPcm,
            Stage::AsrReady,
            Stage::FinishToAsrDone,
            Stage::DurableCommit,
            Stage::FinishToFinalResult,
            Stage::ModelProcessing,
            Stage::AxCapture,
            Stage::NativePasteWait,
            Stage::NativePastePost,
            Stage::StateUiUpdate,
            Stage::PreviewUiUpdate,
        ] {
            assert!(
                stage
                    .name()
                    .bytes()
                    .all(|b| b.is_ascii_lowercase() || b == b'_')
            );
        }
    }
}
