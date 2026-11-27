// SPDX-License-Identifier: GPL-3.0-only
//! Redesigned Apple System Settings × Raycast GPUI views for Vocal More 0.6.2,
//! built on GPUI Kit 0.9.1 Geist theme tokens and integrated with custom
//! dictation scenes.
use super::{
    Control, MicState, Settings, calibration,
    components::{
        Tone, badge, banner, brand_mark, bytes, card, disclosure_header, format_db_from_linear,
        keycap, literal_markdown, localized_mode, localized_status, money, number,
        readable_timestamp, readout, status_dot, titled_card,
    },
    schema::{self, Kind, Tab, get},
    shortcut,
};
use gpui_kit::assets::IconName;
use gpui_kit::component::{
    ActiveTheme, Disableable, Icon, Selectable, Sizable,
    button::{Button, ButtonVariants},
    h_flex,
    input::{Input, Textarea},
    progress::Progress,
    select::Select,
    slider::Slider,
    switch::Switch,
    text::TextView,
    v_flex,
};
use gpui_kit::prelude::FluentBuilder;
use gpui_kit::*;
use serde_json::{Value, json};

fn tab_icon(tab: Tab) -> IconName {
    match tab {
        Tab::Home => IconName::House,
        Tab::History => IconName::Clock,
        Tab::Dictionary => IconName::BookOpen,
        Tab::Audio => IconName::Mic,
        Tab::Ai => IconName::Sparkles,
        Tab::Shortcuts => IconName::Keyboard,
        Tab::General => IconName::SlidersHorizontal,
    }
}

/// Shared card surface used by sibling submodules such as `scenes.rs`.
pub(super) fn panel(cx: &App) -> Div {
    card(cx)
}

/// Shared titled section wrapper used by sibling submodules such as `scenes.rs`.
pub(super) fn titled(title: impl Into<SharedString>, content: Div, cx: &App) -> Div {
    v_flex()
        .w_full()
        .min_w_0()
        .gap_2()
        .child(
            div()
                .text_xs()
                .font_weight(FontWeight::SEMIBOLD)
                .text_color(cx.theme().muted_foreground)
                .child(title.into()),
        )
        .child(content)
}

/// Wrapping horizontal action row used by sibling submodules such as `scenes.rs`.
pub(super) fn actions() -> Div {
    h_flex().gap_2().flex_wrap().items_center()
}

/// Muted helper note used by sibling submodules such as `scenes.rs`.
pub(super) fn note(text: impl Into<SharedString>, cx: &App) -> Div {
    div()
        .text_xs()
        .whitespace_normal()
        .text_color(cx.theme().muted_foreground)
        .child(text.into())
}

impl Settings {
    fn field_row(
        &self,
        key: &'static str,
        bottom_border: bool,
        cx: &mut Context<Self>,
    ) -> AnyElement {
        let field = *schema::FIELDS
            .iter()
            .find(|field| field.key == key)
            .expect("settings field exists");
        let title = field.title(self.english());
        let disabled = self.disabled(key);
        let control_width = if self.narrow { 260. } else { 290. };
        let element = match &self.controls[key] {
            Control::Toggle => Switch::new(key)
                .accessibility_label(title)
                .checked(get(self.config(), key) == true)
                .disabled(disabled)
                .on_change(cx.listener(move |this, checked, window, cx| {
                    this.set_config(key, json!(checked), window, cx)
                }))
                .into_any_element(),
            Control::Input(input) => {
                let input_w = if matches!(field.kind, Kind::Secret) {
                    if self.narrow { 196. } else { 230. }
                } else {
                    control_width
                };
                let mut element = Input::new(input)
                    .id(key)
                    .aria_label(title)
                    .accessibility_id(key)
                    .w(px(input_w))
                    .disabled(disabled);
                if matches!(field.kind, Kind::Secret) {
                    element = element
                        .content_type(gpui_kit::component::input::InputContentType::Password);
                    h_flex()
                        .gap_2()
                        .flex_wrap()
                        .items_center()
                        .child(element)
                        .child(
                            Button::new("show-api-key")
                                .small()
                                .label(self.text(
                                    if self.show_key { "隐藏" } else { "显示" },
                                    if self.show_key { "Hide" } else { "Show" },
                                ))
                                .on_click(cx.listener(|this, _, window, cx| {
                                    this.show_key = !this.show_key;
                                    if let Some(Control::Input(input)) =
                                        this.controls.get("api_key")
                                    {
                                        input.update(cx, |input, cx| {
                                            input.set_masked(!this.show_key, window, cx)
                                        });
                                    }
                                    if this.show_key
                                        && get(this.config(), "_api_key_set") == true
                                        && get(this.config(), "api_key")
                                            .as_str()
                                            .unwrap_or_default()
                                            .is_empty()
                                    {
                                        this.action("revealApiKey", json!({}), cx);
                                    }
                                    if !this.show_key {
                                        this.snapshot["config"]["api_key"] = json!("");
                                        if let Some(Control::Input(input)) =
                                            this.controls.get("api_key")
                                        {
                                            input.update(cx, |input, cx| {
                                                input.set_value("", window, cx)
                                            });
                                        }
                                    }
                                    cx.notify();
                                })),
                        )
                        .child(
                            Button::new("clear-api-key")
                                .small()
                                .label(self.text("清除", "Clear"))
                                .disabled(get(self.config(), "_api_key_set") != true)
                                .on_click(cx.listener(|this, _, window, cx| {
                                    this.set_config("api_key", json!(""), window, cx);
                                    if let Some(Control::Input(input)) =
                                        this.controls.get("api_key")
                                    {
                                        input.update(cx, |input, cx| {
                                            input.set_value("", window, cx)
                                        });
                                    }
                                })),
                        )
                        .into_any_element()
                } else {
                    element.into_any_element()
                }
            }
            Control::Select(select) => Select::new(select)
                .id(key)
                .accessibility_label(title)
                .w(px(control_width))
                .disabled(disabled)
                .into_any_element(),
            Control::Slider(slider) => {
                let Kind::Slider { unit, .. } = field.kind else {
                    unreachable!()
                };
                let value = slider.read(cx).value().start();
                let track_w = if self.narrow { 176. } else { 205. };
                h_flex()
                    .gap_3()
                    .items_center()
                    .w(px(control_width))
                    .child(Slider::new(slider).w(px(track_w)).disabled(disabled))
                    .child(
                        div()
                            .text_xs()
                            .font_family(cx.theme().mono_font_family.clone())
                            .font_weight(FontWeight::MEDIUM)
                            .min_w(px(64.))
                            .child(if value.fract().abs() < 0.001 {
                                format!("{value:.0} {unit}")
                            } else {
                                format!("{value:.2} {unit}")
                            }),
                    )
                    .into_any_element()
            }
        };
        let hint = self.field_hint(key, field);
        let stack = self.narrow || matches!(field.kind, Kind::Secret);
        h_flex()
            .id(SharedString::from(format!("setting-row-{key}")))
            .test_support()
            .w_full()
            .min_w_0()
            .items_center()
            .justify_between()
            .gap_5()
            .when(stack, |row| row.flex_col().items_start().gap_2())
            .py_2p5()
            .when(bottom_border, |row| {
                row.border_b_1()
                    .border_color(cx.theme().border.opacity(0.7))
            })
            .child(
                v_flex()
                    .flex_1()
                    .min_w_0()
                    .w_full()
                    .gap_0p5()
                    .child(div().text_sm().font_weight(FontWeight::MEDIUM).child(title))
                    .when(!hint.is_empty(), |col| {
                        col.child(
                            div()
                                .text_xs()
                                .whitespace_normal()
                                .text_color(if disabled {
                                    cx.theme().yellow
                                } else {
                                    cx.theme().muted_foreground
                                })
                                .child(hint),
                        )
                    }),
            )
            .child(div().flex_shrink_0().child(element))
            .into_any_element()
    }

