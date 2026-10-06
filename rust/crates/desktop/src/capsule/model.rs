// SPDX-License-Identifier: GPL-3.0-only
//! Point geometry and time integration shared by the native renderer and its
//! parity fixtures. Constants deliberately match the shipping AppKit capsule.
use serde::Serialize;

pub const COMPACT_WIDTH: f64 = 240.0;
pub const COMPACT_HEIGHT: f64 = 80.0;
pub const RECORDING_WIDTH: f64 = 360.0;
pub const NOTICE_WIDTH: f64 = 400.0;
pub const MAX_HEIGHT: f64 = 200.0;
pub const BAR_COUNT: usize = 80;
pub const COMPACT_BAR_COUNT: usize = 10;
pub const SILENCE_THRESHOLD: f64 = 0.005;
pub const HIDE_DELAY: f64 = 0.25;
pub const FAILURE_DURATION: f64 = 4.0;

#[derive(Clone, Copy, Debug, PartialEq, Serialize)]
pub struct Frame {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

impl Frame {
    pub fn new(x: f64, y: f64, width: f64, height: f64) -> Self {
        Self {
            x,
            y,
            width,
            height,
        }
    }
    #[cfg(test)]
    pub fn max_x(self) -> f64 {
        self.x + self.width
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum State {
    Hidden,
    Recording,
    Processing,
    ConnectionError,
}

#[derive(Clone, Debug, Serialize)]
pub struct Layout {
    pub surface: Frame,
    pub cancel: Frame,
    pub finish: Frame,
    pub recording_label: Frame,
    pub thinking_label: Frame,
    pub progress_track: Frame,
    pub progress_fill: Frame,
    pub streaming: Frame,
    pub bars: Vec<Frame>,
    pub cancel_visible: bool,
    pub finish_visible: bool,
    pub recording_label_visible: bool,
    pub thinking_label_visible: bool,
    pub progress_visible: bool,
    pub streaming_visible: bool,
    pub waveform_visible: bool,
}

pub struct LayoutInput<'a> {
    pub width: f64,
    pub height: f64,
    pub mode: &'a str,
    pub state: State,
    pub expanded: bool,
    pub label_width: f64,
    pub thinking_width: f64,
    pub progress: f64,
    pub bar_heights: &'a [f64; BAR_COUNT],
}

pub fn display_mode(mode: &str, prompt: bool) -> String {
    match (mode, prompt) {
        ("pushToTalk", true) => "promptPushToTalk",
        ("handsFree", true) => "prompt",
        _ => mode,
    }
    .into()
}

pub fn is_prompt(mode: &str) -> bool {
    matches!(mode, "prompt" | "promptPushToTalk")
}
pub fn is_push_to_talk(mode: &str) -> bool {
    matches!(mode, "pushToTalk" | "promptPushToTalk")
}

pub fn visible_text(text: &str) -> String {
    // Python's bound counts Unicode scalar values, not UTF-8 or UTF-16 units.
    let count = text.chars().count();
    if count <= 4000 {
        text.into()
    } else {
        format!("…{}", text.chars().skip(count - 4000).collect::<String>())
    }
}

pub fn preferred_height(measured_text_height: f64, line_height: f64) -> f64 {
    (measured_text_height.ceil().max(line_height).min(122.0) + 54.0 + 24.0)
        .ceil()
        .min(MAX_HEIGHT)
}

pub fn translation<'a>(language: &str, key: &'a str) -> &'a str {
    match (language, key) {
        ("zh", "prompt") => "提示词",
        ("zh", "transcribing") => "识别中",
        ("zh", "polishing") => "润色中",
        ("zh", "understanding") => "理解中",
        ("zh", "searching") => "搜索中",
        ("zh", "generating") => "生成中",
        ("zh", "failure") => "听写失败",
        (_, "prompt") => "Prompt",
        (_, "transcribing") => "Transcribing",
        (_, "polishing") => "Polishing",
        (_, "understanding") => "Understanding",
        (_, "searching") => "Searching",
        (_, "generating") => "Generating",
        (_, "failure") => "Dictation failed",
        _ => key,
    }
}

