// SPDX-License-Identifier: GPL-3.0-only
//! Reusable Apple System Settings × Raycast primitives built on GPUI Kit 0.9.1
//! and Vocal More's Geist theme tokens.
use gpui_kit::assets::IconName;
use gpui_kit::component::{
    ActiveTheme, Icon, Sizable,
    button::{Button, ButtonVariants},
    h_flex, v_flex,
};
use gpui_kit::prelude::FluentBuilder;
use gpui_kit::*;

use serde_json::Value;

/// Elevated inset grouped card sitting on the muted canvas.
pub fn card(cx: &App) -> Div {
    v_flex()
        .gap_3()
        .p_4()
        .w_full()
        .min_w_0()
        .bg(cx.theme().group_box)
        .border_1()
        .border_color(cx.theme().border)
        .rounded(px(13.))
}

/// Heading above a grouped settings card.
pub fn section_heading(title: impl Into<SharedString>, cx: &App) -> Div {
    div()
        .ml_1p5()
        .text_size(px(13.))
        .font_weight(FontWeight::SEMIBOLD)
        .text_color(cx.theme().foreground)
        .child(title.into())
}

pub fn row_divider(cx: &App) -> Div {
    div()
        .w_full()
        .h(px(1.))
        .flex_shrink_0()
        .bg(cx.theme().border)
}

/// A tabular summary inset into a settings card.
pub fn summary_strip<const N: usize>(items: [(&str, String); N], large: bool, cx: &App) -> Div {
    h_flex()
        .w_full()
        .items_stretch()
        .p_3()
        .rounded(px(9.))
        .bg(cx.theme().popover)
        .children(
            items
                .into_iter()
                .enumerate()
                .map(|(index, (label, value))| {
                    v_flex()
                        .flex_1()
                        .min_w_0()
                        .px_2()
                        .gap_1()
                        .when(index > 0, |cell| {
                            cell.border_l_1().border_color(cx.theme().border)
                        })
                        .child(
                            div()
                                .text_size(px(11.5))
                                .text_color(cx.theme().muted_foreground)
                                .child(SharedString::from(label.to_owned())),
                        )
                        .child(
                            div()
                                .text_size(px(if large { 22. } else { 13. }))
                                .font_weight(FontWeight::SEMIBOLD)
                                .child(value),
                        )
                }),
        )
}

/// Elevated card with a header title and optional muted subtitle.
pub fn titled_card(
    title: impl Into<SharedString>,
    subtitle: Option<impl Into<SharedString>>,
    cx: &App,
) -> Div {
    let mut header = v_flex().gap_0p5().child(
        div()
            .text_sm()
            .font_weight(FontWeight::SEMIBOLD)
            .child(title.into()),
    );
    if let Some(subtitle) = subtitle {
        header = header.child(
            div()
                .text_xs()
                .whitespace_normal()
                .text_color(cx.theme().muted_foreground)
                .child(subtitle.into()),
        );
    }
    card(cx).child(header)
}

/// Subtle badge pill for semantic status, model capabilities, or counts.
#[derive(Clone, Copy)]
pub enum Tone {
    Neutral,
    Primary,
    Success,
    Warning,
    Danger,
}

pub fn badge(label: impl Into<SharedString>, tone: Tone, cx: &App) -> Div {
    let (bg, fg, border) = match tone {
        Tone::Neutral => (
            cx.theme().secondary,
            cx.theme().muted_foreground,
            cx.theme().border,
        ),
        Tone::Primary => (
            cx.theme().primary.opacity(0.12),
            cx.theme().primary,
            cx.theme().primary.opacity(0.28),
        ),
        Tone::Success => (
            cx.theme().green_light,
            cx.theme().green,
            cx.theme().green.opacity(0.32),
        ),
        Tone::Warning => (
            cx.theme().yellow_light,
            cx.theme().yellow,
            cx.theme().yellow.opacity(0.35),
        ),
        Tone::Danger => (
            cx.theme().red_light,
            cx.theme().red,
            cx.theme().red.opacity(0.35),
        ),
    };
    div()
        .px_2()
        .py_0p5()
        .rounded_full()
        .border_1()
        .border_color(border)
        .bg(bg)
        .text_xs()
        .font_weight(FontWeight::MEDIUM)
        .text_color(fg)
        .child(label.into())
}