    fn field_hint(&self, key: &'static str, field: schema::Field) -> &'static str {
        if key == "api_key" && get(self.config(), "_api_key_set") == true {
            return self.text(
                "密钥已安全保存。点击“显示”读取，或点击“清除”移除。",
                "API key saved locally. Show reveals it; Clear removes it.",
            );
        }
        if key == "enable_polish" && self.native_asr() {
            return self.text(
                "当前识别模型为原生直出 ASR，已自动跳过二阶段润色。",
                "Selected native ASR transcribes directly and skips second-stage polish.",
            );
        }
        if key == "enable_polish" && self.hybrid_asr() {
            return if self.hybrid_needs_second_stage() {
                self.text(
                    "当前风格已自动级联二阶段润色模型；切回“原话轻修”可恢复单级极速直出。",
                    "Current style cascades to the polish LLM; switch to Light Clean for single-pass speed.",
                )
            } else {
                self.text(
                    "原话轻修模式由 3.1 输入法模型原生顺滑直出；切换结构化、风格或翻译时自动级联润色模型。",
                    "Light Clean runs natively inside 3.1 ASR; structured, styled, or translated modes cascade automatically.",
                )
            };
        }
        if (key == "audio.gain" || key == "audio.soft_limiter")
            && get(self.config(), "audio.gain_mode") == "automatic"
            && self.snapshot["audio_input_status"]["gain_control"] == "apple_agc"
        {
            return self.text(
                "当前由 Apple 自动增益接管，切换为“手动软件增益”后可调整。",
                "Managed by Apple AGC. Switch Gain control to Manual to adjust.",
            );
        }
        if key == "audio.highpass_freq" && get(self.config(), "audio.highpass_filter") == false {
            return self.text(
                "开启“高通滤波”后可调整截止频率。",
                "Enable High-pass filter above to adjust cutoff frequency.",
            );
        }
        if (key == "native_fast_paste" || key == "restore_clipboard")
            && get(self.config(), "auto_paste") == false
        {
            return self.text(
                "需先开启“自动输入识别结果”才会生效。",
                "Requires Automatically paste results to be enabled.",
            );
        }
        if key.starts_with("audio.") && key != "audio.waveform_ceiling_dbfs" && self.audio_busy() {
            return self.text(
                "麦克风测试或录音进行中，参数暂时锁定。",
                "Locked while microphone test or recording is active.",
            );
        }
        field.hint(self.english())
    }

    fn field_group(&self, keys: &[&'static str], cx: &mut Context<Self>) -> Div {
        let len = keys.len();
        v_flex().w_full().min_w_0().children(
            keys.iter()
                .enumerate()
                .map(|(index, key)| self.field_row(key, index + 1 < len, cx)),
        )
    }

    fn disclosure(
        &self,
        section: &'static str,
        title: &'static str,
        summary: String,
        body: impl FnOnce(&Self, &mut Context<Self>) -> Div,
        cx: &mut Context<Self>,
    ) -> Div {
        let expanded = self.section_expanded(section);
        let mut container = card(cx).gap_2().child(disclosure_header(
            SharedString::from(format!("disclosure-{section}")),
            title,
            self.text("高级", "Advanced"),
            summary,
            expanded,
            cx.listener(move |this, _, _, cx| this.toggle_section(section, cx)),
            cx,
        ));
        if expanded {
            container = container.child(
                v_flex()
                    .w_full()
                    .min_w_0()
                    .gap_3()
                    .pt_2()
                    .border_t_1()
                    .border_color(cx.theme().border)
                    .child(body(self, cx)),
            );
        }
        container
    }

    // -------------------------------------------------------------------------
    // 1. Home / Overview (概览)
    // -------------------------------------------------------------------------
    fn home_page(&self, cx: &mut Context<Self>) -> Div {
        let attention = self.attention_count();
        let preset_name = match self.active_audio_preset() {
            Some("whisper") => self.text("办公室低声", "Office Whisper"),
            Some("noisy") => self.text("嘈杂环境", "Noisy Room"),
            Some("normal") => self.text("正常说话", "Normal Voice"),
            _ => self.text("自定义音频", "Custom Audio"),
        };
        let active_scene = self.active_dictation_scene_name();

        let hero = if attention > 0 {
            let mut issues = v_flex().gap_2();
            if get(self.config(), "_api_key_set") != true {
                issues = issues.child(
                    h_flex()
                        .justify_between()
                        .items_center()
                        .gap_2()
                        .flex_wrap()
                        .child(div().text_xs().child(self.text(
                            "尚未配置 DashScope API Key",
                            "DashScope API key is not configured",
                        )))
                        .child(
                            Button::new("home-fix-api-key")
                                .small()
                                .primary()
                                .label(self.text("去配置", "Configure"))
                                .on_click(cx.listener(|this, _, window, cx| {
                                    this.switch_tab(Tab::General, window, cx)
                                })),
                        ),
                );
            }
            for check in self
                .array("environment_checks")
                .iter()
                .filter(|c| c["status"] != "ok")
            {
                let key = check["key"].as_str().unwrap_or_default();
                if key == "api_key" && get(self.config(), "_api_key_set") != true {
                    continue;
                }
                let label = match key {
                    "microphone_permission" => {
                        self.text("麦克风权限未授权", "Microphone permission required")
                    }
                    "accessibility" => {
                        self.text("辅助功能权限未授权", "Accessibility permission required")
                    }
                    "input_device" => {
                        self.text("未检测到可用麦克风", "No input microphone detected")
                    }
                    "hotkey_listener" => {
                        self.text("全局快捷键监听未就绪", "Global hotkey listener not active")
                    }
                    _ => self.text("运行环境需要检查", "Environment needs attention"),
                };
                let mut row = h_flex()
                    .justify_between()
                    .items_center()
                    .gap_2()
                    .flex_wrap()
                    .child(div().text_xs().child(label));
                if key == "microphone_permission" {
                    row = row.child(
                        Button::new("home-fix-mic")
                            .small()
                            .label(self.text("打开麦克风设置", "Microphone Settings"))
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.action("openMicrophoneSettings", json!({}), cx);
                            })),
                    );
                } else if key == "accessibility" {
                    row = row.child(
                        Button::new(SharedString::from(format!("home-fix-ax-{key}")))
                            .small()
                            .label(self.text("打开辅助功能设置", "Accessibility Settings"))
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.action("openAccessibilitySettings", json!({}), cx);
                            })),
                    );
                }
                issues = issues.child(row);
            }
            if get(self.config(), "ui.onboarding_skipped") == true {
                issues = issues.child(
                    h_flex()
                        .justify_between()
                        .items_center()
                        .gap_2()
                        .flex_wrap()
                        .child(
                            div()
                                .text_xs()
                                .text_color(cx.theme().muted_foreground)
                                .child(self.text(
                                    "首次引导已跳过，可随时继续完成。",
                                    "Initial setup was skipped; resume anytime.",
                                )),
                        )
                        .child(
                            Button::new("home-resume-onboarding")
                                .small()
                                .label(self.text("继续引导", "Resume Setup"))
                                .on_click(cx.listener(|this, _, window, cx| {
                                    this.onboarding_step = 0;
                                    this.set_config(
                                        "ui.onboarding_completed",
                                        json!(false),
                                        window,
                                        cx,
                                    );
                                    this.set_config(
                                        "ui.onboarding_skipped",
                                        json!(false),
                                        window,
                                        cx,
                                    );
                                })),
                        ),
                );
            }
            card(cx)
                .border_color(cx.theme().yellow.opacity(0.45))
                .bg(cx.theme().yellow_light)
                .child(
                    h_flex()
                        .justify_between()
                        .items_center()
                        .gap_2()
                        .flex_wrap()
                        .child(
                            h_flex()
                                .items_center()
                                .gap_2()
                                .child(status_dot(Tone::Warning, cx))
                                .child(div().text_sm().font_weight(FontWeight::SEMIBOLD).child(
                                    self.text("还有几项准备需要处理", "A few items need attention"),
                                )),
                        )
                        .child(
                            Button::new("home-refresh-env")
                                .small()
                                .icon(IconName::RefreshCw)
                                .label(self.text("重新检查", "Check again"))
                                .on_click(cx.listener(|this, _, _, cx| {
                                    this.action("refreshEnvironment", json!({}), cx);
                                    this.action("refreshDevices", json!({}), cx);
                                })),
                        ),
                )
                .child(issues)
        } else {
            card(cx).child(
                h_flex()
                    .w_full()
                    .justify_between()
                    .items_center()
                    .gap_4()
                    .when(self.narrow, |row| row.flex_col().items_start())
                    .child(
                        v_flex()
                            .gap_2()
                            .flex_1()
                            .min_w_0()
                            .child(
                                h_flex()
                                    .items_center()
                                    .gap_2()
                                    .flex_wrap()
                                    .child(status_dot(Tone::Success, cx))
                                    .child(div().text_sm().font_weight(FontWeight::SEMIBOLD).child(
                                        self.text(
                                            "已就绪，随时轻声听写",
                                            "Ready for low-voice dictation",
                                        ),
                                    ))
                                    .child(badge(preset_name, Tone::Primary, cx))
                                    .when_some(active_scene.clone(), |row, scene_name| {
                                        row.child(badge(
                                            format!("{}: {scene_name}", self.text("场景", "Scene")),
                                            Tone::Success,
                                            cx,
                                        ))
                                    }),
                            )
                            .child(
                                h_flex()
                                    .items_center()
                                    .gap_1p5()
                                    .flex_wrap()
                                    .child(
                                        div()
                                            .text_xs()
                                            .text_color(cx.theme().muted_foreground)
                                            .child(self.text("按住", "Hold")),
                                    )
                                    .child(keycap("Fn", cx))
                                    .child(
                                        div()
                                            .text_xs()
                                            .text_color(cx.theme().muted_foreground)
                                            .child(self.text(
                                                "说话 · 轻按免提 ·",
                                                "to speak · Tap hands-free ·",
                                            )),
                                    )
                                    .child(keycap("Esc", cx))
                                    .child(
                                        div()
                                            .text_xs()
                                            .text_color(cx.theme().muted_foreground)
                                            .child(self.text("取消", "cancels")),
                                    ),
                            ),
                    )
                    .child(
                        h_flex()
                            .gap_2()
                            .flex_shrink_0()
                            .child(
                                Button::new("home-calibrate")
                                    .small()
                                    .primary()
                                    .label(self.text("低声校准", "Whisper Calibration"))
                                    .disabled(self.audio_busy())
                                    .on_click(cx.listener(|this, _, _, cx| {
                                        this.calibration.open = true;
                                        cx.notify();
                                    })),
                            )
                            .child(
                                Button::new("home-open-audio")
                                    .small()
                                    .label(self.text("试音", "Test Mic"))
                                    .on_click(cx.listener(|this, _, window, cx| {
                                        this.switch_tab(Tab::Audio, window, cx)
                                    })),
                            ),
                    ),
            )
        };

        // Quick Controls 3-card grid
        let active_preset = self.active_audio_preset();
        let active_device = self.snapshot["audio_input_status"]["device_name"]
            .as_str()
            .filter(|s| !s.is_empty())
            .or_else(|| get(self.config(), "audio.input_device").as_str())
            .unwrap_or_else(|| self.text("系统默认麦克风", "System default mic"))
            .to_owned();

        let scene_card = card(cx)
            .flex_1()
            .gap_2p5()
            .child(
                h_flex()
                    .justify_between()
                    .items_center()
                    .child(
                        div()
                            .text_xs()
                            .font_weight(FontWeight::SEMIBOLD)
                            .text_color(cx.theme().muted_foreground)
                            .child(self.text("声音场景", "Voice Scene")),
                    )
                    .child(
                        Button::new("home-goto-audio")
                            .xsmall()
                            .ghost()
                            .label(self.text("详情", "Details"))
                            .on_click(cx.listener(|this, _, window, cx| {
                                this.switch_tab(Tab::Audio, window, cx)
                            })),
                    ),
            )
            .child(
                h_flex().gap_1().flex_wrap().children(
                    [
                        ("whisper", "低声", "Whisper"),
                        ("normal", "正常", "Normal"),
                        ("noisy", "嘈杂", "Noisy"),
                    ]
                    .into_iter()
                    .map(|(id, zh, en)| {
                        Button::new(SharedString::from(format!("home-preset-{id}")))
                            .xsmall()
                            .label(self.text(zh, en))
                            .selected(active_preset == Some(id))
                            .disabled(self.audio_busy())
                            .on_click(
                                cx.listener(move |this, _, window, cx| this.preset(id, window, cx)),
                            )
                    }),
                ),
            )
            .child(
                div()
                    .text_xs()
                    .text_color(cx.theme().muted_foreground)
                    .child(active_device),
            );

        let polish_on = get(self.config(), "enable_polish") == true && !self.native_asr();
        let polish_summary = if self.native_asr() {
            self.text("原生直出模型（已跳过）", "Native ASR (direct)")
        } else if polish_on {
            let base = match self.active_polish_style() {
                Some("structured") => self.text("清晰结构化", "Structured"),
                Some("technical") => self.text("技术文档", "Technical"),
                Some("chat") => self.text("轻松聊天", "Casual Chat"),
                Some("prompt") => self.text("AI 提示词", "AI Prompt"),
                Some("minimal") => self.text("原话轻修", "Light Clean"),
                _ => self.text("自定义润色风格", "Custom polish style"),
            };
            if self.hybrid_asr() {
                if self.hybrid_needs_second_stage() {
                    match self.active_polish_style() {
                        Some("structured") => {
                            self.text("清晰结构化 · 联动润色", "Structured · +LLM")
                        }
                        Some("technical") => self.text("技术文档 · 联动润色", "Technical · +LLM"),
                        Some("chat") => self.text("轻松聊天 · 联动润色", "Casual Chat · +LLM"),
                        Some("prompt") => self.text("AI 提示词 · 联动润色", "AI Prompt · +LLM"),
                        _ => self.text("自定义风格 · 联动润色", "Custom · +LLM"),
                    }
                } else {
                    self.text("原话轻修 · 原生极速", "Light Clean · Fast native")
                }
            } else {
                base
            }
        } else {
            self.text("已关闭（原话直出）", "Off (raw transcript)")
        };
        let polish_card = card(cx)
            .flex_1()
            .gap_2p5()
            .child(
                h_flex()
                    .justify_between()
                    .items_center()
                    .child(
                        div()
                            .text_xs()
                            .font_weight(FontWeight::SEMIBOLD)
                            .text_color(cx.theme().muted_foreground)
                            .child(self.text("智能润色", "Smart Polish")),
                    )
                    .child(
                        Switch::new("home-enable-polish")
                            .accessibility_label(self.text("智能润色", "Smart Polish"))
                            .checked(polish_on)
                            .disabled(self.native_asr())
                            .on_change(cx.listener(|this, checked, window, cx| {
                                this.set_config("enable_polish", json!(checked), window, cx)
                            })),
                    ),
            )
            .child(
                div()
                    .text_sm()
                    .font_weight(FontWeight::MEDIUM)
                    .child(polish_summary),
            )
            .child(
                Button::new("home-goto-ai")
                    .xsmall()
                    .ghost()
                    .justify_start()
                    .label(if let Some(scene) = &active_scene {
                        SharedString::from(format!("{}: {scene} →", self.text("听写场景", "Scene")))
                    } else {
                        SharedString::from(
                            self.text("模型、场景与风格 →", "Model, scenes & style →"),
                        )
                    })
                    .on_click(
                        cx.listener(|this, _, window, cx| this.switch_tab(Tab::Ai, window, cx)),
                    ),
            );

        let screen_on = get(self.config(), "screen_context_enabled") == true;
        let cursor_on = get(self.config(), "cursor_context_enabled") == true;
        let screen_card = card(cx)
            .flex_1()
            .gap_2p5()
            .child(
                h_flex()
                    .justify_between()
                    .items_center()
                    .child(
                        div()
                            .text_xs()
                            .font_weight(FontWeight::SEMIBOLD)
                            .text_color(cx.theme().muted_foreground)
                            .child(self.text("看屏幕说话", "Screen Context")),
                    )
                    .child(
                        Switch::new("home-screen-context")
                            .accessibility_label(self.text("看屏幕说话", "Screen Context"))
                            .checked(screen_on)
                            .on_change(cx.listener(|this, checked, window, cx| {
                                this.set_config(
                                    "screen_context_enabled",
                                    json!(checked),
                                    window,
                                    cx,
                                )
                            })),
                    ),
            )
            .child(
                div()
                    .text_sm()
                    .font_weight(FontWeight::MEDIUM)
                    .child(if screen_on {
                        self.text("屏幕画面 + 光标上下文", "Screen + cursor context")
                    } else if cursor_on {
                        self.text("光标上下文与动态热词", "Cursor context & hotwords")
                    } else {
                        self.text("仅使用语音上下文", "Voice context only")
                    }),
            )
            .child(
                div()
                    .text_xs()
                    .text_color(cx.theme().muted_foreground)
                    .child(if screen_on {
                        self.text(
                            "自动路由至 Qwen3.8 Omni，屏幕内容不会粘贴",
                            "Auto-routes to Qwen3.8 Omni; screen text never pasted",
                        )
                    } else if cursor_on {
                        self.text(
                            "读取当前输入框附近术语，自动跳过密码框",
                            "Reads nearby terms; skips password fields",
                        )
                    } else {
                        self.text("屏幕内容不会成为粘贴文本", "Screen text is never pasted")
                    }),
            );

        let quick_grid = if self.narrow {
            v_flex()
                .gap_3()
                .child(scene_card)
                .child(polish_card)
                .child(screen_card)
        } else {
            h_flex()
                .gap_3()
                .items_stretch()
                .child(scene_card)
                .child(polish_card)
                .child(screen_card)
        };

        // Recent 3 recordings card
        let mut recent = card(cx).gap_2().child(
            h_flex()
                .justify_between()
                .items_center()
                .child(
                    div()
                        .text_sm()
                        .font_weight(FontWeight::SEMIBOLD)
                        .child(self.text("最近转写", "Recent Transcripts")),
                )
                .child(
                    Button::new("home-view-history")
                        .xsmall()
                        .ghost()
                        .label(self.text("查看全部 →", "View all →"))
                        .on_click(cx.listener(|this, _, window, cx| {
                            this.switch_tab(Tab::History, window, cx)
                        })),
                ),
        );

        let recordings = self.array("recordings");
        if recordings.is_empty() {
            recent = recent.child(
                div()
                    .py_3()
                    .text_xs()
                    .text_color(cx.theme().muted_foreground)
                    .child(self.text(
                        "还没有录音记录。按住 Fn 轻声说一句话，结果会出现在这里。",
                        "No recordings yet. Hold Fn and whisper something to see it here.",
                    )),
            );
        } else {
            for (index, record) in recordings.iter().take(3).enumerate() {
                let id = record["id"].as_str().unwrap_or_default().to_owned();
                let timestamp = readable_timestamp(
                    record["created_at"]
                        .as_str()
                        .or(record["timestamp"].as_str())
                        .unwrap_or(""),
                );
                let duration = record
                    .get("duration_seconds")
                    .or(record.get("duration"))
                    .map(number)
                    .unwrap_or_default();
                let status = record["status"].as_str().unwrap_or("pending");
                let (_, status_tone) = localized_status(status, self.english());
                let transcript = record["transcript"].as_str().unwrap_or_default();
                let error = record["error"].as_str().unwrap_or_default();
                let preview = if !transcript.is_empty() {
                    transcript.chars().take(120).collect::<String>()
                } else if !error.is_empty() {
                    error.to_owned()
                } else {
                    self.text("（无文本）", "(No text)").to_owned()
                };
                let copy_id = id.clone();
                let retry_id = id.clone();
                recent = recent.child(
                    h_flex()
                        .w_full()
                        .min_w_0()
                        .justify_between()
                        .items_start()
                        .gap_3()
                        .py_2()
                        .when(index > 0, |row| {
                            row.border_t_1()
                                .border_color(cx.theme().border.opacity(0.65))
                        })
                        .child(
                            v_flex()
                                .flex_1()
                                .min_w_0()
                                .gap_1()
                                .child(
                                    h_flex()
                                        .items_center()
                                        .gap_2()
                                        .child(status_dot(status_tone, cx))
                                        .child(
                                            div()
                                                .text_xs()
                                                .font_family(cx.theme().mono_font_family.clone())
                                                .text_color(cx.theme().muted_foreground)
                                                .child(format!("{timestamp} · {duration:.0}s")),
                                        ),
                                )
                                .child(
                                    div()
                                        .text_xs()
                                        .whitespace_normal()
                                        .text_color(if status == "failed" {
                                            cx.theme().red
                                        } else {
                                            cx.theme().foreground
                                        })
                                        .child(preview),
                                ),
                        )
                        .child(if status == "failed" {
                            Button::new(("home-retry", index))
                                .xsmall()
                                .primary()
                                .label(self.text("重试", "Retry"))
                                .on_click(cx.listener(move |this, _, _, cx| {
                                    this.action("retryTranscription", json!({"id": retry_id}), cx);
                                }))
                                .into_any_element()
                        } else {
                            Button::new(("home-copy", index))
                                .xsmall()
                                .label(self.text(
                                    if self.copied.as_deref() == Some(&id) {
                                        "已复制"
                                    } else {
                                        "复制"
                                    },
                                    if self.copied.as_deref() == Some(&id) {
                                        "Copied"
                                    } else {
                                        "Copy"
                                    },
                                ))
                                .disabled(transcript.is_empty())
                                .on_click(cx.listener(move |this, _, _, cx| {
                                    this.action("copyTranscript", json!({"id": copy_id}), cx);
                                }))
                                .into_any_element()
                        }),
                );
            }
        }

        v_flex()
            .w_full()
            .min_w_0()
            .gap_4()
            .child(hero)
            .child(quick_grid)
            .child(recent)
    }

    // -------------------------------------------------------------------------
    // 2. History (录音历史 — Content-First)
    // -------------------------------------------------------------------------
    fn history_page(&self, cx: &mut Context<Self>) -> Div {
        let storage = &self.snapshot["recording_storage"];
        let recordings = self.array("recordings");
        let (total, asr, polish) =
            recordings
                .iter()
                .fold((0., 0., 0.), |(total, asr, polish), record| {
                    (
                        total + number(&record["billing"]["total_cost_cny"]),
                        asr + number(&record["billing"]["asr_cost_cny"]),
                        polish + number(&record["billing"]["polish_cost_cny"]),
                    )
                });

        // Compact search + filter toolbar
        let toolbar = h_flex()
            .w_full()
            .min_w_0()
            .gap_2()
            .when(self.narrow, |row| row.flex_col().items_stretch())
            .child(
                div().flex_1().min_w_0().child(
                    Input::new(&self.filter)
                        .id("history-filter")
                        .aria_label(self.text("搜索录音文本", "Search recording text")),
                ),
            )
            .child(
                h_flex().gap_1().flex_shrink_0().children(
                    [
                        ("all", "全部", "All"),
                        ("failed", "失败", "Failed"),
                        ("meeting", "会议", "Meetings"),
                    ]
                    .into_iter()
                    .map(|(filter_id, zh, en)| {
                        Button::new(SharedString::from(format!(
                            "history-filter-btn-{filter_id}"
                        )))
                        .small()
                        .ghost()
                        .label(self.text(zh, en))
                        .selected(self.history_filter == filter_id)
                        .on_click(cx.listener(move |this, _, _, cx| {
                            this.history_filter = filter_id;
                            cx.notify();
                        }))
                    }),
                ),
            );

        // Lightweight single-line summary & storage bar
        let summary_bar = card(cx).py_3().child(
            h_flex()
                .w_full()
                .justify_between()
                .items_center()
                .gap_3()
                .flex_wrap()
                .child(
                    h_flex()
                        .gap_3()
                        .flex_wrap()
                        .items_center()
                        .child(
                            div()
                                .text_xs()
                                .font_family(cx.theme().mono_font_family.clone())
                                .child(format!(
                                    "{} {} · {} ({}/{})",
                                    recordings.len(),
                                    self.text("条", "items"),
                                    bytes(&storage["stored_bytes"]),
                                    storage["compressed_count"],
                                    storage["recording_count"]
                                )),
                        )
                        .child(
                            div()
                                .text_xs()
                                .font_family(cx.theme().mono_font_family.clone())
                                .text_color(cx.theme().muted_foreground)
                                .child(format!(
                                    "{} {} ({} {} / {} {})",
                                    self.text("费用", "Cost"),
                                    money(total),
                                    self.text("识别", "ASR"),
                                    money(asr),
                                    self.text("润色", "Polish"),
                                    money(polish),
                                )),
                        ),
                )
                .child(
                    Button::new("compact-history")
                        .xsmall()
                        .label(self.text(
                            if self.compacting {
                                "正在压缩…"
                            } else {
                                "压缩较早录音"
                            },
                            if self.compacting {
                                "Compressing…"
                            } else {
                                "Compress older"
                            },
                        ))
                        .disabled(self.compacting || number(&storage["recording_count"]) <= 3.)
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.action("compactRecordingHistory", json!({}), cx);
                            cx.notify();
                        })),
                ),
        );

        let mut page = v_flex()
            .w_full()
            .min_w_0()
            .gap_3()
            .child(toolbar)
            .child(summary_bar);

        if self.pending_deletion.is_some() {
            page = page.child(
                h_flex()
                    .w_full()
                    .justify_between()
                    .items_center()
                    .px_3()
                    .py_2p5()
                    .rounded(cx.theme().radius)
                    .bg(cx.theme().yellow_light)
                    .border_1()
                    .border_color(cx.theme().yellow.opacity(0.4))
                    .child(div().text_xs().child(self.text(
                        "录音将于 5 秒后永久删除。",
                        "Recording will be deleted in 5 seconds.",
                    )))
                    .child(
                        Button::new("undo-deletion")
                            .xsmall()
                            .primary()
                            .label(self.text("撤销删除", "Undo delete"))
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.pending_deletion = None;
                                cx.notify();
                            })),
                    ),
            );
        }

        let query = self.filter.read(cx).value().to_lowercase();
        let mut ordered = recordings.iter().enumerate().collect::<Vec<_>>();
        ordered.sort_by_key(|(_, record)| self.focus_recording.as_deref() != record["id"].as_str());

        let mut shown = 0usize;
        for (index, record) in ordered {
            let id = record["id"].as_str().unwrap_or_default().to_owned();
            if self
                .pending_deletion
                .as_ref()
                .is_some_and(|(pending, _)| pending == &id)
            {
                continue;
            }
            let status = record["status"].as_str().unwrap_or("pending");
            let mode = record["mode"].as_str().unwrap_or_default();
            if self.history_filter == "failed" && status != "failed" {
                continue;
            }
            if self.history_filter == "meeting"
                && mode != "meeting"
                && !record["meeting"].is_object()
            {
                continue;
            }
            if !query.is_empty() && !record.to_string().to_lowercase().contains(&query) {
                continue;
            }
            shown += 1;

            let duration = record
                .get("duration_seconds")
                .or(record.get("duration"))
                .map(number)
                .unwrap_or_default();
            let timestamp = readable_timestamp(
                record["created_at"]
                    .as_str()
                    .or(record["timestamp"].as_str())
                    .unwrap_or(""),
            );
            let model = self
                .array("asr_models")
                .iter()
                .find(|model| model["id"] == record["asr_model"])
                .and_then(|model| model["display_name"].as_str())
                .or(record["asr_model"].as_str())
                .unwrap_or("—");
            let (status_label, status_tone) = localized_status(status, self.english());
            let record_cost = number(&record["billing"]["total_cost_cny"]);

            let mut item = card(cx).id(("recording", index)).gap_2p5();
            if self.focus_recording.as_deref() == Some(&id) {
                item = item.border_color(cx.theme().primary);
            } else if status == "failed" {
                item = item.border_color(cx.theme().red.opacity(0.45));
            }

            // Metadata header row
            item = item.child(
                h_flex()
                    .w_full()
                    .justify_between()
                    .items_center()
                    .gap_2()
                    .flex_wrap()
                    .child(
                        h_flex()
                            .items_center()
                            .gap_2()
                            .flex_wrap()
                            .child(status_dot(status_tone, cx))
                            .child(
                                div()
                                    .text_xs()
                                    .font_family(cx.theme().mono_font_family.clone())
                                    .font_weight(FontWeight::SEMIBOLD)
                                    .child(timestamp),
                            )
                            .child(badge(
                                localized_mode(mode, self.english()),
                                Tone::Neutral,
                                cx,
                            ))
                            .child(
                                div()
                                    .text_xs()
                                    .text_color(cx.theme().muted_foreground)
                                    .child(format!("{duration:.0}s · {model}")),
                            ),
                    )
                    .child(
                        h_flex()
                            .items_center()
                            .gap_2()
                            .child(badge(status_label, status_tone, cx))
                            .when(record_cost > 0.0, |row| {
                                row.child(
                                    div()
                                        .text_xs()
                                        .font_family(cx.theme().mono_font_family.clone())
                                        .text_color(cx.theme().muted_foreground)
                                        .child(money(record_cost)),
                                )
                            }),
                    ),
            );

            // Prominent transcript or error body
            if let Some(transcript) = record["transcript"]
                .as_str()
                .filter(|text| !text.is_empty())
            {
                item = item.child(
                    TextView::markdown(("transcript", index), literal_markdown(transcript))
                        .selectable(true),
                );
            }
            if let Some(error) = record["error"].as_str().filter(|text| !text.is_empty()) {
                item = item.child(banner(error.to_owned(), Tone::Danger, cx));
            }

            // Optional meeting structured summary
            if record["meeting"].is_object() {
                let meeting_id = id.clone();
                let expanded = self.expanded_meetings.contains(&id);
                item = item.child(
                    Button::new(("toggle-meeting", index))
                        .xsmall()
                        .ghost()
                        .justify_start()
                        .icon(if expanded {
                            IconName::ChevronDown
                        } else {
                            IconName::ChevronRight
                        })
                        .label(self.text("会议纪要与发言人时间线", "Meeting notes & speakers"))
                        .on_click(cx.listener(move |this, _, _, cx| {
                            if !this.expanded_meetings.remove(&meeting_id) {
                                this.expanded_meetings.insert(meeting_id.clone());
                            }
                            cx.notify();
                        })),
                );
                if expanded {
                    item = item.child(self.meeting(&record["meeting"], index, cx));
                }
            }

            // Compact footer actions
            let retrying = status == "retrying";
            let no_text = record["transcript"].as_str().unwrap_or_default().is_empty();
            let play_id = id.clone();
            let retry_id = id.clone();
            let copy_id = id.clone();
            let delete_id = id.clone();
            let is_playing = self.playing.as_deref() == Some(&id);
            let is_copied = self.copied.as_deref() == Some(&id);

            let footer_actions = h_flex()
                .w_full()
                .justify_between()
                .items_center()
                .gap_2()
                .flex_wrap()
                .pt_1()
                .border_t_1()
                .border_color(cx.theme().border.opacity(0.6))
                .child(
                    h_flex()
                        .gap_1p5()
                        .flex_wrap()
                        .child(
                            Button::new((
                                if is_playing {
                                    "stopRecording"
                                } else {
                                    "playRecording"
                                },
                                index,
                            ))
                            .xsmall()
                            .icon(if is_playing {
                                IconName::Square
                            } else {
                                IconName::Play
                            })
                            .label(self.text(
                                if is_playing { "停止回放" } else { "回放" },
                                if is_playing { "Stop" } else { "Play" },
                            ))
                            .disabled(retrying)
                            .on_click(cx.listener(
                                move |this, _, _, cx| {
                                    let act = if is_playing {
                                        "stopRecording"
                                    } else {
                                        "playRecording"
                                    };
                                    this.action(act, json!({"id": play_id}), cx);
                                    cx.notify();
                                },
                            )),
                        )
                        .child(
                            Button::new(("copyTranscript", index))
                                .xsmall()
                                .icon(if is_copied {
                                    IconName::Check
                                } else {
                                    IconName::Copy
                                })
                                .label(self.text(
                                    if is_copied {
                                        "已复制"
                                    } else {
                                        "复制文本"
                                    },
                                    if is_copied { "Copied" } else { "Copy" },
                                ))
                                .disabled(no_text)
                                .on_click(cx.listener(move |this, _, _, cx| {
                                    this.action("copyTranscript", json!({"id": copy_id}), cx);
                                    cx.notify();
                                })),
                        )
                        .child({
                            let btn = Button::new(("retryTranscription", index))
                                .xsmall()
                                .icon(IconName::RefreshCw)
                                .label(self.text("重新识别", "Retry"))
                                .disabled(retrying)
                                .on_click(cx.listener(move |this, _, _, cx| {
                                    this.action("retryTranscription", json!({"id": retry_id}), cx);
                                    cx.notify();
                                }));
                            if status == "failed" {
                                btn.primary()
                            } else {
                                btn
                            }
                        }),
                )
                .child(
                    Button::new(("delete-recording", index))
                        .xsmall()
                        .danger()
                        .label(self.text("删除", "Delete"))
                        .disabled(retrying)
                        .on_click(cx.listener(move |this, _, window, cx| {
                            this.stage_delete(delete_id.clone(), window, cx);
                        })),
                );

            page = page.child(item.child(footer_actions));
        }

        if shown == 0 {
            page = page.child(
                card(cx).items_center().py_8().child(
                    div()
                        .text_xs()
                        .text_color(cx.theme().muted_foreground)
                        .child(self.text("没有匹配的录音记录。", "No matching recordings found.")),
                ),
            );
        }

        page
    }

    fn meeting(&self, meeting: &Value, index: usize, cx: &App) -> Div {
        let mut view = v_flex()
            .gap_2()
            .p_3()
            .rounded_md()
            .bg(cx.theme().secondary.opacity(0.6))
            .border_1()
            .border_color(cx.theme().border);
        let minutes = if meeting["minutes"].is_object() {
            &meeting["minutes"]
        } else {
            meeting
        };
        if let Some(summary) = minutes["summary"].as_str().filter(|s| !s.is_empty()) {
            view = view.child(
                v_flex()
                    .gap_1()
                    .child(
                        div()
                            .text_xs()
                            .font_weight(FontWeight::SEMIBOLD)
                            .child(self.text("会议摘要", "Meeting summary")),
                    )
                    .child(
                        TextView::markdown(("meeting-summary", index), literal_markdown(summary))
                            .selectable(true),
                    ),
            );
        }
        for (field, zh, en) in [
            ("key_points", "要点", "Key points"),
            ("action_items", "待办事项", "Action items"),
        ] {
            if let Some(items) = minutes[field].as_array().filter(|a| !a.is_empty()) {
                let mut list = v_flex().gap_1().child(
                    div()
                        .text_xs()
                        .font_weight(FontWeight::SEMIBOLD)
                        .child(self.text(zh, en)),
                );
                for item in items.iter().filter_map(Value::as_str) {
                    list = list.child(div().text_xs().child(format!("• {item}")));
                }
                view = view.child(list);
            }
        }
        if let Some(segments) = meeting["segments"].as_array().filter(|a| !a.is_empty()) {
            let mut seg_list = v_flex().gap_1().child(
                div()
                    .text_xs()
                    .font_weight(FontWeight::SEMIBOLD)
                    .child(self.text("发言人时间线", "Speaker timeline")),
            );
            for segment in segments {
                let speaker = segment["speaker_label"]
                    .as_str()
                    .or(segment["speaker"].as_str())
                    .unwrap_or_else(|| self.text("发言人", "Speaker"));
                let start = segment["timestamp"]
                    .as_str()
                    .or(segment["start"].as_str())
                    .map(str::to_owned)
                    .unwrap_or_else(|| format!("{:.1}s", number(&segment["start_seconds"])));
                let text = segment["text"].as_str().unwrap_or_default();
                seg_list = seg_list.child(
                    div()
                        .text_xs()
                        .whitespace_normal()
                        .child(format!("[{start}] {speaker}: {text}")),
                );
            }
            view = view.child(seg_list);
        }
        for value in [meeting, minutes] {
            if let Some(error) = value["error"].as_str().filter(|s| !s.is_empty()) {
                view = view.child(banner(error.to_owned(), Tone::Danger, cx));
            }
        }
        view
    }

    // -------------------------------------------------------------------------
    // 3. Dictionary (个人词典) — implemented in `dictionary.rs`
    // -------------------------------------------------------------------------

    // -------------------------------------------------------------------------
    // 4. Audio & Low-Voice (声音与低声)
    // -------------------------------------------------------------------------
    fn audio_page(&self, cx: &mut Context<Self>) -> Div {
        let active_preset = self.active_audio_preset();
        let presets_data = [
            (
                "whisper",
                self.text("办公室低声", "Office Whisper"),
                self.text("推荐", "Recommended"),
                self.text(
                    "轻声耳语也能清晰拾音，过滤风扇空调底噪",
                    "High gain + high-pass filter for quiet shared offices",
                ),
                "+18 dB · HPF 220 Hz",
            ),
            (
                "normal",
                self.text("正常说话", "Normal Voice"),
                "",
                self.text(
                    "自然动态范围，适合独立房间或日常对话",
                    "Balanced gain for private rooms and everyday speech",
                ),
                "+12 dB · HPF 200 Hz",
            ),
            (
                "noisy",
                self.text("嘈杂环境", "Noisy Room"),
                "",
                self.text(
                    "强力削减低频轰鸣，减少开放空间背景干扰",
                    "Stronger rumble cut for noisy open environments",
                ),
                "+16 dB · HPF 280 Hz",
            ),
        ];

        let preset_cards = presets_data
            .into_iter()
            .map(|(id, title, tag, desc, meta)| {
                let selected = active_preset == Some(id);
                v_flex()
                    .flex_1()
                    .min_w_0()
                    .gap_2()
                    .p_3()
                    .rounded(cx.theme().radius_lg)
                    .border_1()
                    .border_color(if selected {
                        cx.theme().foreground
                    } else {
                        cx.theme().border
                    })
                    .bg(if selected {
                        cx.theme().secondary
                    } else {
                        cx.theme().secondary.opacity(0.35)
                    })
                    .child(
                        h_flex()
                            .justify_between()
                            .items_center()
                            .gap_1()
                            .child(
                                div()
                                    .text_xs()
                                    .font_weight(FontWeight::SEMIBOLD)
                                    .child(title),
                            )
                            .when(!tag.is_empty() && !selected, |row| {
                                row.child(badge(tag, Tone::Primary, cx))
                            })
                            .when(selected, |row| {
                                row.child(badge(self.text("当前", "Active"), Tone::Success, cx))
                            }),
                    )
                    .child(
                        div()
                            .text_xs()
                            .whitespace_normal()
                            .text_color(cx.theme().muted_foreground)
                            .child(desc),
                    )
                    .child(
                        h_flex()
                            .justify_between()
                            .items_center()
                            .gap_2()
                            .pt_1()
                            .child(
                                div()
                                    .text_xs()
                                    .font_family(cx.theme().mono_font_family.clone())
                                    .text_color(cx.theme().muted_foreground)
                                    .child(meta),
                            )
                            .child(
                                Button::new(id)
                                    .xsmall()
                                    .label(if selected {
                                        self.text("已启用", "Selected")
                                    } else {
                                        self.text("使用", "Use")
                                    })
                                    .selected(selected)
                                    .when(selected, |b| b.icon(Icon::new(IconName::Check)))
                                    .disabled(self.audio_busy())
                                    .on_click(cx.listener(move |this, _, window, cx| {
                                        this.preset(id, window, cx)
                                    })),
                            ),
                    )
                    .into_any_element()
            });

        let scene_section = card(cx)
            .child(
                h_flex()
                    .w_full()
                    .justify_between()
                    .items_center()
                    .gap_3()
                    .flex_wrap()
                    .child(
                        v_flex()
                            .gap_0p5()
                            .child(
                                div()
                                    .text_sm()
                                    .font_weight(FontWeight::SEMIBOLD)
                                    .child(self.text("声音场景预设", "Voice Scene Presets")),
                            )
                            .child(
                                div()
                                    .text_xs()
                                    .text_color(cx.theme().muted_foreground)
                                    .child(self.text(
                                        "一键切换增益、低频滤波与防削波限幅，或通过 8 秒校准自动匹配你的声音。",
                                        "Tune gain, high-pass filter, and soft limiter together, or run 8s calibration.",
                                    )),
                            ),
                    )
                    .child(
                        Button::new("calibrate-whisper")
                            .small()
                            .primary()
                            .label(self.text("低声智能校准", "Whisper calibration"))
                            .disabled(self.audio_busy())
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.calibration.open = true;
                                cx.notify();
                            })),
                    ),
            )
            .child(if self.narrow {
                v_flex().gap_2p5().children(preset_cards)
            } else {
                h_flex().gap_2p5().items_stretch().children(preset_cards)
            });

        // Microphone selection + Live Test integrated card
        let input_status = &self.snapshot["audio_input_status"];
        let mic_perm_denied = matches!(
            input_status["microphone_permission"].as_str(),
            Some("denied" | "restricted")
        );
        let mut mic_section = titled_card(
            self.text("麦克风与实时试音", "Microphone & Live Test"),
            Some(self.text(
                "选择拾音设备，并录一段轻声语音回放验证清晰度。",
                "Choose your microphone and record a short whisper sample to verify clarity.",
            )),
            cx,
        )
        .child(self.field_row("audio.input_device", false, cx))
        .child(
            h_flex()
                .gap_2()
                .flex_wrap()
                .child(
                    Button::new("refresh-devices")
                        .small()
                        .icon(IconName::RefreshCw)
                        .label(self.text("刷新设备", "Refresh devices"))
                        .disabled(self.audio_busy())
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.action("refreshDevices", json!({}), cx);
                        })),
                )
                .child(
                    Button::new("microphone-permissions")
                        .small()
                        .label(self.text("麦克风权限设置", "Microphone permissions"))
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.action("openMicrophoneSettings", json!({}), cx);
                        })),
                ),
        );
        if mic_perm_denied {
            mic_section = mic_section.child(banner(
                self.text(
                    "macOS 麦克风权限尚未开启，请点击上方“麦克风权限设置”授权后重新检查。",
                    "Microphone access is denied. Open Microphone permissions above to grant access.",
                ),
                Tone::Danger,
                cx,
            ));
        }
        if let Some(reason) = input_status["fallback_reason"]
            .as_str()
            .filter(|s| !s.is_empty())
        {
            mic_section = mic_section.child(banner(reason.to_owned(), Tone::Warning, cx));
        }
        mic_section = mic_section.child(self.mic_card_body(cx));

        // Collapsible Advanced Audio DSP & Diagnostics
        let gain_linear = get(self.config(), "audio.gain").as_f64().unwrap_or(2.0);
        let hp_freq = get(self.config(), "audio.highpass_freq")
            .as_f64()
            .unwrap_or(200.0);
        let hp_on = get(self.config(), "audio.highpass_filter") == true;
        let tail_ms = get(self.config(), "audio.tail_padding_ms")
            .as_u64()
            .unwrap_or(200);
        let summary = format!(
            "{} · {} · {} · {} {} ms",
            if get(self.config(), "audio.gain_mode") == "automatic" {
                self.text("自动增益", "Auto gain").to_owned()
            } else {
                format!(
                    "{} {}",
                    self.text("手动增益", "Manual"),
                    format_db_from_linear(gain_linear)
                )
            },
            if hp_on {
                format!("{} {hp_freq:.0} Hz", self.text("4阶高通", "4th HPF"))
            } else {
                self.text("高通关闭", "HPF Off").to_owned()
            },
            if get(self.config(), "audio.soft_limiter") == true {
                self.text("动态限幅开", "Dynamics On")
            } else {
                self.text("动态限幅关", "Dynamics Off")
            },
            self.text("尾音", "Tail"),
            tail_ms
        );

        let advanced_audio =
            self.disclosure(
                "audio",
                self.text("高级音频参数与运行诊断", "Advanced Audio DSP & Diagnostics"),
                summary,
                |this, cx| {
                    let input = &this.snapshot["audio_input_status"];
                    let labels = [
                        ("device_name", "当前输入设备", "Active input"),
                        ("phase", "采集状态", "Capture state"),
                        ("capture_channels", "采集声道", "Capture channels"),
                        ("processing_mode", "输入处理", "Input processing"),
                        ("echo_cancellation", "回声消除", "Echo cancellation"),
                        ("gain_control", "实际增益控制", "Effective gain control"),
                        (
                            "microphone_permission",
                            "麦克风权限",
                            "Microphone permission",
                        ),
                        ("native_backend", "原生后端", "Native backend"),
                        (
                            "active_microphone_mode",
                            "系统麦克风模式",
                            "System microphone mode",
                        ),
                        ("source_sample_rate_hz", "源采样率", "Source sample rate"),
                        ("output_sample_rate_hz", "输出采样率", "Output sample rate"),
                        ("queue_dropped_blocks", "丢弃音频块", "Dropped audio blocks"),
                        ("runtime_fault_count", "运行故障", "Runtime faults"),
                    ];
                    let mut diag =
                        v_flex()
                            .gap_1()
                            .p_3()
                            .rounded_md()
                            .bg(cx.theme().secondary.opacity(0.45))
                            .border_1()
                            .border_color(cx.theme().border)
                            .child(div().text_xs().font_weight(FontWeight::SEMIBOLD).child(
                                this.text("运行状态与诊断", "Runtime status & diagnostics"),
                            ));
                    for (key, zh, en) in labels {
                        if !input[key].is_null() {
                            diag = diag.child(readout(
                                this.text(zh, en),
                                schema::display_value(&input[key]),
                                cx,
                            ));
                        }
                    }
                    if !input.is_null() {
                        diag = diag.child(
                            TextView::markdown(
                                "audio-diagnostics",
                                format!(
                                    "```json\n{}\n```",
                                    serde_json::to_string_pretty(input).unwrap_or_default()
                                ),
                            )
                            .selectable(true),
                        );
                    }
                    v_flex()
                        .gap_3()
                        .child(this.field_group(
                            &[
                                "audio.capture_backend",
                                "audio.gain_mode",
                                "audio.gain",
                                "audio.highpass_filter",
                                "audio.highpass_freq",
                                "audio.soft_limiter",
                                "audio.tail_padding_ms",
                                "audio.waveform_ceiling_dbfs",
                            ],
                            cx,
                        ))
                        .child(diag)
                },
                cx,
            );

        v_flex()
            .w_full()
            .min_w_0()
            .gap_4()
            .child(scene_section)
            .child(mic_section)
            .child(advanced_audio)
    }

    fn mic_card_body(&self, cx: &mut Context<Self>) -> Div {
        let mut test = v_flex()
            .gap_2p5()
            .p_3()
            .rounded(cx.theme().radius)
            .bg(cx.theme().secondary.opacity(0.45))
            .border_1()
            .border_color(cx.theme().border);

        if matches!(self.mic, MicState::Starting | MicState::Recording) {
            let level = calibration::waveform(
                self.mic_level,
                get(self.config(), "audio.waveform_ceiling_dbfs")
                    .as_f64()
                    .unwrap_or(-6.),
            ) * 100.;
            test = test
                .child(
                    h_flex()
                        .justify_between()
                        .items_center()
                        .child(div().text_xs().font_weight(FontWeight::MEDIUM).child(
                            self.text("正在试音，请轻声说一句话…", "Listening — whisper a phrase…"),
                        ))
                        .child(
                            div()
                                .text_xs()
                                .font_family(cx.theme().mono_font_family.clone())
                                .text_color(cx.theme().muted_foreground)
                                .child(format!("{:.1} dBFS", calibration::dbfs(self.mic_level))),
                        ),
                )
                .child(
                    Progress::new("mic-level")
                        .accessibility_label(self.text("麦克风电平", "Microphone level"))
                        .value(level),
                )
                .child(
                    Button::new("stop-mic-test")
                        .small()
                        .danger()
                        .label(self.text("停止试音", "Stop"))
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.stop_mic();
                            cx.notify();
                        })),
                );
        } else {
            if let Some(error) = &self.mic_error {
                test = test.child(banner(error.clone(), Tone::Danger, cx));
            }
            let mut row_actions = h_flex().gap_2().flex_wrap().child(
                Button::new("start-mic-test")
                    .small()
                    .primary()
                    .icon(IconName::Mic)
                    .label(self.text(
                        if self.mic == MicState::Done {
                            "重新试音"
                        } else {
                            "开始试音"
                        },
                        if self.mic == MicState::Done {
                            "Test again"
                        } else {
                            "Start mic test"
                        },
                    ))
                    .disabled(self.audio_busy())
                    .on_click(cx.listener(|this, _, _, cx| this.start_mic(cx))),
            );
            if self.mic_playable {
                row_actions = row_actions.child(
                    Button::new("replay-mic-test")
                        .small()
                        .icon(if self.mic_playing {
                            IconName::Square
                        } else {
                            IconName::Play
                        })
                        .label(self.text(
                            if self.mic_playing {
                                "停止回放"
                            } else {
                                "回放试音"
                            },
                            if self.mic_playing {
                                "Stop playback"
                            } else {
                                "Play recording"
                            },
                        ))
                        .on_click(cx.listener(|this, _, _, cx| {
                            if this.mic_playing {
                                this.commands.request("stop_mic_test_playback", json!({}));
                                this.mic_playing = false;
                            } else {
                                this.action("playMicTest", json!({}), cx);
                            }
                            cx.notify();
                        })),
                );
            }
            test = test.child(row_actions);
        }
        test
    }

    // -------------------------------------------------------------------------
    // 5. AI & Output (识别与润色 — Merged Recognition + 0.6.2 Dictation Scenes + Polish)
    // -------------------------------------------------------------------------
    fn ai_page(&self, cx: &mut Context<Self>) -> Div {
        let selected_model = self
            .array("asr_models")
            .iter()
            .find(|model| &model["id"] == get(self.config(), "asr.model"));

        // Capability badges for selected ASR model
        let mut cap_badges = h_flex().gap_1p5().flex_wrap();
        if let Some(model) = selected_model {
            if model["transport"] == "realtime_ws" {
                cap_badges =
                    cap_badges.child(badge(self.text("流式实时", "Realtime"), Tone::Neutral, cx));
            }
            if model["pipeline"] == "hybrid_asr" {
                cap_badges = cap_badges.child(badge(
                    self.text("输入法专精 · 原生去口语", "Input-method native polish"),
                    Tone::Success,
                    cx,
                ));
            }
            if model["pipeline"] == "native_asr" {
                cap_badges = cap_badges.child(badge(
                    self.text("原生直出（跳过润色）", "Native direct"),
                    Tone::Warning,
                    cx,
                ));
            }
            if model["supports_screen_context"] == true {
                cap_badges = cap_badges.child(badge(
                    self.text("支持看屏幕说话", "Screen-aware"),
                    Tone::Primary,
                    cx,
                ));
            }
            if model["handles_inline_polish"] == true {
                cap_badges = cap_badges.child(badge(
                    self.text("模型内润色", "Inline polish"),
                    Tone::Success,
                    cx,
                ));
            }
            if model["supports_super_hotwords"] == true {
                cap_badges = cap_badges.child(badge(
                    self.text("超级热词", "Super hotwords"),
                    Tone::Primary,
                    cx,
                ));
            } else if model["supports_instant_hotwords"] == true {
                cap_badges = cap_badges.child(badge(
                    self.text("即时词典", "Instant hotwords"),
                    Tone::Neutral,
                    cx,
                ));
            }
        }

        let mut recognition_card = titled_card(
            self.text("语音识别", "Speech Recognition"),
            Some(self.text(
                "选择云端实时识别模型与语言，并配置光标上下文与屏幕上下文辅助识别。",
                "Choose the ASR model, spoken language, cursor context, and screen assistance.",
            )),
            cx,
        )
        .child(self.field_row("asr.model", false, cx))
        .child(cap_badges)
        .child(self.field_row("asr.language", true, cx))
        .child(self.field_row("cursor_context_enabled", false, cx))
        .child(self.field_row("screen_context_enabled", false, cx));

        if get(self.config(), "cursor_context_enabled") == true && !self.accessibility_ready() {
            recognition_card = recognition_card.child(
                h_flex()
                    .w_full()
                    .justify_between()
                    .items_center()
                    .gap_2()
                    .flex_wrap()
                    .px_3()
                    .py_2()
                    .rounded(cx.theme().radius)
                    .bg(cx.theme().yellow_light)
                    .border_1()
                    .border_color(cx.theme().yellow.opacity(0.45))
                    .child(div().text_xs().child(self.text(
                        "开启“辅助功能”权限后才能读取光标前后文与选中术语。",
                        "Grant Accessibility permission so cursor context can read nearby terms.",
                    )))
                    .child(
                        Button::new("ai-fix-ax")
                            .xsmall()
                            .label(self.text("打开辅助功能设置", "Accessibility Settings"))
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.action("openAccessibilitySettings", json!({}), cx);
                            })),
                    ),
            );
        }

        if get(self.config(), "screen_context_enabled") == true
            && selected_model.is_some_and(|m| m["supports_screen_context"] != true)
        {
            recognition_card = recognition_card.child(banner(
                self.text(
                    "已开启“看屏幕说话”：听写时将自动使用 Qwen3.8 Omni Flash Realtime 结合屏幕画面识别，关闭后自动恢复当前所选模型。",
                    "Screen Context is on: dictation automatically routes to Qwen3.8 Omni Flash Realtime and returns to your selected model when turned off.",
                ),
                Tone::Primary,
                cx,
            ));
        }

        // 0.6.2 Custom Dictation Scenes (integrated seamlessly between Recognition & Polish)
        let dictation_scenes_card = self.dictation_scenes(cx);

        // Text Polish Card with One-Click Style Presets
        let active_style = self.active_polish_style();
        let polish_disabled = self.native_asr();
        let style_bar = h_flex().gap_1p5().flex_wrap().children(
            [
                ("minimal", "原话轻修", "Light Clean"),
                ("structured", "清晰结构化", "Structured"),
                ("technical", "技术文档", "Technical"),
                ("chat", "轻松聊天", "Casual Chat"),
                ("prompt", "AI 提示词", "AI Prompt"),
            ]
            .into_iter()
            .map(|(style_id, zh, en)| {
                Button::new(SharedString::from(format!("polish-style-{style_id}")))
                    .xsmall()
                    .label(self.text(zh, en))
                    .selected(active_style == Some(style_id) && !polish_disabled)
                    .disabled(polish_disabled)
                    .on_click(cx.listener(move |this, _, window, cx| {
                        this.apply_polish_style(style_id, window, cx)
                    }))
            }),
        );

        let mut polish_card = titled_card(
            self.text("文本润色与输出风格", "Text Polish & Output Style"),
            Some(self.text(
                "先选一键风格预设，或按需微调输出类型、语气和写作风格。",
                "Pick a quick style preset or fine-tune output type, tone, and persona.",
            )),
            cx,
        )
        .child(self.field_row("enable_polish", true, cx));

        if self.native_asr() {
            polish_card = polish_card.child(banner(
                self.text(
                    "当前使用原生直出 ASR 模型，系统会跳过二阶段润色以获得更低延迟。",
                    "Native ASR transcribes directly and skips second-stage polish.",
                ),
                Tone::Warning,
                cx,
            ));
        } else if self.hybrid_asr() && get(self.config(), "enable_polish") == true {
            let llm_model = get(self.config(), "llm.model")
                .as_str()
                .unwrap_or("qwen3.8-flash");
            if self.hybrid_needs_second_stage() {
                polish_card = polish_card.child(banner(
                    if self.english() {
                        format!(
                            "Active pipeline: 3.1 ASR transcription → {llm_model} second-stage polish."
                        )
                    } else {
                        format!("当前链路：3.1 ASR 识别 → {llm_model} 二阶段深度润色。")
                    },
                    Tone::Primary,
                    cx,
                ));
            } else {
                polish_card = polish_card.child(banner(
                    self.text(
                        "当前链路：3.1 输入法模型原生单级顺滑（极速低延迟，自动跳过二阶段大模型）。",
                        "Active pipeline: 3.1 Message native single-pass cleanup (lowest latency, skips second-stage LLM).",
                    ),
                    Tone::Success,
                    cx,
                ));
            }
        }

        polish_card = polish_card
            .child(
                v_flex()
                    .gap_1p5()
                    .child(
                        div()
                            .text_xs()
                            .font_weight(FontWeight::MEDIUM)
                            .text_color(cx.theme().muted_foreground)
                            .child(self.text("一键风格预设", "Quick Style Presets")),
                    )
                    .child(style_bar),
            )
            .child(self.field_group(
                &[
                    "llm.polish_mode",
                    "llm.output_language",
                    "llm.level",
                    "llm.structured",
                    "llm.tone",
                    "llm.persona",
                ],
                cx,
            ));

        // Collapsible Advanced Model Endpoint & Custom Prompts
        let endpoint_summary = if get(self.config(), "asr.realtime_url")
            .as_str()
            .unwrap_or_default()
            .is_empty()
        {
            self.text("公共实时端点", "Public realtime endpoint")
        } else {
            self.text("自定义空间端点", "Custom workspace endpoint")
        };
        let llm_model = get(self.config(), "llm.model")
            .as_str()
            .unwrap_or("qwen3.8-flash");
        let ai_advanced = self.disclosure(
            "ai",
            self.text(
                "高级模型端点与自定义提示词",
                "Workspace Endpoint, LLM & Custom Prompts",
            ),
            format!(
                "{endpoint_summary} · {llm_model} · {}",
                self.text("5 类自定义提示词", "5 custom prompt categories")
            ),
            |this, cx| {
                let mut inner =
                    v_flex()
                        .gap_3()
                        .child(this.field_row("asr.realtime_url", false, cx))
                        .child(
                            h_flex().child(
                                Button::new("public-endpoint")
                                    .xsmall()
                                    .label(this.text(
                                        "恢复公共实时端点",
                                        "Restore public realtime endpoint",
                                    ))
                                    .on_click(cx.listener(|this, _, window, cx| {
                                        this.set_config("asr.realtime_url", json!(""), window, cx);
                                        this.sync_controls(window, cx, true);
                                    })),
                            ),
                        );

                if let Some(model) = this
                    .array("asr_models")
                    .iter()
                    .find(|model| &model["id"] == get(this.config(), "asr.model"))
                {
                    let mut specs = v_flex()
                        .gap_1()
                        .p_3()
                        .rounded_md()
                        .bg(cx.theme().secondary.opacity(0.45))
                        .border_1()
                        .border_color(cx.theme().border)
                        .child(
                            div()
                                .text_xs()
                                .font_weight(FontWeight::SEMIBOLD)
                                .child(this.text("识别模型详细规格", "ASR Model Specifications")),
                        );
                    for (key, zh, en) in [
                        ("transport", "传输方式", "Transport"),
                        ("pipeline", "处理流水线", "Pipeline"),
                        (
                            "supports_super_hotwords",
                            "超级热词（权重 50）",
                            "Super hotwords",
                        ),
                        (
                            "supports_native_disfluency_removal",
                            "原生口语顺滑",
                            "Native disfluency removal",
                        ),
                        ("language_count", "支持语言数量", "Languages"),
                        ("chinese_dialect_count", "中文方言数量", "Chinese dialects"),
                        (
                            "max_input_tokens",
                            "上下文 Token 上限",
                            "Context token limit",
                        ),
                        (
                            "max_audio_seconds",
                            "音频时间上限（秒）",
                            "Maximum audio seconds",
                        ),
                        (
                            "rollover_audio_seconds",
                            "长录音滚动边界（秒）",
                            "Rollover seconds",
                        ),
                    ] {
                        if !model[key].is_null() {
                            specs = specs.child(readout(
                                this.text(zh, en),
                                schema::display_value(&model[key]),
                                cx,
                            ));
                        }
                    }
                    inner = inner.child(specs);
                }

                inner = inner.child(
                    this.field_group(&["llm.model", "llm.temperature", "llm.enable_thinking"], cx),
                );

                // 5-category Custom Prompt Override Editor
                let category = this.prompt_category;
                let enabled = this.prompt_enabled(category);
                let disabled = this.disabled("llm.prompt_overrides");
                let categories =
                    h_flex()
                        .gap_1()
                        .flex_wrap()
                        .children(schema::PROMPT_CATEGORIES.iter().map(|&(category, zh, en)| {
                            Button::new(category)
                                .xsmall()
                                .label(this.text(zh, en))
                                .selected(this.prompt_category == category)
                                .on_click(cx.listener(move |this, _, window, cx| {
                                    this.prompt_category = category;
                                    this.sync_controls(window, cx, false);
                                    cx.notify();
                                }))
                                .into_any_element()
                        }));
                let custom_switch = Switch::new("custom-prompt-enabled")
                    .label(this.text("使用自定义提示词", "Use custom prompt"))
                    .checked(enabled)
                    .disabled(disabled)
                    .on_change(cx.listener(move |this, enabled, window, cx| {
                        let text = if *enabled {
                            this.config()["llm"]["prompt_overrides"][category]["prompt"]
                                .as_str()
                                .filter(|prompt| !prompt.is_empty())
                                .map(str::to_owned)
                                .unwrap_or_else(|| this.prompt_preset(category))
                        } else {
                            this.prompt_text(category)
                        };
                        this.set_prompt(category, *enabled, text, window, cx);
                    }));

                inner.child(
                    v_flex()
                        .gap_2p5()
                        .p_3()
                        .rounded_md()
                        .bg(cx.theme().secondary.opacity(0.45))
                        .border_1()
                        .border_color(cx.theme().border)
                        .child(
                            div()
                                .text_xs()
                                .font_weight(FontWeight::SEMIBOLD)
                                .child(this.text("自定义提示词覆盖", "Custom Prompt Overrides")),
                        )
                        .child(categories)
                        .child(custom_switch)
                        .child(
                            Textarea::new(&this.prompts[category])
                                .aria_label(this.text("提示词内容", "Prompt text"))
                                .h(px(180.))
                                .readonly(!enabled)
                                .disabled(disabled),
                        )
                        .child(
                            h_flex()
                                .justify_between()
                                .items_center()
                                .gap_2()
                                .flex_wrap()
                                .child(
                                    Button::new("reload-prompt-preset")
                                        .xsmall()
                                        .label(
                                            this.text("重新载入系统预设", "Reload system preset"),
                                        )
                                        .disabled(disabled || !enabled)
                                        .on_click(cx.listener(move |this, _, window, cx| {
                                            let preset = this.prompt_preset(category);
                                            this.set_prompt(category, true, preset, window, cx);
                                        })),
                                )
                                .child(
                                    div()
                                        .text_xs()
                                        .text_color(cx.theme().muted_foreground)
                                        .child(this.text(
                                            "失焦时自动保存。",
                                            "Saves when focus leaves the editor.",
                                        )),
                                ),
                        ),
                )
            },
            cx,
        );

        v_flex()
            .w_full()
            .min_w_0()
            .gap_4()
            .child(recognition_card)
            .child(dictation_scenes_card)
            .child(polish_card)
            .child(ai_advanced)
    }

    // -------------------------------------------------------------------------
    // 6. Shortcuts & Input (快捷键与输入)
    // -------------------------------------------------------------------------
    fn shortcuts_page(&self, cx: &mut Context<Self>) -> Div {
        let active = get(self.config(), "hotkey.active_hotkeys")
            .as_array()
            .cloned()
            .unwrap_or_else(|| vec![json!("fn")]);
        let fn_enabled = active.iter().any(|key| key == "fn");
        let keys = shortcut::custom_keys(self.config());

        let mut trigger_card = titled_card(
            self.text("全局触发快捷键", "Global Trigger Hotkeys"),
            Some(self.text(
                "按住说话松开结束；短按进入免提长录音；Esc 随时取消。",
                "Hold to speak and release to finish; tap for hands-free; Escape cancels.",
            )),
            cx,
        )
        .child(
            h_flex()
                .w_full()
                .justify_between()
                .items_center()
                .gap_3()
                .py_2()
                .border_b_1()
                .border_color(cx.theme().border.opacity(0.7))
                .child(
                    h_flex()
                        .items_center()
                        .gap_2()
                        .child(keycap("Fn / Globe", cx))
                        .child(
                            div()
                                .text_sm()
                                .font_weight(FontWeight::MEDIUM)
                                .child(self.text("内置地球键", "Built-in Globe key")),
                        ),
                )
                .child(
                    Switch::new("fn-hotkey")
                        .accessibility_label("Fn / Globe")
                        .checked(fn_enabled)
                        .on_change(cx.listener(move |this, enabled, _, cx| {
                            let mut active = get(this.config(), "hotkey.active_hotkeys")
                                .as_array()
                                .cloned()
                                .unwrap_or_default();
                            active.retain(|key| key != "fn");
                            if *enabled {
                                active.push(json!("fn"));
                            }
                            this.action("setActiveHotkeys", json!({"hotkeys": active}), cx);
                            cx.notify();
                        })),
                ),
        );

        let mut custom_list = h_flex().gap_2().flex_wrap();
        for key in &keys {
            let code = key["key_code"].clone();
            let name = key["display_name"].as_str().unwrap_or("Key").to_owned();
            custom_list = custom_list.child(
                h_flex()
                    .items_center()
                    .gap_1p5()
                    .pl_2p5()
                    .pr_1()
                    .py_1()
                    .rounded(cx.theme().radius)
                    .bg(cx.theme().secondary.opacity(0.55))
                    .border_1()
                    .border_color(cx.theme().border)
                    .child(
                        div()
                            .text_xs()
                            .font_family(cx.theme().mono_font_family.clone())
                            .font_weight(FontWeight::MEDIUM)
                            .child(name),
                    )
                    .child(
                        Button::new(("remove-key", code.as_u64().unwrap_or_default() as usize))
                            .xsmall()
                            .ghost()
                            .label(self.text("移除", "Remove"))
                            .on_click(cx.listener(move |this, _, window, cx| {
                                let keys = shortcut::custom_keys(this.config())
                                    .into_iter()
                                    .filter(|key| key["key_code"] != code)
                                    .collect::<Vec<_>>();
                                this.set_config("hotkey.custom_keys", json!(keys), window, cx);
                            })),
                    ),
            );
        }

        if self.capture.active {
            let capture_msg = if self.capture.pending.is_some() {
                self.text(
                    "请再次按下同一修饰键以确认；按 Esc 取消录入。",
                    "Press the same modifier again to confirm; Escape cancels.",
                )
            } else {
                self.text(
                    "正在监听按键… 请按下触发键（单独修饰键需连按两次确认，Esc 取消）。",
                    "Listening… Press a trigger key (bare modifiers need two presses; Escape cancels).",
                )
            };
            trigger_card = trigger_card.child(banner(capture_msg, Tone::Primary, cx));
        }

        trigger_card = trigger_card
            .child(
                h_flex()
                    .justify_between()
                    .items_center()
                    .gap_2()
                    .flex_wrap()
                    .child(
                        div()
                            .text_xs()
                            .text_color(cx.theme().muted_foreground)
                            .child(self.text(
                                "自定义触发键（最多 8 个，区分左右修饰键）",
                                "Custom trigger keys (up to 8, left/right modifier aware)",
                            )),
                    )
                    .child(
                        Button::new("capture-key")
                            .small()
                            .primary()
                            .label(self.text(
                                if self.capture.active {
                                    "取消录入"
                                } else {
                                    "添加触发键"
                                },
                                if self.capture.active {
                                    "Cancel capture"
                                } else {
                                    "Add trigger key"
                                },
                            ))
                            .disabled(!self.capture.active && keys.len() >= 8)
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.capture.active = !this.capture.active;
                                this.capture.pending = None;
                                this.commands.request(
                                    if this.capture.active {
                                        "begin_hotkey_capture"
                                    } else {
                                        "end_hotkey_capture"
                                    },
                                    json!({}),
                                );
                                cx.notify();
                            })),
                    ),
            )
            .when(!keys.is_empty(), |card| card.child(custom_list));

        let input_card = titled_card(
            self.text("录音模式与文本输入", "Recording Mode & Text Insertion"),
            Some(self.text(
                "决定录音默认手势，以及转写完成后如何上屏到当前应用。",
                "Choose default gesture and how recognized text is inserted.",
            )),
            cx,
        )
        .child(self.field_group(&["default_mode", "auto_paste", "streaming_paste"], cx));

        let double_tap = get(self.config(), "hotkey.double_tap_threshold")
            .as_f64()
            .unwrap_or(0.3);
        let shortcuts_advanced = self.disclosure(
            "shortcuts",
            self.text("高级输入与热键参数", "Advanced Input & Hotkey Tuning"),
            format!(
                "{} {:.2} s · {} · {}",
                self.text("双击间隔", "Double-tap"),
                double_tap,
                if get(self.config(), "native_fast_paste") == true {
                    self.text("原生快速输入开", "Fast paste On")
                } else {
                    self.text("原生快速输入关", "Fast paste Off")
                },
                if get(self.config(), "restore_clipboard") == true {
                    self.text("恢复剪贴板开", "Restore clipboard On")
                } else {
                    self.text("恢复剪贴板关", "Restore clipboard Off")
                }
            ),
            |this, cx| {
                this.field_group(
                    &[
                        "hotkey.double_tap_threshold",
                        "native_fast_paste",
                        "restore_clipboard",
                    ],
                    cx,
                )
            },
            cx,
        );

        v_flex()
            .w_full()
            .min_w_0()
            .gap_4()
            .child(trigger_card)
            .child(input_card)
            .child(shortcuts_advanced)
    }

    // -------------------------------------------------------------------------
    // 7. General & Connection (通用与连接)
    // -------------------------------------------------------------------------
    fn general_page(&self, cx: &mut Context<Self>) -> Div {
        let mut connection_card = titled_card(
            self.text("语音服务与网络连接", "Speech Service & Network"),
            Some(self.text(
                "配置 DashScope API Key 与网络代理，并可随时测试模型连通性。",
                "Configure your DashScope API key, proxy, and verify model availability.",
            )),
            cx,
        )
        .child(self.field_row("api_key", true, cx))
        .child(
            h_flex()
                .gap_2()
                .flex_wrap()
                .child(
                    Button::new("check-models")
                        .small()
                        .primary()
                        .label(self.text(
                            if self.model_checking {
                                "验证中…"
                            } else {
                                "验证 API Key / 模型可用性"
                            },
                            if self.model_checking {
                                "Checking…"
                            } else {
                                "Check API key / models"
                            },
                        ))
                        .disabled(self.model_checking || get(self.config(), "_api_key_set") != true)
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.model_checking = true;
                            this.action("checkDashScopeModels", json!({}), cx);
                            cx.notify();
                        })),
                )
                .child(
                    Button::new("obtain-api-key")
                        .small()
                        .label(self.text("获取 API Key", "Get API key"))
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.action(
                                "openExternal",
                                json!({"url": "https://dashscope.console.aliyun.com/apiKey"}),
                                cx,
                            );
                        })),
                ),
        );

        if let Some(results) = self.model_results.as_array().filter(|r| !r.is_empty()) {
            let mut list = v_flex()
                .gap_1()
                .p_3()
                .rounded_md()
                .bg(cx.theme().secondary.opacity(0.45))
                .border_1()
                .border_color(cx.theme().border);
            for result in results {
                let name = result["display_name"]
                    .as_str()
                    .or(result["model"].as_str())
                    .unwrap_or("Model");
                let message = format!(
                    "{} · {} ms{}",
                    result["status"].as_str().unwrap_or("unknown"),
                    result["latency_ms"],
                    result["error"]
                        .as_str()
                        .map(|error| format!(" · {error}"))
                        .unwrap_or_default()
                );
                list = list.child(readout(name, message, cx));
            }
            connection_card = connection_card.child(list);
        }

        connection_card = connection_card.child(self.field_row("network.proxy_url", false, cx));

        let mut appearance_card = titled_card(
            self.text("外观与交互偏好", "Appearance & Experience"),
            Some(self.text(
                "自定义悬浮胶囊屏幕位置、界面语言与更新通道。",
                "Customize floating capsule placement, interface language, and update channel.",
            )),
            cx,
        )
        .child(self.field_group(
            &["ui.capsule_position", "ui.language", "update_channel"],
            cx,
        ));

        if get(self.config(), "ui.capsule_position") == "custom" {
            appearance_card = appearance_card.child(
                h_flex().child(
                    Button::new("reset-capsule-position")
                        .xsmall()
                        .label(self.text("重置胶囊拖拽位置", "Reset dragged position"))
                        .on_click(cx.listener(|this, _, window, cx| {
                            this.apply_form_patch(
                                json!({
                                    "ui": {
                                        "capsule_position": "bottom",
                                        "capsule_custom_x": null,
                                        "capsule_custom_y": null,
                                    }
                                }),
                                window,
                                cx,
                            );
                        })),
                ),
            );
        }

        let mut maintenance_card = titled_card(
            self.text("应用与维护", "Application & Maintenance"),
            None::<&'static str>,
            cx,
        )
        .child(readout(
            self.text("版本", "Version"),
            self.snapshot["version"].as_str().unwrap_or("—"),
            cx,
        ))
        .child(self.field_row("ui.advanced_settings", false, cx));

        if get(self.config(), "ui.onboarding_skipped") == true {
            maintenance_card = maintenance_card.child(banner(
                self.text(
                    "首次引导有未完成项，可点击下方重新运行首次设置。",
                    "Initial setup has unfinished items. Run setup again below anytime.",
                ),
                Tone::Warning,
                cx,
            ));
        }

        let mut maint_actions = h_flex().gap_2().flex_wrap().child(
            Button::new("open-config")
                .small()
                .label(self.text("打开配置文件", "Open configuration file"))
                .on_click(cx.listener(|this, _, _, cx| {
                    this.action("openConfigFile", json!({}), cx);
                })),
        );
        if self.rerun_confirm {
            maint_actions = maint_actions
                .child(
                    Button::new("rerun-confirm")
                        .small()
                        .primary()
                        .label(self.text("确认重新设置", "Confirm setup"))
                        .on_click(cx.listener(|this, _, window, cx| {
                            this.rerun_confirm = false;
                            this.onboarding_step = 0;
                            this.set_config("ui.onboarding_completed", json!(false), window, cx);
                            this.set_config("ui.onboarding_skipped", json!(false), window, cx);
                        })),
                )
                .child(
                    Button::new("rerun-cancel")
                        .small()
                        .label(self.text("取消", "Cancel"))
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.rerun_confirm = false;
                            cx.notify();
                        })),
                );
        } else {
            maint_actions = maint_actions.child(
                Button::new("rerun-setup")
                    .small()
                    .label(self.text("重新运行首次设置", "Run setup again"))
                    .on_click(cx.listener(|this, _, _, cx| {
                        this.rerun_confirm = true;
                        cx.notify();
                    })),
            );
        }
        maintenance_card = maintenance_card.child(maint_actions);

        v_flex()
            .w_full()
            .min_w_0()
            .gap_4()
            .child(connection_card)
            .child(appearance_card)
            .child(maintenance_card)
    }

    // -------------------------------------------------------------------------
    // Onboarding: 3-Step Focused Wizard (首次引导)
    // -------------------------------------------------------------------------
    fn readiness(&self, key: &str) -> bool {
        self.array("environment_checks")
            .iter()
            .any(|check| check["key"] == key && check["status"] == "ok")
    }

    fn onboarding(&self, cx: &mut Context<Self>) -> Div {
        let step = self.onboarding_step.min(2);
        let step_defs = [
            (0usize, "1. 连接服务", "1. Connect Service"),
            (1usize, "2. 系统权限", "2. Permissions"),
            (2usize, "3. 低声体验", "3. Try Whisper"),
        ];

        let stepper = h_flex()
            .gap_2()
            .flex_wrap()
            .children(step_defs.into_iter().map(|(idx, zh, en)| {
                Button::new(("onboarding-step", idx))
                    .small()
                    .ghost()
                    .label(self.text(zh, en))
                    .selected(step == idx)
                    .on_click(cx.listener(move |this, _, _, cx| {
                        this.onboarding_step = idx;
                        cx.notify();
                    }))
            }));

        let step_card = match step {
            0 => titled_card(
                self.text("步骤 1 · 连接语音服务", "Step 1 · Connect Speech Service"),
                Some(self.text(
                    "输入 DashScope API Key 开启实时识别与智能润色，密钥仅保存在本机。",
                    "Enter your DashScope API key for realtime ASR and polishing. Stored locally.",
                )),
                cx,
            )
            .child(self.field_row("api_key", false, cx))
            .child(
                h_flex()
                    .gap_2()
                    .flex_wrap()
                    .child(
                        Button::new("onboarding-check-models")
                            .small()
                            .primary()
                            .label(self.text(
                                if self.model_checking {
                                    "验证中…"
                                } else {
                                    "验证连接"
                                },
                                if self.model_checking {
                                    "Checking…"
                                } else {
                                    "Verify connection"
                                },
                            ))
                            .disabled(
                                self.model_checking || get(self.config(), "_api_key_set") != true,
                            )
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.model_checking = true;
                                this.action("checkDashScopeModels", json!({}), cx);
                                cx.notify();
                            })),
                    )
                    .child(
                        Button::new("onboarding-api-console")
                            .small()
                            .label(self.text("获取 API Key", "Get API key"))
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.action(
                                    "openExternal",
                                    json!({"url": "https://dashscope.console.aliyun.com/apiKey"}),
                                    cx,
                                );
                            })),
                    ),
            ),
            1 => {
                let mut permissions = titled_card(
                    self.text(
                        "步骤 2 · 授予 macOS 系统权限",
                        "Step 2 · Grant macOS Permissions",
                    ),
                    Some(self.text(
                        "仅用于低声拾音、全局 Fn 热键响应与自动粘贴识别结果。",
                        "Used only for microphone capture, global Fn hotkeys, and auto-paste.",
                    )),
                    cx,
                );
                for (key, zh, en) in [
                    ("api_key", "API Key", "API key"),
                    (
                        "microphone_permission",
                        "麦克风权限",
                        "Microphone permission",
                    ),
                    ("input_device", "输入设备", "Input device"),
                    ("accessibility", "辅助功能权限", "Accessibility permission"),
                    ("hotkey_listener", "快捷键监听", "Hotkey listener"),
                ] {
                    let ok = self.readiness(key);
                    permissions = permissions.child(
                        h_flex()
                            .w_full()
                            .justify_between()
                            .items_center()
                            .py_1p5()
                            .border_b_1()
                            .border_color(cx.theme().border.opacity(0.6))
                            .child(
                                h_flex()
                                    .items_center()
                                    .gap_2()
                                    .child(status_dot(
                                        if ok { Tone::Success } else { Tone::Warning },
                                        cx,
                                    ))
                                    .child(div().text_sm().child(self.text(zh, en))),
                            )
                            .child(badge(
                                if ok {
                                    self.text("已就绪", "Ready")
                                } else {
                                    self.text("需要处理", "Needs attention")
                                },
                                if ok { Tone::Success } else { Tone::Warning },
                                cx,
                            )),
                    );
                }
                permissions.child(
                    h_flex()
                        .gap_2()
                        .flex_wrap()
                        .pt_1()
                        .child(
                            Button::new("onboarding-accessibility")
                                .small()
                                .primary()
                                .label(self.text("打开辅助功能设置", "Open Accessibility settings"))
                                .on_click(cx.listener(|this, _, _, cx| {
                                    this.action("openAccessibilitySettings", json!({}), cx);
                                })),
                        )
                        .child(
                            Button::new("onboarding-microphone")
                                .small()
                                .label(self.text("打开麦克风设置", "Open Microphone settings"))
                                .on_click(cx.listener(|this, _, _, cx| {
                                    this.action("openMicrophoneSettings", json!({}), cx);
                                })),
                        )
                        .child(
                            Button::new("onboarding-refresh")
                                .small()
                                .icon(IconName::RefreshCw)
                                .label(self.text("重新检查", "Check again"))
                                .on_click(cx.listener(|this, _, _, cx| {
                                    this.action("refreshEnvironment", json!({}), cx);
                                    this.action("refreshDevices", json!({}), cx);
                                })),
                        ),
                )
            }
            _ => titled_card(
                self.text(
                    "步骤 3 · 体验办公室低声输入",
                    "Step 3 · Try Low-Voice Input",
                ),
                Some(self.text(
                    "选择麦克风、启用低声预设并完成一次试音，确认轻声说话也能被清晰识别。",
                    "Select a microphone, enable the whisper preset, and record a quick test.",
                )),
                cx,
            )
            .child(self.field_row("audio.input_device", false, cx))
            .child(
                h_flex()
                    .gap_2()
                    .flex_wrap()
                    .child(
                        Button::new("onboarding-whisper-preset")
                            .small()
                            .label(self.text("采用办公室低声预设", "Use whisper preset"))
                            .selected(self.active_audio_preset() == Some("whisper"))
                            .disabled(self.audio_busy())
                            .on_click(cx.listener(|this, _, window, cx| {
                                this.preset("whisper", window, cx)
                            })),
                    )
                    .child(
                        Button::new("onboarding-calibrate")
                            .small()
                            .label(self.text("8 秒低声智能校准", "8s whisper calibration"))
                            .disabled(self.audio_busy())
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.calibration.open = true;
                                cx.notify();
                            })),
                    ),
            )
            .child(self.mic_card_body(cx)),
        };

        let missing = [
            get(self.config(), "_api_key_set") == true,
            !self.array("devices").is_empty() && self.readiness("input_device"),
            self.readiness("accessibility"),
            self.readiness("hotkey_listener"),
            self.mic == MicState::Done,
        ]
        .into_iter()
        .filter(|ready| !ready)
        .count();
        let can_finish = missing == 0;

        let mut nav_buttons = h_flex().gap_2().flex_wrap().items_center();
        if !can_finish {
            nav_buttons = nav_buttons.child(
                note(
                    if self.english() {
                        format!("{missing} item(s) left")
                    } else {
                        format!("还差 {missing} 项")
                    },
                    cx,
                )
                .id("onboarding-missing"),
            );
        }
        if step > 0 {
            nav_buttons = nav_buttons.child(
                Button::new("onboarding-prev")
                    .small()
                    .label(self.text("上一步", "Back"))
                    .on_click(cx.listener(|this, _, _, cx| {
                        this.onboarding_step = this.onboarding_step.saturating_sub(1);
                        cx.notify();
                    })),
            );
        }
        if step < 2 {
            nav_buttons = nav_buttons.child(
                Button::new("onboarding-next")
                    .small()
                    .primary()
                    .label(self.text("下一步", "Continue"))
                    .on_click(cx.listener(|this, _, _, cx| {
                        this.onboarding_step = (this.onboarding_step + 1).min(2);
                        cx.notify();
                    })),
            );
        }
        nav_buttons = nav_buttons.child(
            Button::new("finish-onboarding")
                .small()
                .primary()
                .label(self.text("完成设置", "Finish setup"))
                .disabled(!can_finish)
                .on_click(cx.listener(|this, _, window, cx| {
                    this.set_config("ui.onboarding_completed", json!(true), window, cx);
                    this.set_config("ui.onboarding_skipped", json!(false), window, cx);
                })),
        );

        v_flex()
            .w_full()
            .max_w(px(680.))
            .mx_auto()
            .gap_4()
            .child(
                h_flex().items_center().gap_3().child(brand_mark()).child(
                    v_flex()
                        .gap_0p5()
                        .child(
                            div()
                                .text_xl()
                                .font_weight(FontWeight::SEMIBOLD)
                                .child(self.text("欢迎使用 Vocal More", "Welcome to Vocal More")),
                        )
                        .child(
                            div()
                                .text_xs()
                                .text_color(cx.theme().muted_foreground)
                                .child(self.text(
                                    "三步完成配置，马上开始安静、流畅的桌面语音输入。",
                                    "Three quick steps to start quiet, effortless voice input.",
                                )),
                        ),
                ),
            )
            .child(stepper)
            .child(step_card)
            .child(
                h_flex()
                    .w_full()
                    .justify_between()
                    .items_center()
                    .gap_2()
                    .flex_wrap()
                    .pt_2()
                    .child(
                        Button::new("skip-onboarding")
                            .small()
                            .ghost()
                            .label(self.text("稍后设置", "Set up later"))
                            .on_click(cx.listener(|this, _, window, cx| {
                                this.stop_mic();
                                this.set_config("ui.onboarding_completed", json!(true), window, cx);
                                this.set_config("ui.onboarding_skipped", json!(true), window, cx);
                            })),
                    )
                    .child(nav_buttons),
            )
    }

    // -------------------------------------------------------------------------
    // Calibration Sheet (低声输入校准)
    // -------------------------------------------------------------------------
    fn calibration_sheet(&self, cx: &mut Context<Self>) -> Div {
        let mut contents = titled_card(
            self.text("8 秒低声智能校准", "8-Second Whisper Calibration"),
            Some(self.text(
                "先保持房间安静 3 秒，再用日常轻声朗读：“今天的工作安排已经准备好了。”",
                "Stay quiet for 3 seconds, then whisper: “My work plan for today is ready.”",
            )),
            cx,
        )
        .id("calibration-sheet-card")
        .w(px(if self.narrow { 520. } else { 540. }))
        .max_w_full()
        .max_h(px(440.))
        .overflow_y_scroll()
        .shadow_lg();

        if let Some(phase) = self.calibration.phase {
            contents = contents
                .child(
                    h_flex()
                        .justify_between()
                        .items_center()
                        .child(div().text_sm().font_weight(FontWeight::SEMIBOLD).child(
                            match phase {
                                calibration::Phase::Quiet => self.text(
                                    "步骤 1/2：请保持房间安静（3 秒）",
                                    "Step 1/2: Stay quiet (3 seconds)",
                                ),
                                calibration::Phase::Whisper => self.text(
                                    "步骤 2/2：请轻声朗读参考句（4.5 秒）",
                                    "Step 2/2: Whisper the phrase (4.5 seconds)",
                                ),
                            },
                        ))
                        .child(
                            div()
                                .text_xs()
                                .font_family(cx.theme().mono_font_family.clone())
                                .text_color(cx.theme().muted_foreground)
                                .child(format!("{:.1} dBFS", calibration::dbfs(self.mic_level))),
                        ),
                )
                .child(
                    Progress::new("calibration-level").value(
                        calibration::waveform(
                            self.mic_level,
                            get(self.config(), "audio.waveform_ceiling_dbfs")
                                .as_f64()
                                .unwrap_or(-6.),
                        ) * 100.,
                    ),
                );
        } else if let Some(result) = &self.calibration.result {
            match result {
                Ok(result) => {
                    let mut diff = v_flex()
                        .gap_1()
                        .p_3()
                        .rounded_md()
                        .bg(cx.theme().secondary.opacity(0.45))
                        .border_1()
                        .border_color(cx.theme().border)
                        .child(readout(
                            self.text("环境底噪", "Noise floor"),
                            format!("{:.1} dBFS", result.noise),
                            cx,
                        ))
                        .child(readout(
                            self.text("低声电平", "Whisper level"),
                            format!("{:.1} dBFS", result.whisper),
                            cx,
                        ));
                    for (key, value) in calibration::changes(result, self.config()) {
                        diff = diff.child(readout(
                            schema::FIELDS
                                .iter()
                                .find(|field| field.key == key)
                                .map(|field| field.title(self.english()))
                                .unwrap_or(key),
                            schema::display_value(&value),
                            cx,
                        ));
                    }
                    contents = contents
                        .child(banner(
                            self.text(
                                "测量完成，建议应用以下低声参数组合：",
                                "Measurement complete. Recommended low-voice settings:",
                            ),
                            Tone::Success,
                            cx,
                        ))
                        .child(diff);
                    if result.clamped {
                        contents = contents.child(banner(
                            self.text(
                                "建议增益已限制在安全范围 1–50。",
                                "Recommended gain was clamped to the safe range 1–50.",
                            ),
                            Tone::Warning,
                            cx,
                        ));
                    }
                    contents = contents.child(
                        Button::new("apply-calibration")
                            .small()
                            .primary()
                            .label(self.text("应用上述建议", "Apply recommendation"))
                            .on_click(cx.listener(|this, _, window, cx| {
                                if let Some(Ok(result)) = &this.calibration.result {
                                    let changes = calibration::changes(result, this.config());
                                    for (key, value) in changes {
                                        this.set_config(key, value, window, cx);
                                    }
                                }
                                this.close_calibration();
                                this.sync_controls(window, cx, true);
                                cx.notify();
                            })),
                    );
                }
                Err(reason) => {
                    contents = contents.child(banner(
                        if *reason == "low-snr" {
                            self.text(
                                "低声与背景噪声差异不足，请靠近麦克风或降低环境噪声后重测。",
                                "Whisper level is too close to room noise. Move closer or reduce noise.",
                            )
                        } else {
                            self.text(
                                "有效样本不足，请重新测量。",
                                "Not enough valid samples. Measure again.",
                            )
                        },
                        Tone::Warning,
                        cx,
                    ));
                }
            }
        }
        if let Some(error) = &self.mic_error {
            contents = contents.child(banner(error.clone(), Tone::Danger, cx));
        }

        let mut footer = h_flex().justify_end().gap_2().pt_2().child(
            Button::new("close-calibration")
                .small()
                .label(self.text("关闭", "Close"))
                .on_click(cx.listener(|this, _, _, cx| {
                    this.close_calibration();
                    cx.notify();
                })),
        );
        if self.calibration.phase.is_none() {
            let measured = matches!(self.calibration.result, Some(Ok(_)));
            footer = footer.child(
                Button::new("start-calibration")
                    .small()
                    .when(!measured, |button| button.primary())
                    .label(if measured {
                        self.text("重新测量", "Measure again")
                    } else {
                        self.text("开始测量", "Start measurement")
                    })
                    .disabled(self.audio_busy())
                    .on_click(cx.listener(|this, _, _, cx| this.start_calibration(cx))),
            );
        }

        div()
            .absolute()
            .inset_0()
            .occlude()
            .p_4()
            .bg(cx.theme().background.opacity(0.88))
            .flex()
            .items_center()
            .justify_center()
            .child(contents.child(footer))
    }

    fn sidebar_item(&self, tab: Tab, cx: &mut Context<Self>) -> Div {
        let pending_dict = if tab == Tab::Dictionary {
            self.pending_learning_count()
        } else {
            0
        };
        let home_attention = if tab == Tab::Home {
            self.attention_count()
        } else {
            0
        };
        let title = tab.title(self.english());
        let active = self.tab == tab;
        let mut content = h_flex()
            .w_full()
            .min_w_0()
            .items_center()
            .justify_between()
            .gap_2()
            .child(
                h_flex()
                    .flex_1()
                    .min_w_0()
                    .items_center()
                    .justify_start()
                    .gap_2()
                    .child(
                        div()
                            .text_color(if active {
                                cx.theme().sidebar_accent_foreground
                            } else {
                                cx.theme().muted_foreground
                            })
                            .child(Icon::new(tab_icon(tab)).small()),
                    )
                    .child(
                        div()
                            .flex_1()
                            .min_w_0()
                            .text_xs()
                            .font_weight(if active {
                                FontWeight::SEMIBOLD
                            } else {
                                FontWeight::NORMAL
                            })
                            .child(title),
                    ),
            );
        if pending_dict > 0 {
            content = content.child(badge(format!("{pending_dict}"), Tone::Primary, cx));
        } else if home_attention > 0 {
            content = content.child(status_dot(Tone::Warning, cx));
        }
        div().w_full().child(
            Button::new(tab.id())
                .small()
                .ghost()
                .w_full()
                .rounded(cx.theme().radius)
                .accessibility_label(title)
                .selected(active)
                .text_color(cx.theme().sidebar_foreground)
                .when(active, |button| {
                    button
                        .bg(cx.theme().sidebar_accent)
                        .text_color(cx.theme().sidebar_accent_foreground)
                })
                .on_click(cx.listener(move |this, _, window, cx| this.switch_tab(tab, window, cx)))
                .child(content),
        )
    }
}