pub fn layout(input: LayoutInput<'_>) -> Layout {
    let LayoutInput {
        width,
        height,
        mode,
        state,
        expanded,
        label_width,
        thinking_width,
        progress,
        bar_heights,
    } = input;
    let recording = state == State::Recording;
    let processing = state == State::Processing;
    let connection = state == State::ConnectionError;
    let label_visible = recording && is_prompt(mode);
    let label_width = if label_visible {
        label_width.ceil()
    } else {
        0.0
    };
    let thinking_width = thinking_width.ceil().max(112.0);
    let mut compact_width: f64 = match mode {
        "pushToTalk" => 64.0,
        "handsFree" => 126.0,
        "prompt" => 178.0,
        "promptPushToTalk" => 112.0,
        _ => 126.0,
    };
    if processing {
        compact_width = compact_width
            .max(184.0)
            .max(thinking_width + 8.0 + 48.0 + 24.0);
    }
    if label_visible {
        compact_width = compact_width
            .max(label_width + 8.0 + 38.0 + if mode == "prompt" { 80.0 } else { 24.0 });
    }
    let surface_width = if expanded {
        width - 40.0
    } else {
        compact_width
    };
    let surface_height = if expanded { height - 24.0 } else { 36.0 };
    let buttons_visible = recording && matches!(mode, "handsFree" | "prompt");
    let row_center = if expanded {
        surface_height - 18.0
    } else {
        18.0
    };
    let bar_count = if expanded {
        let inset = if buttons_visible { 42.0 } else { 12.0 };
        let space =
            surface_width - 2.0 * inset - label_width - if label_width > 0.0 { 8.0 } else { 0.0 };
        (((space + 2.0) / 4.0).floor() as usize).clamp(COMPACT_BAR_COUNT, BAR_COUNT)
    } else {
        COMPACT_BAR_COUNT
    };
    let bars_width = bar_count as f64 * 4.0 - 2.0;
    let label_gap = if label_width > 0.0 { 8.0 } else { 0.0 };
    let group_x = (surface_width - label_width - label_gap - bars_width) / 2.0;
    let bars_x = group_x + label_width + label_gap;
    let processing_width = thinking_width + if connection { 0.0 } else { 8.0 + 48.0 };
    let processing_x = (surface_width - processing_width) / 2.0;
    Layout {
        surface: Frame::new(
            (width - surface_width) / 2.0,
            12.0,
            surface_width,
            surface_height,
        ),
        cancel: Frame::new(10.0, row_center - 11.0, 22.0, 22.0),
        finish: Frame::new(surface_width - 32.0, row_center - 11.0, 22.0, 22.0),
        recording_label: Frame::new(group_x, row_center - 9.0, label_width, 18.0),
        thinking_label: Frame::new(processing_x, row_center - 9.0, thinking_width, 18.0),
        progress_track: Frame::new(
            processing_x + thinking_width + 8.0,
            row_center - 1.5,
            48.0,
            3.0,
        ),
        progress_fill: Frame::new(0.0, 0.0, 48.0 * progress, 3.0),
        streaming: Frame::new(
            12.0,
            12.0,
            surface_width - 24.0,
            (surface_height - 54.0).max(1.0),
        ),
        bars: (0..bar_count)
            .map(|i| {
                Frame::new(
                    bars_x + i as f64 * 4.0,
                    row_center - bar_heights[i] / 2.0,
                    2.0,
                    bar_heights[i],
                )
            })
            .collect(),
        cancel_visible: buttons_visible || connection,
        finish_visible: buttons_visible && !connection,
        recording_label_visible: label_visible,
        thinking_label_visible: processing || connection,
        progress_visible: processing,
        streaming_visible: expanded,
        waveform_visible: recording,
    }
}

#[derive(Clone, Debug)]
pub struct Animation {
    pub phase: f64,
    pub levels: [f64; BAR_COUNT],
    pub heights: [f64; BAR_COUNT],
    pub progress: f64,
}

impl Default for Animation {
    fn default() -> Self {
        Self {
            phase: 0.0,
            levels: [0.0; BAR_COUNT],
            heights: [2.0; BAR_COUNT],
            progress: 0.0,
        }
    }
}