/// Compact macOS keyboard keycap visual (`Fn`, `Esc`, `Right ⌘`, `F13`).
pub fn keycap(label: impl Into<SharedString>, cx: &App) -> Div {
    div()
        .px_2()
        .py_0p5()
        .rounded_md()
        .bg(cx.theme().secondary)
        .border_1()
        .border_color(cx.theme().border)
        .text_xs()
        .font_family(cx.theme().mono_font_family.clone())
        .font_weight(FontWeight::MEDIUM)
        .text_color(cx.theme().foreground)
        .child(label.into())
}

/// Small semantic status indicator circle.
pub fn status_dot(tone: Tone, cx: &App) -> Div {
    let color = match tone {
        Tone::Neutral => cx.theme().muted_foreground,
        Tone::Primary => cx.theme().primary,
        Tone::Success => cx.theme().success,
        Tone::Warning => cx.theme().warning,
        Tone::Danger => cx.theme().danger,
    };
    div().w(px(8.)).h(px(8.)).rounded_full().bg(color)
}

/// Semantic inline notice banner (info / warning / danger / success).
pub fn banner(text: impl Into<SharedString>, tone: Tone, cx: &App) -> Div {
    let (bg, border, icon_color, icon_name) = match tone {
        Tone::Success => (
            cx.theme().green_light,
            cx.theme().green.opacity(0.32),
            cx.theme().green,
            IconName::CircleCheck,
        ),
        Tone::Warning => (
            cx.theme().yellow_light,
            cx.theme().yellow.opacity(0.36),
            cx.theme().yellow,
            IconName::TriangleAlert,
        ),
        Tone::Danger => (
            cx.theme().red_light,
            cx.theme().red.opacity(0.36),
            cx.theme().red,
            IconName::CircleAlert,
        ),
        _ => (
            cx.theme().primary.opacity(0.08),
            cx.theme().primary.opacity(0.26),
            cx.theme().primary,
            IconName::CircleAlert,
        ),
    };
    h_flex()
        .w_full()
        .min_w_0()
        .items_start()
        .gap_2()
        .px_3()
        .py_2p5()
        .rounded(cx.theme().radius)
        .bg(bg)
        .border_1()
        .border_color(border)
        .child(
            div()
                .pt_0p5()
                .text_color(icon_color)
                .child(Icon::new(icon_name).small()),
        )
        .child(
            div()
                .flex_1()
                .min_w_0()
                .text_xs()
                .whitespace_normal()
                .text_color(cx.theme().foreground)
                .child(text.into()),
        )
}

/// Key/value metadata row with monospace value.
pub fn readout(label: impl Into<SharedString>, value: impl Into<SharedString>, cx: &App) -> Div {
    h_flex()
        .w_full()
        .min_w_0()
        .justify_between()
        .items_center()
        .gap_4()
        .py_1()
        .child(
            div()
                .text_xs()
                .text_color(cx.theme().muted_foreground)
                .child(label.into()),
        )
        .child(
            div()
                .text_xs()
                .font_family(cx.theme().mono_font_family.clone())
                .font_weight(FontWeight::MEDIUM)
                .text_color(cx.theme().foreground)
                .child(value.into()),
        )
}

/// Brand mini-capsule icon matching the Dynamic Island dark pill identity.
pub fn brand_mark() -> Div {
    h_flex()
        .w(px(24.))
        .h(px(24.))
        .rounded_md()
        .bg(rgb(0x09090b))
        .border_1()
        .border_color(rgba(0xffffff38))
        .items_center()
        .justify_center()
        .gap(px(2.))
        .child(
            div()
                .w(px(2.))
                .h(px(6.))
                .rounded_full()
                .bg(rgba(0xffffffb8)),
        )
        .child(div().w(px(2.)).h(px(12.)).rounded_full().bg(rgb(0xffffff)))
        .child(
            div()
                .w(px(2.))
                .h(px(8.))
                .rounded_full()
                .bg(rgba(0xffffffd8)),
        )
}