impl Render for Settings {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        self.recover_rejected_action(window, cx);
        self.narrow = window.viewport_size().width < px(820.);
        let mut root = h_flex()
            .relative()
            .size_full()
            .whitespace_normal()
            .font_family(cx.theme().font_family.clone())
            .text_color(cx.theme().foreground)
            .bg(cx.theme().background);

        if get(self.config(), "ui.onboarding_completed") != true {
            root = root.child(
                div()
                    .id("onboarding-page")
                    .test_support()
                    .size_full()
                    .bg(cx.theme().background)
                    .overflow_y_scroll()
                    .p_6()
                    .child(self.onboarding(cx)),
            );
        } else {
            let sidebar_w = if self.narrow { 156. } else { 196. };
            let mut sidebar = v_flex()
                .w(px(sidebar_w))
                .h_full()
                .flex_shrink_0()
                .px_2p5()
                .py_4()
                .gap_3()
                .bg(cx.theme().sidebar)
                .border_r_1()
                .border_color(cx.theme().sidebar_border)
                .child(
                    h_flex()
                        .items_center()
                        .gap_2()
                        .px_2()
                        .pb_1()
                        .child(brand_mark())
                        .child(
                            div()
                                .text_sm()
                                .font_weight(FontWeight::SEMIBOLD)
                                .child("Vocal More"),
                        ),
                );

            let mut workspace_group = v_flex().gap_0p5().child(
                div()
                    .px_2()
                    .pb_1()
                    .text_xs()
                    .font_weight(FontWeight::MEDIUM)
                    .text_color(cx.theme().muted_foreground)
                    .child(self.text("工作区", "Workspace")),
            );
            for tab in Tab::WORKSPACE {
                workspace_group = workspace_group.child(self.sidebar_item(tab, cx));
            }

            let mut settings_group = v_flex().gap_0p5().child(
                div()
                    .px_2()
                    .pb_1()
                    .text_xs()
                    .font_weight(FontWeight::MEDIUM)
                    .text_color(cx.theme().muted_foreground)
                    .child(self.text("设置", "Settings")),
            );
            for tab in Tab::SETTINGS {
                settings_group = settings_group.child(self.sidebar_item(tab, cx));
            }

            let ready = self.attention_count() == 0;
            sidebar = sidebar
                .child(workspace_group)
                .child(settings_group)
                .child(div().flex_1())
                .child(
                    h_flex()
                        .id("readiness-summary")
                        .px_2()
                        .pt_2()
                        .border_t_1()
                        .border_color(cx.theme().sidebar_border)
                        .justify_between()
                        .items_center()
                        .child(
                            h_flex()
                                .items_center()
                                .gap_1p5()
                                .child(status_dot(
                                    if ready { Tone::Success } else { Tone::Warning },
                                    cx,
                                ))
                                .child(
                                    div()
                                        .text_xs()
                                        .text_color(cx.theme().muted_foreground)
                                        .child(if ready {
                                            self.text("就绪", "Ready")
                                        } else {
                                            self.text("待处理", "Setup")
                                        }),
                                ),
                        )
                        .child(
                            div()
                                .text_xs()
                                .font_family(cx.theme().mono_font_family.clone())
                                .text_color(cx.theme().muted_foreground)
                                .child(format!(
                                    "v{}",
                                    self.snapshot["version"].as_str().unwrap_or("—")
                                )),
                        ),
                );

            let page = match self.tab {
                Tab::Home => self.home_page(cx),
                Tab::History => self.history_page(cx),
                Tab::Dictionary => self.dictionary_page(cx),
                Tab::Audio => self.audio_page(cx),
                Tab::Ai => self.ai_page(cx),
                Tab::Shortcuts => self.shortcuts_page(cx),
                Tab::General => self.general_page(cx),
            };

            root = root.child(sidebar).child(
                v_flex()
                    .id("settings-page")
                    .test_support()
                    .flex_1()
                    .min_w_0()
                    .h_full()
                    .bg(cx.theme().background)
                    .overflow_y_scroll()
                    .p_5()
                    .gap_4()
                    .child(
                        v_flex()
                            .gap_0p5()
                            .child(
                                div()
                                    .text_xl()
                                    .font_weight(FontWeight::SEMIBOLD)
                                    .child(self.tab.title(self.english())),
                            )
                            .child(
                                div()
                                    .text_xs()
                                    .text_color(cx.theme().muted_foreground)
                                    .child(self.tab.subtitle(self.english())),
                            ),
                    )
                    .child(page.max_w(px(760.))),
            );
        }

        if let Some(error) = &self.error {
            root = root.child(
                h_flex()
                    .absolute()
                    .bottom_3()
                    .left_3()
                    .right_3()
                    .occlude()
                    .p_3()
                    .gap_3()
                    .rounded(cx.theme().radius_lg)
                    .bg(cx.theme().danger)
                    .text_color(cx.theme().danger_foreground)
                    .child(div().flex_1().text_sm().child(error.clone()))
                    .child(
                        Button::new("dismiss-settings-error")
                            .small()
                            .label(self.text("关闭", "Dismiss"))
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.error = None;
                                cx.notify();
                            })),
                    ),
            );
        }
        if self.calibration.open {
            root = root.child(self.calibration_sheet(cx));
        }
        root
    }
}
