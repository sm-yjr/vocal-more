// SPDX-License-Identifier: GPL-3.0-only
//! Reusable Apple System Settings × Raycast primitives built on GPUI Kit 0.9.1
//! and Vocal More's Geist theme tokens.
use gpui_kit::assets::IconName;
use gpui_kit::component::{
    ActiveTheme, Icon, Sizable,
    button::{Button, ButtonVariants},
    h_flex, v_flex,
};
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
        .rounded(cx.theme().radius_lg)
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
        ("review" | "pending", false) => ("待确认", Tone::Primary),
        ("review" | "pending", true) => ("Needs review", Tone::Primary),
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