/// Collapsible disclosure card header button (controls visibility of advanced panels).
pub fn disclosure_header(
    id: impl Into<ElementId>,
    title: impl Into<SharedString>,
    tag: impl Into<SharedString>,
    summary: impl Into<SharedString>,
    expanded: bool,
    on_toggle: impl Fn(&ClickEvent, &mut Window, &mut App) + 'static,
    cx: &App,
) -> Div {
    let title = title.into();
    let chevron = if expanded {
        IconName::ChevronDown
    } else {
        IconName::ChevronRight
    };
    v_flex()
        .w_full()
        .min_w_0()
        .gap_1()
        .child(
            h_flex()
                .w_full()
                .min_w_0()
                .items_center()
                .justify_between()
                .gap_2()
                .child(
                    div().flex_1().min_w_0().child(
                        Button::new(id)
                            .small()
                            .ghost()
                            .w_full()
                            .accessibility_label(title.clone())
                            .on_click(on_toggle)
                            .child(
                                h_flex()
                                    .w_full()
                                    .min_w_0()
                                    .items_center()
                                    .justify_start()
                                    .gap_2()
                                    .child(Icon::new(chevron).small())
                                    .child(
                                        div()
                                            .flex_1()
                                            .min_w_0()
                                            .text_sm()
                                            .font_weight(FontWeight::SEMIBOLD)
                                            .child(title),
                                    ),
                            ),
                    ),
                )
                .child(div().flex_shrink_0().child(badge(tag, Tone::Neutral, cx))),
        )
        .child(
            div()
                .px_2()
                .text_xs()
                .whitespace_normal()
                .text_color(cx.theme().muted_foreground)
                .child(summary.into()),
        )
}

pub fn number(value: &Value) -> f64 {
    value.as_f64().unwrap_or_default()
}

pub fn bytes(value: &Value) -> String {
    let bytes = number(value);
    if bytes < 1024. {
        format!("{bytes:.0} B")
    } else if bytes < 1048576. {
        format!("{:.1} KB", bytes / 1024.)
    } else {
        format!("{:.1} MB", bytes / 1048576.)
    }
}

pub fn money(amount: f64) -> String {
    format!("¥{amount:.4}")
}

/// Escape markdown punctuation so recording content remains literal selectable text.
pub fn literal_markdown(text: &str) -> String {
    text.chars()
        .flat_map(|c| {
            if "\\`*_{}[]<>()#+-.!|".contains(c) {
                vec!['\\', c]
            } else {
                vec![c]
            }
        })
        .collect()
}

/// Format an ISO-8601 timestamp (`2026-10-06T16:31:06...`) into readable `2026-10-06 16:31`.
pub fn readable_timestamp(raw: &str) -> String {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return "—".into();
    }
    if let Some((date, time)) = trimmed.split_once('T') {
        let short_time = time.get(..5).unwrap_or(time);
        format!("{date} {short_time}")
    } else {
        trimmed.to_owned()
    }
}

pub fn format_db_from_linear(gain: f64) -> String {
    let db = (20.0 * gain.max(0.001).log10()).round();
    if db >= 0.0 {
        format!("+{db:.0} dB")
    } else {
        format!("{db:.0} dB")
    }
}

pub fn localized_mode(mode: &str, english: bool) -> &'static str {
    match (mode, english) {
        ("walkie_talkie" | "pushToTalk", false) => "按住说话",
        ("realtime_long" | "handsFree", false) => "免提",
        ("meeting", false) => "会议",
        ("prompt" | "promptPushToTalk", false) => "提示词",
        ("walkie_talkie" | "pushToTalk", true) => "Push to talk",
        ("realtime_long" | "handsFree", true) => "Hands-free",
        ("meeting", true) => "Meeting",
        ("prompt" | "promptPushToTalk", true) => "Prompt",
        (_, false) => "听写",
        (_, true) => "Dictation",
    }
}

pub fn localized_status(status: &str, english: bool) -> (&'static str, Tone) {
    match (status, english) {
        ("success" | "completed", false) => ("已完成", Tone::Success),
        ("success" | "completed", true) => ("Completed", Tone::Success),
        ("failed" | "error", false) => ("识别失败", Tone::Danger),
        ("failed" | "error", true) => ("Failed", Tone::Danger),
        ("retrying", false) => ("重新识别中…", Tone::Warning),
        ("retrying", true) => ("Retrying…", Tone::Warning),
        (_, false) => ("处理中", Tone::Neutral),
        (_, true) => ("Pending", Tone::Neutral),
    }
}

pub fn localized_learning_status(status: &str, english: bool) -> (&'static str, Tone) {
    match (status, english) {
        ("review", false) => ("待审核", Tone::Primary),
        ("review", true) => ("Needs review", Tone::Primary),
        ("pending" | "processing" | "retry" | "applying", false) => ("分析中", Tone::Neutral),
        ("pending" | "processing" | "retry" | "applying", true) => ("Analyzing", Tone::Neutral),
        ("applied" | "approved", false) => ("已加入词典", Tone::Success),
        ("applied" | "approved", true) => ("Added to dictionary", Tone::Success),
        ("ignored" | "rejected" | "dismissed", false) => ("已忽略", Tone::Neutral),
        ("ignored" | "rejected" | "dismissed", true) => ("Ignored", Tone::Neutral),
        ("reverted" | "reverting" | "undone", false) => ("已撤销", Tone::Warning),
        ("reverted" | "reverting" | "undone", true) => ("Undone", Tone::Warning),
        ("failed", false) => ("处理失败", Tone::Danger),
        ("failed", true) => ("Failed", Tone::Danger),
        (_, false) => ("处理中", Tone::Neutral),
        (_, true) => ("Processing", Tone::Neutral),
    }
}