impl Animation {
    pub fn reset_waveform(&mut self) {
        self.levels.fill(0.0);
        self.heights.fill(2.0);
    }
    pub fn waveform(
        &mut self,
        level: f64,
        elapsed: f64,
        count: usize,
        scale: f64,
        reduce_motion: bool,
    ) {
        let elapsed = elapsed.clamp(1.0 / 120.0, 0.05);
        let level = if level <= SILENCE_THRESHOLD {
            0.0
        } else {
            level.clamp(0.0, 1.0)
        };
        if !reduce_motion {
            self.phase += 8.64 * elapsed;
        }
        let center = (count - 1) as f64 / 2.0;
        for i in 0..BAR_COUNT {
            if i >= count {
                self.levels[i] = 0.0;
                continue;
            }
            let distance = (i as f64 - center).abs() / center;
            let movement = 0.86
                + if reduce_motion {
                    0.0
                } else {
                    0.14 * (self.phase + i as f64 * 0.83).sin()
                };
            let target = (level * (-3.5 * distance * distance).exp() * movement).min(1.0);
            let tau = if target > self.levels[i] { 0.045 } else { 0.18 };
            self.levels[i] += (target - self.levels[i]) * (1.0 - (-elapsed / tau).exp());
            // Ties-to-even is Python round's backing-pixel behavior.
            self.heights[i] = ((2.0 + self.levels[i] * 18.0) * scale).round_ties_even() / scale;
        }
    }
    pub fn advance_progress(&mut self, elapsed: f64) -> bool {
        let elapsed = elapsed.clamp(1.0 / 120.0, 0.05);
        self.progress += (0.9 - self.progress) * (1.0 - (-elapsed / 0.464).exp());
        0.9 - self.progress > 0.001
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn all_recording_modes_keep_controls_labels_and_bars_inside_surface() {
        let heights = [2.0; BAR_COUNT];
        for mode in [
            "pushToTalk",
            "handsFree",
            "prompt",
            "promptPushToTalk",
            "command",
            "meeting",
        ] {
            for label in [24.0, 42.0] {
                for expanded in [false, true] {
                    let l = layout(LayoutInput {
                        width: if expanded { 360.0 } else { 240.0 },
                        height: if expanded { 200.0 } else { 80.0 },
                        mode,
                        state: State::Recording,
                        expanded,
                        label_width: label,
                        thinking_width: 90.0,
                        progress: 0.0,
                        bar_heights: &heights,
                    });
                    assert!(l.surface.x >= 0.0);
                    for b in &l.bars {
                        assert!(b.x >= 0.0 && b.max_x() <= l.surface.width);
                        if l.finish_visible {
                            assert!(b.max_x() <= l.finish.x - 4.0);
                        }
                    }
                    if l.cancel_visible && l.recording_label_visible {
                        assert!(l.recording_label.x >= l.cancel.max_x() + 4.0);
                    }
                }
            }
        }
    }
    #[test]
    fn elapsed_integration_is_frame_rate_independent_for_progress_and_reduced_motion() {
        let mut a = Animation::default();
        let mut b = a.clone();
        for _ in 0..60 {
            a.waveform(0.7, 1.0 / 60.0, 10, 2.0, true);
            a.advance_progress(1.0 / 60.0);
        }
        for _ in 0..120 {
            b.waveform(0.7, 1.0 / 120.0, 10, 2.0, true);
            b.advance_progress(1.0 / 120.0);
        }
        for i in 0..10 {
            assert!((a.levels[i] - b.levels[i]).abs() < 1e-12);
        }
        assert!((a.progress - b.progress).abs() < 1e-12);
        assert_eq!(a.phase, 0.0);
    }
    #[test]
    fn silence_decays_to_two_backing_pixels_and_progress_never_claims_complete() {
        let mut a = Animation::default();
        a.waveform(1.0, 0.05, 80, 2.0, false);
        for _ in 0..500 {
            a.waveform(0.005, 1.0 / 60.0, 80, 2.0, false);
            a.advance_progress(1.0 / 60.0);
        }
        assert_eq!(a.heights, [2.0; BAR_COUNT]);
        assert!(a.progress > 0.89 && a.progress <= 0.9);
        assert!(!a.advance_progress(1.0 / 60.0));
    }
    #[test]
    fn text_bound_preserves_unicode_and_latest_output() {
        let text = format!("{}LATEST END", "😀中".repeat(3000));
        let v = visible_text(&text);
        assert_eq!(v.chars().count(), 4001);
        assert!(v.starts_with('…'));
        assert!(v.ends_with("LATEST END"));
        assert_eq!(preferred_height(13.3, 14.0), 92.0);
        assert_eq!(preferred_height(9999.0, 14.0), 200.0);
    }
}