pub fn localized_learning_reason(code: &str, english: bool) -> Option<&'static str> {
    match (code, english) {
        ("awaiting_independent_correction", false) => {
            Some("首次发现此修正；可手动立即加入，或在下次同类修正后自动加入")
        }
        ("awaiting_independent_correction", true) => {
            Some("First occurrence; approve now or wait for a second matching edit")
        }
        ("conflicting_corrections", false) => Some("该别名曾对应其他词条或曾被忽略，需人工确认"),
        ("conflicting_corrections", true) => {
            Some("Conflicts with an earlier mapping or rejected rule; review needed")
        }
        ("same_term_correction", false) => Some("同音或大小写修正，已自动生效"),
        ("same_term_correction", true) => Some("Phonetic or casing match; applied automatically"),
        ("repeated_correction", false) => Some("多次独立修正一致，已自动生效"),
        ("repeated_correction", true) => {
            Some("Confirmed across separate edits; applied automatically")
        }
        ("proper_noun_correction", false) => Some("识别为专有名词修正"),
        ("proper_noun_correction", true) => Some("Classified as a proper-noun correction"),
        ("dictionary_conflict", false) => Some("与现有词典规则冲突，已跳过"),
        ("dictionary_conflict", true) => Some("Conflicts with an existing dictionary rule"),
        ("not_a_reusable_term" | "non_lexical_mapping" | "non_lexical_phrase", false) => {
            Some("判定为普通语句改写，非可复用词条")
        }
        ("not_a_reusable_term" | "non_lexical_mapping" | "non_lexical_phrase", true) => {
            Some("Treated as an ordinary rewrite rather than a reusable term")
        }
        ("numeric_or_date_change" | "numeric_identifier_change", false) => {
            Some("涉及数字、日期或编号修改，已跳过")
        }
        ("numeric_or_date_change" | "numeric_identifier_change", true) => {
            Some("Skipped numeric, date, or identifier change")
        }
        ("sentence_sized_term" | "term_too_long", false) => Some("片段过长，不适合作为词条"),
        ("sentence_sized_term" | "term_too_long", true) => Some("Too long to store as a term"),
        ("alias_too_broad" | "mapping_does_not_explain_edit", false) => {
            Some("替换范围过宽或无法解释本次修改，已跳过")
        }
        ("alias_too_broad" | "mapping_does_not_explain_edit", true) => {
            Some("Mapping is too broad or does not explain the edit")
        }
        ("learning_disabled_or_excluded", false) => Some("自动学习已关闭或该应用处于隐私排除列表"),
        ("learning_disabled_or_excluded", true) => {
            Some("Auto-learning disabled or app is in excluded list")
        }
        ("", _) => None,
        (_, false) => Some("已按本地规则过滤"),
        (_, true) => Some("Filtered by local validation rules"),
    }
}

/// Format a Unix epoch timestamp (seconds) into a concise human-readable relative label.
pub fn readable_unix_timestamp(seconds: f64, english: bool) -> String {
    if !seconds.is_finite() || seconds <= 0.0 {
        return "—".into();
    }
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_secs_f64())
        .unwrap_or(seconds);
    let diff = (now - seconds).max(0.0);
    if diff < 60.0 {
        return if english { "Just now" } else { "刚刚" }.into();
    }
    if diff < 3600.0 {
        let mins = (diff / 60.0).floor() as u64;
        return if english {
            format!("{mins}m ago")
        } else {
            format!("{mins} 分钟前")
        };
    }
    if diff < 86400.0 {
        let hours = (diff / 3600.0).floor() as u64;
        return if english {
            format!("{hours}h ago")
        } else {
            format!("{hours} 小时前")
        };
    }
    let days = (diff / 86400.0).floor() as u64;
    if days < 30 {
        return if english {
            format!("{days}d ago")
        } else {
            format!("{days} 天前")
        };
    }
    // Fall back to UTC YYYY-MM-DD using civil-from-days formula.
    let z = (seconds / 86400.0).floor() as i64 + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = y + i64::from(m <= 2);
    format!("{y:04}-{m:02}-{d:02}")
}
