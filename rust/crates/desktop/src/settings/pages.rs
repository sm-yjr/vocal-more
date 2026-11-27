// SPDX-License-Identifier: GPL-3.0-only
//! Native Tahoe settings views, backed by the existing desktop settings contracts.
use super::{
    Control, MicState, Settings, calibration,
    components::{
        Tone, badge, banner, brand_mark, bytes, card, disclosure_header, format_db_from_linear,
        keycap, literal_markdown, localized_mode, localized_status, money, number,
        readable_timestamp, readout, row_divider, section_heading, status_dot, summary_strip,
        titled_card,
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

fn gradient(top: u32, bottom: u32) -> Background {
    linear_gradient(
        145.,
        linear_color_stop(rgb(top), 0.),
        linear_color_stop(rgb(bottom), 1.),
    )
}

fn app_icon(id: &str, edge: f32) -> Img {
    img(SharedString::from(format!("icons/{id}.png")))
        .size(px(edge))
        .rounded(px(edge / 3.5))
        .flex_shrink_0()
}

fn tile(id: impl Into<ElementId>, label: &'static str, selected: bool, cx: &App) -> Button {
    Button::new(id)
        .ghost()
        .w_full()
        .rounded(px(11.))
        .selected(selected)
        .border_2()
        .border_color(if selected {
            rgb(0x007aff).into()
        } else {
            cx.theme().border
        })
        .accessibility_label(label)
}

fn tile_caption(label: &'static str, selected: bool, cx: &App) -> Div {
    div()
        .text_size(px(11.5))
        .text_center()
        .text_color(if selected {
            cx.theme().foreground
        } else {
            cx.theme().muted_foreground
        })
        .font_weight(if selected {
            FontWeight::SEMIBOLD
        } else {
            FontWeight::NORMAL
        })
        .child(label)
}

fn waveform(heights: &[u8], width: f32) -> Div {
    waveform_with_color(heights, width, rgb(0xffffff).into())
}

fn waveform_with_color(heights: &[u8], width: f32, color: Hsla) -> Div {
    h_flex()
        .h(px(24.))
        .items_center()
        .gap(px(width + 1.))
        .children(heights.iter().map(|&height| {
            div()
                .w(px(width))
                .h(px(f32::from(height)))
                .rounded_full()
                .bg(color)
        }))
}

fn mini_window(scheme: &str) -> Div {
    div()
        .w(px(46.))
        .h(px(35.))
        .rounded(px(6.))
        .shadow_sm()
        .border_1()
        .border_color(rgba(0xffffff8c))
        .bg(match scheme {
            "light" => rgb(0xeaf1ff).into(),
            "dark" => rgb(0x252838).into(),
            _ => linear_gradient(
                115.,
                linear_color_stop(rgb(0xeaf1ff), 0.49),
                linear_color_stop(rgb(0x252838), 0.51),
            ),
        })
        .child(
            h_flex().gap(px(2.)).p_1().children(
                [0xff5f57, 0xfabc32, 0x28c840]
                    .map(|color| div().size(px(3.)).rounded_full().bg(rgb(color))),
            ),
        )
        .child(
            v_flex()
                .ml(px(14.))
                .mr_1()
                .gap_px()
                .children([1, 2].map(|_| {
                    div()
                        .w_full()
                        .h(px(8.))
                        .rounded(px(2.))
                        .bg(rgba(0x8b9bb67a))
                })),
        )
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
        .child(section_heading(title, cx))
        .child(content)
}

/// Wrapping horizontal action row used by sibling submodules such as `scenes.rs`.
pub(super) fn actions() -> Div {
    h_flex().gap_2().flex_wrap().items_center()
}

/// Muted helper note used by sibling submodules such as `scenes.rs`.
pub(super) fn note(text: impl Into<SharedString>, cx: &App) -> Div {
    div()
        .text_size(px(11.5))
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
        let full_width = matches!(field.kind, Kind::Text | Kind::List);
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
                    .when(full_width, |input| input.w_full())
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
                let track_w = if self.narrow { 153. } else { 177. };
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
                            .child(if key == "audio.gain" {
                                format!("{value:+.0} dB · {:.1}×", 10_f32.powf(value / 20.))
                            } else if value.fract().abs() < 0.001 {
                                format!("{value:.0} {unit}")
                            } else {
                                format!("{value:.2} {unit}")
                            }),
                    )
                    .into_any_element()
            }
        };
        let hint = self.field_hint(key, field);
        let stack = (self.narrow && !matches!(field.kind, Kind::Toggle))
            || matches!(field.kind, Kind::Secret | Kind::Text | Kind::List);
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
                    .child(
                        div()
                            .text_size(px(13.5))
                            .font_weight(FontWeight::SEMIBOLD)
                            .child(title),
                    )
                    .when(!hint.is_empty(), |col| {
                        col.child(
                            div()
                                .text_size(px(11.5))
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
            .child(
                div()
                    .flex_shrink_0()
                    .when(full_width, |container| container.w_full())
                    .child(element),
            )
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
                "需先开启“自动粘贴”才会生效。",
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
    // Voice engine and runtime readiness
    // -------------------------------------------------------------------------
    fn engine_page(&self, cx: &mut Context<Self>) -> Div {
        let input = &self.snapshot["audio_input_status"];
        let native = self.native_backend_label(&input["native_backend"]);
        let hero = card(cx).child(
            h_flex()
                .items_center()
                .gap_2p5()
                .flex_wrap()
                .child(self.active_icon(40.))
                .child(
                    v_flex()
                        .flex_1()
                        .min_w_0()
                        .gap_0p5()
                        .child(
                            div()
                                .text_sm()
                                .font_weight(FontWeight::SEMIBOLD)
                                .child(self.text("Vocal More 语音引擎", "Vocal More Engine")),
                        )
                        .child(
                            div()
                                .id("readiness-summary")
                                .child(note(format!("{} · {native}", self.engine_state()), cx)),
                        ),
                )
                .child(
                    Button::new("home-refresh-env")
                        .small()
                        .rounded(px(20.))
                        .label(self.text("刷新状态", "Refresh status"))
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.action("refreshEnvironment", json!({}), cx);
                            this.action("refreshDevices", json!({}), cx);
                        })),
                ),
        );
        let device = input["device_name"]
            .as_str()
            .filter(|name| !name.is_empty())
            .unwrap_or(self.text("未检测到设备", "No input detected"));
        let source_rate = input["source_sample_rate_hz"].as_f64();
        let rate = source_rate
            .or_else(|| input["output_sample_rate_hz"].as_f64())
            .map(|rate| {
                format!(
                    " · {} {rate:.0} Hz",
                    if source_rate.is_some() {
                        self.text("采集", "Input")
                    } else {
                        self.text("输出", "Output")
                    }
                )
            })
            .unwrap_or_default();
        let channels = input["capture_channels"]
            .as_u64()
            .map(|count| format!(" · {count} {}", self.text("声道", "ch")))
            .unwrap_or_default();
        let recording = matches!(self.mic, MicState::Starting | MicState::Recording);
        let quick_input = card(cx)
            .child(
                h_flex()
                    .items_center()
                    .gap_2p5()
                    .flex_wrap()
                    .child(
                        v_flex()
                            .flex_1()
                            .min_w_0()
                            .gap_0p5()
                            .child(
                                div()
                                    .text_sm()
                                    .font_weight(FontWeight::SEMIBOLD)
                                    .child(self.text("当前输入设备", "Current input device")),
                            )
                            .child(note(format!("{device}{channels}{rate}"), cx)),
                    )
                    .child(
                        Button::new("home-goto-audio")
                            .small()
                            .rounded(px(20.))
                            .icon(IconName::Info)
                            .accessibility_label(self.text("音频设置", "Audio Settings"))
                            .on_click(cx.listener(|this, _, window, cx| {
                                this.switch_tab(Tab::Audio, window, cx)
                            })),
                    )
                    .when(
                        !recording && !self.mic_playable && self.mic_error.is_none(),
                        |row| row.child(self.mic_start_button(cx)),
                    ),
            )
            .when(
                recording || self.mic_playable || self.mic_error.is_some(),
                |card| card.child(self.mic_card_body(cx)),
            );

        let mut status = card(cx).gap_0().py_1();
        for (key, zh, en) in [
            (
                "microphone_permission",
                "麦克风权限",
                "Microphone permission",
            ),
            ("accessibility", "辅助功能权限", "Accessibility permission"),
            (
                "hotkey_listener",
                "全局快捷键监听",
                "Global hotkey listener",
            ),
            ("input_device", "输入设备可用性", "Input availability"),
        ] {
            let state = self
                .array("environment_checks")
                .iter()
                .find(|check| check["key"] == key);
            let ok = state.is_some_and(|check| check["status"] == "ok");
            let message = if ok && matches!(key, "microphone_permission" | "accessibility") {
                self.text("已授权", "Authorized")
            } else if ok {
                self.text("就绪", "Ready")
            } else if state.is_some_and(|check| check["status"] == "error") {
                self.text("需要处理", "Needs attention")
            } else {
                self.text("尚未确认", "Unverified")
            };
            let value = h_flex()
                .gap_1p5()
                .items_center()
                .child(status_dot(
                    if ok { Tone::Success } else { Tone::Warning },
                    cx,
                ))
                .child(div().text_sm().child(message))
                .when(!ok && key == "microphone_permission", |row| {
                    row.child(
                        Button::new("home-fix-mic")
                            .xsmall()
                            .label(self.text("打开麦克风设置", "Microphone Settings"))
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.action("openMicrophoneSettings", json!({}), cx);
                            })),
                    )
                })
                .when(!ok && key == "accessibility", |row| {
                    row.child(
                        Button::new("home-fix-ax-accessibility")
                            .xsmall()
                            .label(self.text("打开辅助功能设置", "Accessibility Settings"))
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.action("openAccessibilitySettings", json!({}), cx);
                            })),
                    )
                });
            status = status
                .child(
                    h_flex()
                        .py_2p5()
                        .gap_2()
                        .justify_between()
                        .items_center()
                        .flex_wrap()
                        .child(
                            div()
                                .text_sm()
                                .font_weight(FontWeight::MEDIUM)
                                .child(self.text(zh, en)),
                        )
                        .child(value),
                )
                .child(row_divider(cx));
        }
        let echo = match input["echo_cancellation"].as_str() {
            Some("active") => self.text("回声消除已启用", "Echo cancellation active"),
            Some("ready") => self.text("回声消除就绪", "Echo cancellation ready"),
            _ => self.text("低延迟模式", "Low Latency"),
        };
        let gain = match input["gain_control"].as_str() {
            Some("apple_agc") => self.text("Apple 自动增益", "Apple automatic gain"),
            Some("software_fallback") => self.text("软件增益回退", "Software gain fallback"),
            _ => self.text("手动软件增益", "Manual software gain"),
        };
        let last = &input["last_session"];
        let last_value = if last.is_object() {
            let dropped = last["queue_dropped_blocks"]
                .as_u64()
                .map(|count| format!("{count} {} · ", self.text("丢块", "dropped blocks")))
                .unwrap_or_default();
            let verified = if last["start_verified"] == true {
                self.text("已验证 · ", "Verified · ")
            } else {
                ""
            };
            format!("{verified}{dropped}{native}")
        } else {
            self.text("暂无运行诊断", "No session diagnostics")
                .to_owned()
        };
        status = status
            .child(
                readout(
                    self.text("回声消除与自动增益", "Echo cancellation & gain"),
                    format!("{echo} · {gain}"),
                    cx,
                )
                .py_2(),
            )
            .child(row_divider(cx))
            .child(
                readout(
                    self.text("最近一次录音", "Latest recording"),
                    last_value,
                    cx,
                )
                .py_2(),
            );
        let onboarding_reminder = h_flex()
            .gap_2()
            .flex_wrap()
            .items_center()
            .child(banner(
                self.text(
                    "首次引导已跳过，可随时继续完成。",
                    "Initial setup was skipped; resume anytime.",
                ),
                Tone::Warning,
                cx,
            ))
            .child(
                Button::new("home-resume-onboarding")
                    .small()
                    .label(self.text("继续引导", "Resume Setup"))
                    .on_click(cx.listener(|this, _, window, cx| {
                        this.onboarding_step = 0;
                        this.apply_form_patch(
                            json!({"ui":{"onboarding_completed":false,"onboarding_skipped":false}}),
                            window,
                            cx,
                        );
                    })),
            );

        let active_scene = self.active_dictation_scene_name();
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
                        Button::new("home-open-audio")
                            .xsmall()
                            .ghost()
                            .label(self.text("详情", "Details"))
                            .on_click(cx.listener(|this, _, window, cx| {
                                this.switch_tab(Tab::Audio, window, cx)
                            })),
                    ),
            )
            .child(
                Button::new("home-calibrate")
                    .xsmall()
                    .label(self.text("低声校准", "Whisper Calibration"))
                    .disabled(self.audio_busy())
                    .on_click(cx.listener(|this, _, _, cx| {
                        this.calibration.open = true;
                        cx.notify();
                    })),
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
                        cx.listener(|this, _, window, cx| this.switch_tab(Tab::Polish, window, cx)),
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
            .child(v_flex().gap_2p5().child(hero).child(quick_input))
            .when(
                get(self.config(), "ui.onboarding_skipped") == true,
                |page| page.child(onboarding_reminder),
            )
            .child(titled(
                self.text("系统权限与运行状态", "Permissions & Runtime Status"),
                status,
                cx,
            ))
            .child(self.connection_card(cx))
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

        let total_label = if recordings
            .iter()
            .any(|record| record["billing"]["estimated"] == true)
        {
            self.text("累计费用（含估算）", "Total cost incl. estimates")
        } else {
            self.text("累计费用", "Total cost")
        };

        // Compact search + filter toolbar
        let toolbar = h_flex()
            .w_full()
            .min_w_0()
            .gap_2()
            .when(self.narrow, |row| row.flex_col().items_stretch())
            .child(div().flex_1().min_w_0().child(self.filter_input(
                "history-filter",
                &self.filter,
                self.text("搜索录音文本", "Search recording text"),
                cx,
            )))
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
                        .rounded(px(14.))
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
        let summary_bar = titled(
            self.text("费用与存储", "Billing & Storage"),
            card(cx)
                .gap_2()
                .child(summary_strip(
                    [
                        (total_label, money(total)),
                        (self.text("识别费用", "Recognition"), money(asr)),
                        (self.text("润色费用", "Polish"), money(polish)),
                    ],
                    true,
                    cx,
                ))
                .child(row_divider(cx))
                .child(
                    h_flex()
                        .items_center()
                        .justify_between()
                        .gap_2()
                        .flex_wrap()
                        .child(
                            v_flex()
                                .flex_1()
                                .min_w_0()
                                .gap_0p5()
                                .child(div().text_sm().font_weight(FontWeight::SEMIBOLD).child(
                                    self.text(
                                        "音频无损压缩（FLAC）",
                                        "Lossless audio compression (FLAC)",
                                    ),
                                ))
                                .child(note(
                                    format!(
                                        "{} {}/{} · {} {} · {} {}",
                                        self.text("已归档", "Archived"),
                                        number(&storage["compressed_count"]),
                                        number(&storage["recording_count"]),
                                        self.text("节省", "Saved"),
                                        bytes(&storage["bytes_saved"]),
                                        self.text("占用", "Stored"),
                                        bytes(&storage["stored_bytes"])
                                    ),
                                    cx,
                                )),
                        )
                        .child(
                            Button::new("compact-history")
                                .small()
                                .rounded(px(20.))
                                .label(if self.compacting {
                                    self.text("正在压缩…", "Compressing…")
                                } else {
                                    self.text("立即压缩", "Compress Now")
                                })
                                .disabled(
                                    self.compacting || number(&storage["recording_count"]) <= 3.,
                                )
                                .on_click(cx.listener(|this, _, _, cx| {
                                    this.action("compactRecordingHistory", json!({}), cx);
                                })),
                        ),
                ),
            cx,
        );

        let mut page = v_flex()
            .w_full()
            .min_w_0()
            .gap_2p5()
            .child(summary_bar)
            .child(toolbar)
            .child(section_heading(
                self.text("最近录音", "Recent Recordings"),
                cx,
            ));

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

            // Recording actions
            let retrying = status == "retrying";
            let no_text = record["transcript"].as_str().unwrap_or_default().is_empty();
            let play_id = id.clone();
            let retry_id = id.clone();
            let copy_id = id.clone();
            let delete_id = id.clone();
            let is_playing = self.playing.as_deref() == Some(&id);
            let is_copied = self.copied.as_deref() == Some(&id);

            let recording_actions = h_flex()
                .items_center()
                .gap_1()
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
                    .rounded(px(12.))
                    .size(px(24.))
                    .icon(if is_playing {
                        IconName::Square
                    } else {
                        IconName::Play
                    })
                    .accessibility_label(self.text(
                        if is_playing { "停止回放" } else { "回放" },
                        if is_playing { "Stop" } else { "Play" },
                    ))
                    .disabled(retrying)
                    .on_click(cx.listener(move |this, _, _, cx| {
                        let act = if is_playing {
                            "stopRecording"
                        } else {
                            "playRecording"
                        };
                        this.action(act, json!({"id": play_id}), cx);
                        cx.notify();
                    })),
                )
                .child(
                    Button::new(("copyTranscript", index))
                        .xsmall()
                        .rounded(px(12.))
                        .size(px(24.))
                        .icon(if is_copied {
                            IconName::Check
                        } else {
                            IconName::Copy
                        })
                        .accessibility_label(self.text(
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
                        .rounded(px(12.))
                        .size(px(24.))
                        .icon(IconName::RefreshCw)
                        .accessibility_label(self.text("重新识别", "Retry"))
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
                })
                .child(
                    Button::new(("delete-recording", index))
                        .xsmall()
                        .rounded(px(12.))
                        .size(px(24.))
                        .ghost()
                        .icon(IconName::Trash)
                        .accessibility_label(self.text("删除", "Delete"))
                        .disabled(retrying)
                        .on_click(cx.listener(move |this, _, window, cx| {
                            this.stage_delete(delete_id.clone(), window, cx);
                        })),
                );

            let duration = duration.max(0.) as u64;
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
                            .gap_1p5()
                            .flex_wrap()
                            .child(
                                div()
                                    .text_sm()
                                    .font_weight(FontWeight::SEMIBOLD)
                                    .child(timestamp),
                            )
                            .child(badge(
                                localized_mode(mode, self.english()),
                                Tone::Neutral,
                                cx,
                            ))
                            .child(badge(model.to_owned(), Tone::Neutral, cx))
                            .child(badge(
                                format!("{}:{:02}", duration / 60, duration % 60),
                                Tone::Neutral,
                                cx,
                            ))
                            .child(badge(status_label, status_tone, cx)),
                    )
                    .child(recording_actions),
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

            if record_cost > 0. {
                let qualifier = if record["billing"]["estimated"] == true {
                    self.text(" · 估算", " · Estimated")
                } else {
                    ""
                };
                item = item.child(note(
                    format!(
                        "{} {}{qualifier}",
                        self.text("费用", "Cost"),
                        money(record_cost)
                    ),
                    cx,
                ));
            }
            page = page.child(item);
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
        let dark = cx.theme().mode.is_dark();
        let presets = [
            (
                "whisper",
                "轻声办公",
                "Office Whisper",
                "+18 dB",
                220,
                (0xdff0f9, 0xddd7f6, 0x4b51a2),
                (0x343956, 0x3f3258, 0xbfc7ff),
                [3, 6, 7, 12, 19, 24, 16, 11, 7, 5, 3],
            ),
            (
                "normal",
                "普通说话",
                "Normal Voice",
                "+12 dB",
                200,
                (0xdaf1f1, 0xcbe5ee, 0x196e82),
                (0x244449, 0x27384d, 0x9be4e5),
                [4, 11, 17, 20, 12, 24, 17, 22, 16, 13, 8],
            ),
            (
                "noisy",
                "嘈杂环境",
                "Noisy Room",
                "+16 dB",
                280,
                (0xf5e6d9, 0xedd8e9, 0x914d63),
                (0x543b36, 0x49304e, 0xf4b4bf),
                [8, 16, 24, 15, 20, 12, 21, 23, 13, 21, 14],
            ),
        ];
        let preset_cards =
            presets
                .into_iter()
                .map(|(id, zh, en, gain, freq, light, night, bars)| {
                    let selected = active_preset == Some(id);
                    let title = self.text(zh, en);
                    let (top, bottom, ink) = if dark { night } else { light };
                    v_flex()
                        .flex_1()
                        .min_w_0()
                        .gap_1p5()
                        .items_center()
                        .child(
                            tile(id, title, selected, cx)
                                .h(px(if self.narrow { 68. } else { 91. }))
                                .bg(gradient(top, bottom))
                                .disabled(self.audio_busy())
                                .on_click(cx.listener(move |this, _, window, cx| {
                                    this.preset(id, window, cx)
                                }))
                                .child(
                                    v_flex()
                                        .items_center()
                                        .gap_1()
                                        .text_color(rgb(ink))
                                        .child(waveform_with_color(&bars, 2.3, rgb(ink).into()))
                                        .child(
                                            div()
                                                .text_size(px(if self.narrow { 10. } else { 11. }))
                                                .text_center()
                                                .font_weight(FontWeight::SEMIBOLD)
                                                .child(format!(
                                                    "{gain} · {freq} Hz{}",
                                                    self.text(" 高通", " HPF")
                                                )),
                                        ),
                                ),
                        )
                        .child(tile_caption(title, selected, cx))
                });
        let scene_section = titled(self.text("低声场景预设", "Low-Voice Presets"), card(cx)
            .child(h_flex().gap_2p5().items_stretch().children(preset_cards))
            .child(row_divider(cx))
            .child(h_flex().justify_between().items_center().flex_wrap().gap_2()
                .child(v_flex().flex_1().min_w_0().gap_1()
                    .child(div().text_sm().font_weight(FontWeight::SEMIBOLD).child(self.text("低语校准", "Whisper calibration")))
                    .child(note(self.text("通过 8 秒静默与轻声朗读，推荐增益与波形满幅电平。", "Use eight seconds of silence and soft speech to determine gain and waveform ceiling."), cx)))
                .child(Button::new("calibrate-whisper").small().primary().rounded(px(20.))
                    .label(self.text("开始校准", "Start Calibration"))
                    .disabled(self.audio_busy())
                    .on_click(cx.listener(|this, _, _, cx| { this.calibration.open = true; cx.notify(); })))), cx);

        // Microphone selection + Live Test integrated card
        let input_status = &self.snapshot["audio_input_status"];
        let mic_perm_denied = matches!(
            input_status["microphone_permission"].as_str(),
            Some("denied" | "restricted")
        );
        let mut mic_section = card(cx)
            .gap_2()
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
        mic_section = mic_section
            .child(row_divider(cx))
            .child(self.field_group(&["audio.capture_backend", "audio.gain_mode"], cx));

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

        let advanced_audio = self.disclosure(
            "audio",
            self.text("信号处理（DSP）", "Signal Processing (DSP)"),
            summary,
            |this, cx| {
                this.field_group(
                    &[
                        "audio.gain",
                        "audio.highpass_filter",
                        "audio.highpass_freq",
                        "audio.soft_limiter",
                        "audio.tail_padding_ms",
                        "audio.waveform_ceiling_dbfs",
                    ],
                    cx,
                )
            },
            cx,
        );
        let diagnostics =
            self.disclosure(
                "audio-diagnostics",
                self.text("运行状态与诊断", "Runtime Status & Diagnostics"),
                self.native_backend_label(&input_status["native_backend"]),
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
                    diag
                },
                cx,
            );

        v_flex()
            .w_full()
            .min_w_0()
            .gap_4()
            .child(titled(
                self.text("输入与引擎", "Input & Engine"),
                mic_section,
                cx,
            ))
            .child(scene_section)
            .child(advanced_audio)
            .child(titled(
                self.text("测试录音", "Test Recording"),
                card(cx).child(self.mic_card_body(cx)),
                cx,
            ))
            .child(diagnostics)
    }

    fn native_backend_label(&self, backend: &Value) -> String {
        match backend.as_str() {
            Some("objective_cpp") => self
                .text(
                    "原生 Objective-C++ 采集引擎",
                    "Native Objective-C++ capture",
                )
                .to_owned(),
            Some(backend) => backend.to_owned(),
            None => self
                .text("采集后端未知", "Capture backend unknown")
                .to_owned(),
        }
    }

    fn mic_start_button(&self, cx: &mut Context<Self>) -> Button {
        Button::new("start-mic-test")
            .small()
            .primary()
            .rounded(px(20.))
            .label(if self.mic == MicState::Done {
                self.text("重新试音", "Test again")
            } else {
                self.text("测试录音", "Test Recording")
            })
            .disabled(self.audio_busy())
            .on_click(cx.listener(|this, _, _, cx| this.start_mic(cx)))
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
            let mut row_actions = h_flex()
                .gap_2()
                .flex_wrap()
                .child(self.mic_start_button(cx));
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
    // Recognition and polish
    // -------------------------------------------------------------------------
    fn recognition_page(&self, cx: &mut Context<Self>) -> Div {
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

        let mut recognition_card = card(cx)
            .gap_2()
            .child(self.field_row("asr.model", true, cx))
            .when_some(selected_model, |card, model| {
                let transport = if model["transport"] == "realtime_ws" {
                    self.text("实时 WebSocket", "Realtime WebSocket")
                } else {
                    self.text("文件识别", "File transcription")
                };
                let language = if model["language_count"].is_number() {
                    format!(
                        "{} {} · {} {}",
                        model["language_count"],
                        self.text("种语言", "languages"),
                        model["chinese_dialect_count"].as_u64().unwrap_or(0),
                        self.text("种中文方言", "Chinese dialects")
                    )
                } else {
                    self.text("依模型而定", "Model dependent").into()
                };
                let mut context = model["max_input_tokens"]
                    .as_u64()
                    .map(|tokens| format!("{tokens} tokens"))
                    .unwrap_or_default();
                if let Some(seconds) = model["rollover_audio_seconds"].as_u64() {
                    if !context.is_empty() {
                        context.push_str(" · ");
                    }
                    context.push_str(&format!("{seconds} s {}", self.text("续接", "rollover")));
                }
                if context.is_empty() {
                    context = "—".into();
                }
                card.child(readout(self.text("传输协议", "Transport"), transport, cx))
                    .child(cap_badges)
                    .child(row_divider(cx))
                    .child(self.field_row("asr.language", false, cx))
                    .child(summary_strip(
                        [
                            (self.text("语言覆盖", "Language coverage"), language),
                            (self.text("上下文容量", "Context capacity"), context),
                            (
                                self.text("多模态", "Multimodal"),
                                if model["supports_screen_context"] == true {
                                    self.text("支持看屏幕说话", "Screen context supported")
                                } else {
                                    self.text("仅语音输入", "Voice input")
                                }
                                .into(),
                            ),
                        ],
                        false,
                        cx,
                    ))
            })
            .when(selected_model.is_none(), |card| {
                card.child(self.field_row("asr.language", false, cx))
            })
            .child(self.field_row("cursor_context_enabled", true, cx))
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

        let endpoint_summary = if get(self.config(), "asr.realtime_url")
            .as_str()
            .unwrap_or_default()
            .is_empty()
        {
            self.text("公共实时端点", "Public realtime endpoint")
        } else {
            self.text("自定义空间端点", "Custom workspace endpoint")
        };
        let endpoint = card(cx)
            .gap_2()
            .child(readout(
                self.text("端点类型", "Endpoint type"),
                endpoint_summary,
                cx,
            ))
            .child(row_divider(cx))
            .child(self.field_row("asr.realtime_url", false, cx))
            .child(
                Button::new("public-endpoint")
                    .xsmall()
                    .label(self.text("恢复公共实时端点", "Restore public realtime endpoint"))
                    .on_click(cx.listener(|this, _, window, cx| {
                        this.set_config("asr.realtime_url", json!(""), window, cx);
                        this.sync_controls(window, cx, true);
                    })),
            );
        let specs =
            self.disclosure(
                "recognition",
                self.text("识别模型详细规格", "ASR Model Specifications"),
                endpoint_summary.into(),
                |this, cx| {
                    let mut inner = v_flex();
                    if let Some(model) = this
                        .array("asr_models")
                        .iter()
                        .find(|model| &model["id"] == get(this.config(), "asr.model"))
                    {
                        let mut specs =
                            v_flex()
                                .gap_1()
                                .p_3()
                                .rounded_md()
                                .bg(cx.theme().secondary.opacity(0.45))
                                .border_1()
                                .border_color(cx.theme().border)
                                .child(div().text_xs().font_weight(FontWeight::SEMIBOLD).child(
                                    this.text("识别模型详细规格", "ASR Model Specifications"),
                                ));
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

                    inner
                },
                cx,
            );
        v_flex()
            .gap_5()
            .w_full()
            .min_w_0()
            .child(titled(
                self.text("语音识别模型", "Speech Recognition"),
                recognition_card,
                cx,
            ))
            .child(titled(
                self.text("Model Studio 实时端点", "Model Studio Realtime Endpoint"),
                endpoint,
                cx,
            ))
            .child(self.dictation_scenes(cx))
            .child(specs)
    }

    fn polish_page(&self, cx: &mut Context<Self>) -> Div {
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

        let mut polish_card = card(cx)
            .py_0()
            .child(self.field_row("enable_polish", false, cx));

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

        let style_card = card(cx)
            .gap_2()
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

        let advanced = self.disclosure(
            "polish",
            self.text(
                "高级润色模型与自定义 Prompt",
                "Advanced Polish Model & Custom Prompts",
            ),
            self.text(
                "模型参数与 5 类提示词覆盖",
                "Model parameters and five prompt categories",
            )
            .into(),
            |this, cx| {
                let inner = v_flex().gap_3().child(
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
            .gap_5()
            .w_full()
            .min_w_0()
            .child(polish_card)
            .child(titled(
                self.text("润色风格", "Polish Style"),
                style_card,
                cx,
            ))
            .child(advanced)
    }

    fn shortcuts_page(&self, cx: &mut Context<Self>) -> Div {
        let active = get(self.config(), "hotkey.active_hotkeys")
            .as_array()
            .cloned()
            .unwrap_or_else(|| vec![json!("fn")]);
        let fn_enabled = active.iter().any(|key| key == "fn");
        let keys = shortcut::custom_keys(self.config());

        let query = self.shortcut_filter.read(cx).value().trim().to_lowercase();
        let fn_matches = self
            .text("fn globe 地球键 内置热键", "fn globe built-in hotkeys")
            .contains(&query);
        let built_in = card(cx).py_1().child(
            h_flex()
                .w_full()
                .justify_between()
                .items_center()
                .gap_3()
                .py_2()
                .child(
                    h_flex().items_center().gap_2().child(
                        div()
                            .text_sm()
                            .font_weight(FontWeight::MEDIUM)
                            .child(self.text("Fn 键（按住 / 切换）", "Fn key (hold / toggle)")),
                    ),
                )
                .child(
                    h_flex()
                        .gap_2()
                        .items_center()
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
                        )
                        .child(keycap("Fn", cx)),
                ),
        );

        let mut trigger_card = card(cx).gap_0().py_1();
        let mut shown = 0;
        for key in &keys {
            let code = key["key_code"].clone();
            let name = key["display_name"].as_str().unwrap_or("Key").to_owned();
            if !name.to_lowercase().contains(&query) {
                continue;
            }
            shown += 1;
            trigger_card = trigger_card
                .child(
                    h_flex()
                        .w_full()
                        .justify_between()
                        .items_center()
                        .gap_2()
                        .py_2p5()
                        .child(
                            div()
                                .text_sm()
                                .font_weight(FontWeight::SEMIBOLD)
                                .child(name.clone()),
                        )
                        .child(
                            h_flex()
                                .gap_2()
                                .items_center()
                                .child(
                                    div()
                                        .min_w(px(104.))
                                        .flex()
                                        .justify_center()
                                        .child(keycap(name, cx)),
                                )
                                .child(
                                    Button::new((
                                        "remove-key",
                                        code.as_u64().unwrap_or_default() as usize,
                                    ))
                                    .xsmall()
                                    .ghost()
                                    .icon(IconName::X)
                                    .accessibility_label(self.text("移除", "Remove"))
                                    .on_click(cx.listener(
                                        move |this, _, window, cx| {
                                            let keys = shortcut::custom_keys(this.config())
                                                .into_iter()
                                                .filter(|key| key["key_code"] != code)
                                                .collect::<Vec<_>>();
                                            this.set_config(
                                                "hotkey.custom_keys",
                                                json!(keys),
                                                window,
                                                cx,
                                            );
                                        },
                                    )),
                                ),
                        ),
                )
                .child(row_divider(cx));
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

        trigger_card = trigger_card.child(
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
                                "录制快捷键"
                            },
                            if self.capture.active {
                                "Cancel capture"
                            } else {
                                "Record Shortcut"
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
        );

        let double_tap = get(self.config(), "hotkey.double_tap_threshold")
            .as_f64()
            .unwrap_or(0.3);
        let shortcuts_advanced = self.disclosure(
            "shortcuts",
            self.text("高级热键参数", "Advanced Hotkey Tuning"),
            format!("{} {double_tap:.2} s", self.text("双击间隔", "Double-tap")),
            |this, cx| this.field_group(&["hotkey.double_tap_threshold"], cx),
            cx,
        );

        v_flex().w_full().min_w_0().gap_5()
            .child(v_flex().gap_2()
                .child(self.filter_input("shortcut-filter", &self.shortcut_filter, self.text("筛选快捷键", "Filter Shortcuts"), cx))
                .child(note(self.text(
                    "按住说话，松开结束。轻按一次进入免手持听写，再按一次结束。按 Esc 取消。",
                    "Hold to speak and release to finish. Tap once for hands-free, then tap again to finish. Press Esc to cancel."
                ), cx)))
            .when(fn_matches, |page| page.child(titled(self.text("内置热键", "Built-in Hotkeys"), built_in, cx)))
            .when(!fn_matches && shown == 0, |page| page.child(note(self.text("没有匹配的快捷键", "No matching shortcuts"), cx)))
            .child(titled(self.text("自定义触发键", "Custom Trigger Keys"), trigger_card, cx))
            .child(shortcuts_advanced)
    }

    // -------------------------------------------------------------------------
    // DashScope credentials and general preferences
    // -------------------------------------------------------------------------
    fn connection_card(&self, cx: &mut Context<Self>) -> Div {
        let mut connection_card = card(cx)
            .gap_2()
            .child(self.field_row("api_key", true, cx))
            .child(
                h_flex().gap_2().flex_wrap().child(
                    Button::new("check-models")
                        .small()
                        .rounded(px(20.))
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
                ),
            );

        if let Some(results) = self.model_results.as_array().filter(|r| !r.is_empty()) {
            let mut list = h_flex()
                .flex_wrap()
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
                list = list.child(
                    badge(
                        format!("{name} · {message}"),
                        if result["status"] == "ok" {
                            Tone::Success
                        } else {
                            Tone::Warning
                        },
                        cx,
                    )
                    .max_w_full()
                    .whitespace_normal(),
                );
            }
            connection_card = connection_card.child(list);
        }

        connection_card = connection_card.child(row_divider(cx)).child(
            h_flex().justify_start().items_center().child(
                Button::new("obtain-api-key")
                    .small()
                    .ghost()
                    .icon(IconName::ExternalLink)
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
        titled(
            self.text("DashScope 服务连接", "DashScope Connection"),
            connection_card,
            cx,
        )
    }

    fn general_page(&self, cx: &mut Context<Self>) -> Div {
        let updates = card(cx)
            .gap_1()
            .child(
                h_flex()
                    .items_center()
                    .gap_2p5()
                    .flex_wrap()
                    .child(self.active_icon(40.))
                    .child(
                        v_flex()
                            .flex_1()
                            .min_w_0()
                            .gap_0p5()
                            .child(
                                div()
                                    .text_sm()
                                    .font_weight(FontWeight::SEMIBOLD)
                                    .child("Vocal More"),
                            )
                            .child(note(
                                format!("v{}", self.snapshot["version"].as_str().unwrap_or("—")),
                                cx,
                            )),
                    )
                    .child(
                        Button::new("check-updates")
                            .small()
                            .rounded(px(20.))
                            .label(self.text("立即检查", "Check Now"))
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.action("checkForUpdates", json!({}), cx);
                            })),
                    ),
            )
            .child(row_divider(cx))
            .child(self.field_row("update_channel", false, cx));
        let dictation = titled(
            self.text("听写与粘贴", "Dictation & Paste"),
            card(cx).py_0().child(self.field_group(
                &[
                    "default_mode",
                    "auto_paste",
                    "native_fast_paste",
                    "restore_clipboard",
                    "streaming_paste",
                ],
                cx,
            )),
            cx,
        );
        let icons = titled(
            self.text("应用图标", "App Icon"),
            card(cx)
                .gap_2()
                .child(note(self.text("图标样式", "Icon style"), cx))
                .child(self.visual_picker("ui.app_icon", cx))
                .child(row_divider(cx))
                .child(self.field_row("ui.icon_visibility", false, cx)),
            cx,
        );

        let mut maintenance_card = card(cx)
            .child(self.field_group(
                &["ui.language", "network.proxy_url", "ui.advanced_settings"],
                cx,
            ))
            .child(row_divider(cx));

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
            .child(updates)
            .child(dictation)
            .child(icons)
            .child(titled(
                self.text("偏好与维护", "Preferences & Maintenance"),
                maintenance_card,
                cx,
            ))
    }

    fn visual_picker(&self, key: &'static str, cx: &mut Context<Self>) -> Div {
        let field = schema::FIELDS
            .iter()
            .find(|field| field.key == key)
            .unwrap();
        let Kind::Choice(options) = field.kind else {
            unreachable!()
        };
        let selected = get(self.config(), key);
        h_flex()
            .gap_2p5()
            .w_full()
            .items_stretch()
            .children(options.iter().map(|&(value, zh, en)| {
                let preview = match key {
                    "ui.app_icon" => app_icon(value, 38.).into_any_element(),
                    "ui.color_scheme" => mini_window(value).into_any_element(),
                    "ui.interface_material" => {
                        let glass = value == "glass";
                        div()
                            .size(px(36.))
                            .rounded_full()
                            .border_1()
                            .border_color(rgb(0xffffff))
                            .shadow_sm()
                            .when(glass, |orb| orb.bg(gradient(0xffffff, 0xa3afe1)))
                            .when(!glass, |orb| orb.bg(rgb(0xf2f3fa)))
                            .into_any_element()
                    }
                    _ => unreachable!(),
                };
                let label = self.text(zh, en);
                v_flex()
                    .flex_1()
                    .min_w_0()
                    .items_center()
                    .gap_1p5()
                    .child(
                        tile(
                            SharedString::from(format!("{key}--{value}")),
                            label,
                            *selected == value,
                            cx,
                        )
                        .h(px(60.))
                        .bg(gradient(0xc5e6f4, 0x8b77cb))
                        .child(preview)
                        .on_click(cx.listener(
                            move |this, _, window, cx| {
                                this.set_config(key, json!(value), window, cx);
                            },
                        )),
                    )
                    .child(tile_caption(label, *selected == value, cx))
            }))
    }

    fn appearance_page(&self, cx: &mut Context<Self>) -> Div {
        let appearance = titled(
            self.text("外观", "Appearance"),
            card(cx)
                .gap_2p5()
                .child(
                    h_flex()
                        .gap_3()
                        .items_stretch()
                        .when(self.narrow, |row| row.flex_col())
                        .child(
                            v_flex()
                                .gap_2()
                                .flex_1()
                                .min_w_0()
                                .child(note(self.text("配色方案", "Color scheme"), cx))
                                .child(self.visual_picker("ui.color_scheme", cx)),
                        )
                        .when(!self.narrow, |row| {
                            row.child(div().w(px(1.)).bg(cx.theme().border))
                        })
                        .child(
                            v_flex()
                                .gap_2()
                                .flex_1()
                                .min_w_0()
                                .child(note(self.text("界面材质", "Interface material"), cx))
                                .child(self.visual_picker("ui.interface_material", cx)),
                        ),
                )
                .child(row_divider(cx))
                .child(self.field_row("ui.reduce_motion", false, cx)),
            cx,
        );

        let position = crate::theme::UiPreferences::from_config(self.config()).capsule_position;
        let preview = h_flex()
            .w_full()
            .h(px(109.))
            .p_4()
            .rounded(px(9.))
            .bg(if cx.theme().mode.is_dark() {
                gradient(0x353549, 0x252d47)
            } else {
                gradient(0xf3effa, 0xcfd9ee)
            })
            .justify_center()
            .when(position == "top", |row| row.items_start())
            .when(position == "bottom", |row| row.items_end())
            .when(position == "custom", |row| row.items_center().justify_end())
            .child(
                h_flex()
                    .h(px(36.))
                    .px_4()
                    .gap_2p5()
                    .items_center()
                    .rounded_full()
                    .bg(rgb(0x000000))
                    .border_1()
                    .border_color(rgba(0xffffff52))
                    .shadow_lg()
                    .text_color(rgb(0xeaeaea))
                    .child(waveform(&[3, 4, 6, 9, 12, 15, 12, 9, 6, 4, 3], 1.7))
                    .child(
                        div()
                            .text_size(px(11.5))
                            .child(self.text("轻声听写中 · 0:12", "Dictating softly · 0:12")),
                    ),
            );

        let mut anchors = div()
            .relative()
            .w(px(131.))
            .h(px(72.))
            .flex_shrink_0()
            .rounded(px(10.))
            .border_1()
            .border_color(cx.theme().border)
            .bg(cx.theme().popover);
        for (value, zh, en, x, y) in [
            ("top", "顶部", "Top", 31., 4.),
            ("custom", "自定义", "Custom", 67., 25.),
            ("bottom", "底部", "Bottom", 31., 46.),
        ] {
            let label = self.text(zh, en);
            anchors = anchors.child(
                div().absolute().left(px(x)).top(px(y)).child(
                    Button::new(SharedString::from(format!("ui.capsule_position--{value}")))
                        .xsmall()
                        .h(px(20.))
                        .rounded(px(10.))
                        .label(label)
                        .selected(position == value)
                        .when(position == value, |button| button.primary())
                        .on_click(cx.listener(move |this, _, window, cx| {
                            this.set_config("ui.capsule_position", json!(value), window, cx);
                        })),
                ),
            );
        }
        let capsule = titled(self.text("悬浮胶囊", "Floating Capsule"), card(cx).gap_2p5()
            .child(preview)
            .child(row_divider(cx))
            .child(h_flex().items_center().gap_3()
                .child(v_flex().flex_1().min_w_0().gap_1()
                    .child(div().text_sm().font_weight(FontWeight::SEMIBOLD).child(self.text("胶囊位置", "Capsule position")))
                    .child(note(self.text("免手持录音时可直接拖拽至自定义位置。", "Drag the capsule during hands-free recording to customize its position."), cx)))
                .child(anchors))
            .child(row_divider(cx))
            .child(h_flex().justify_between().gap_2().items_center().flex_wrap()
                .child(div().text_sm().font_weight(FontWeight::MEDIUM).child(self.text("重置自定义位置", "Reset custom position")))
                .child(Button::new("reset-capsule-position").small().rounded(px(20.))
                    .label(self.text("恢复底部默认", "Restore bottom default"))
                    .on_click(cx.listener(|this, _, window, cx| {
                        this.apply_form_patch(json!({"ui": {
                            "capsule_position": "bottom", "capsule_custom_x": null, "capsule_custom_y": null
                        }}), window, cx);
                    })))), cx);
        v_flex().gap_5().w_full().min_w_0().child(appearance).child(
            v_flex().gap_2().child(capsule).child(note(
                self.text(
                    "悬浮胶囊始终保持深色外观。",
                    "The floating capsule always uses its dark appearance.",
                ),
                cx,
            )),
        )
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

    pub(super) fn matches_settings(&self, tab: Tab, query: &str) -> bool {
        let en = self.english();
        let keywords = match tab {
            Tab::Engine => self.text(
                "权限 麦克风 试音 API Key",
                "permissions microphone test API key",
            ),
            Tab::Audio => self.text("低声场景预设 低语校准", "whisper presets calibration"),
            Tab::Recognition => self.text("自定义听写场景", "dictation scenes"),
            Tab::Polish => self.text("自定义提示词", "custom prompts"),
            Tab::Shortcuts => self.text("触发键 Fn", "trigger hotkeys Fn"),
            Tab::Dictionary => self.text(
                "自定义词条 隐私 学习审查",
                "custom terms privacy learning review",
            ),
            Tab::History => self.text(
                "录音 费用 存储 压缩",
                "recordings billing storage compression",
            ),
            _ => "",
        };
        query.to_lowercase().split_whitespace().all(|word| {
            tab.title(en).to_lowercase().contains(word)
                || keywords.to_lowercase().contains(word)
                || schema::SECTIONS.iter().any(|(owner, zh, title, _)| {
                    *owner == tab && (if en { title } else { zh }).to_lowercase().contains(word)
                })
                || schema::FIELDS
                    .iter()
                    .filter(|field| field.tab == tab)
                    .any(|field| {
                        field.key.contains(word) || field.title(en).to_lowercase().contains(word)
                    })
        })
    }

    fn active_icon(&self, size: f32) -> impl IntoElement {
        app_icon(
            crate::theme::UiPreferences::from_config(self.config()).app_icon,
            size,
        )
    }

    fn engine_state(&self) -> &'static str {
        if self.attention_count() > 0 {
            self.text("需要处理", "Needs Attention")
        } else if matches!(
            self.snapshot["audio_input_status"]["processing_mode"].as_str(),
            Some("macos_voice_processing")
        ) {
            self.text("就绪 · Apple 语音处理", "Ready · Apple Voice Processing")
        } else {
            self.text("就绪 · 低延迟", "Ready · Low Latency")
        }
    }

    fn sidebar_item(&self, tab: Tab, cx: &mut Context<Self>) -> Button {
        let title = tab.title(self.english());
        let active = self.tab == tab;
        let featured = tab == Tab::Engine;
        let pending = if tab == Tab::Dictionary {
            self.pending_learning_count()
        } else {
            0
        };
        let icon = if featured {
            self.active_icon(34.).into_any_element()
        } else {
            let (name, top, bottom, ink) = match tab {
                Tab::General => (IconName::Settings, 0xaeb6bf, 0x7e8993, 0xffffff),
                Tab::Audio => (IconName::AudioLines, 0x38adf9, 0x0969dc, 0xffffff),
                Tab::Appearance => (IconName::AppWindow, 0xfafafb, 0xd7dae1, 0x626a7a),
                Tab::Recognition => (IconName::Sparkles, 0xdf78fd, 0x923ed8, 0xffffff),
                Tab::Polish => (IconName::WandSparkles, 0x258fe4, 0xc654a7, 0xffffff),
                Tab::Shortcuts => (IconName::Command, 0xfafafb, 0xd7dae1, 0x626a7a),
                Tab::Dictionary => (IconName::BookOpen, 0xff9b67, 0xee593d, 0xffffff),
                Tab::History => (IconName::Clock, 0x56d7bb, 0x179b9f, 0xffffff),
                Tab::Engine => unreachable!(),
            };
            h_flex()
                .size(px(24.))
                .flex_shrink_0()
                .rounded(px(6.))
                .items_center()
                .justify_center()
                .bg(gradient(top, bottom))
                .text_color(rgb(ink))
                .child(Icon::new(name).small())
                .into_any_element()
        };
        Button::new(tab.id())
            .ghost()
            .w_full()
            .h(px(if featured { 46. } else { 34. }))
            .rounded(px(9.))
            .px_2()
            .accessibility_label(title)
            .selected(active)
            .text_color(cx.theme().sidebar_foreground)
            .when(active, |button| {
                button
                    .bg(cx.theme().sidebar_accent)
                    .text_color(cx.theme().sidebar_accent_foreground)
            })
            .on_click(cx.listener(move |this, _, window, cx| {
                this.sidebar_search
                    .update(cx, |input, cx| input.set_value("", window, cx));
                this.switch_tab(tab, window, cx);
            }))
            .child(
                h_flex()
                    .w_full()
                    .min_w_0()
                    .gap_2p5()
                    .items_center()
                    .child(icon)
                    .child(
                        v_flex()
                            .flex_1()
                            .min_w_0()
                            .child(
                                div()
                                    .text_size(px(13.5))
                                    .font_weight(if active || featured {
                                        FontWeight::SEMIBOLD
                                    } else {
                                        FontWeight::NORMAL
                                    })
                                    .child(title),
                            )
                            .when(featured, |copy| copy.child(note(self.engine_state(), cx))),
                    )
                    .when(featured && self.attention_count() > 0, |row| {
                        row.child(status_dot(Tone::Warning, cx))
                    })
                    .when(pending > 0, |row| {
                        row.child(badge(format!("{pending}"), Tone::Primary, cx))
                    }),
            )
    }

    fn settings_sidebar(&self, cx: &mut Context<Self>) -> Div {
        let query = self.sidebar_search.read(cx).value().to_string();
        let mut sidebar = v_flex()
            .id("settings-sidebar")
            .overflow_y_scroll()
            .w(px(if self.narrow { 184. } else { 259. }))
            .h_full()
            .flex_shrink_0()
            .bg(
                if crate::theme::UiPreferences::from_config(self.config()).interface_material
                    == "glass"
                {
                    cx.theme().sidebar.opacity(0.84)
                } else {
                    cx.theme().sidebar
                },
            )
            .border_r_1()
            .border_color(cx.theme().sidebar_border)
            .pt(px(44.))
            .px(px(13.))
            .pb_3()
            .gap_3()
            .child(self.filter_input(
                "settings-sidebar-search",
                &self.sidebar_search,
                self.text("搜索设置", "Search Settings"),
                cx,
            ));
        let mut found = false;
        for group in [
            &[Tab::Engine][..],
            &[Tab::General, Tab::Audio, Tab::Appearance],
            &[Tab::Recognition, Tab::Polish],
            &[Tab::Shortcuts, Tab::Dictionary, Tab::History],
        ] {
            let mut items = v_flex().gap_0p5();
            let mut has_items = false;
            for &tab in group
                .iter()
                .filter(|&&tab| self.matches_settings(tab, &query))
            {
                items = items.child(self.sidebar_item(tab, cx));
                has_items = true;
            }
            if has_items {
                sidebar = sidebar.child(items);
                found = true;
            }
        }
        div()
            .h_full()
            .flex_shrink_0()
            .child(sidebar.when(!found, |sidebar| {
                sidebar.child(
                    div()
                        .id("settings-search-empty")
                        .test_support()
                        .px_2()
                        .child(note(
                            self.text("没有匹配的设置", "No matching settings"),
                            cx,
                        )),
                )
            }))
    }

    fn filter_input(
        &self,
        id: &'static str,
        input: &Entity<gpui_kit::component::input::InputState>,
        label: &'static str,
        cx: &App,
    ) -> Div {
        div()
            .w_full()
            .min_w_0()
            .rounded_full()
            .bg(cx.theme().secondary)
            .child(
                Input::new(input)
                    .id(id)
                    .accessibility_id(id)
                    .aria_label(label)
                    .appearance(false)
                    .w_full()
                    .prefix(
                        Icon::new(IconName::Search)
                            .small()
                            .text_color(cx.theme().muted_foreground),
                    ),
            )
    }

    fn settings_header(&self, cx: &mut Context<Self>) -> Div {
        let glass =
            crate::theme::UiPreferences::from_config(self.config()).interface_material == "glass";
        let surface = cx.theme().popover;
        let pill = || {
            h_flex()
                .h(px(32.))
                .items_center()
                .rounded_full()
                .border_1()
                .border_color(cx.theme().border)
                .bg(if glass {
                    surface.opacity(0.89)
                } else {
                    surface
                })
        };
        h_flex()
            .h(px(56.))
            .flex_shrink_0()
            .px(px(24.))
            .gap(px(14.))
            .items_center()
            .child(
                pill()
                    .child(
                        Button::new("nav-back")
                            .ghost()
                            .small()
                            .rounded(px(16.))
                            .w(px(34.))
                            .icon(IconName::ChevronLeft)
                            .accessibility_label(self.text("后退", "Back"))
                            .disabled(self.nav_index == 0)
                            .on_click(cx.listener(|this, _, window, cx| this.go_back(window, cx))),
                    )
                    .child(div().w(px(1.)).h(px(15.)).bg(cx.theme().border))
                    .child(
                        Button::new("nav-forward")
                            .ghost()
                            .small()
                            .rounded(px(16.))
                            .w(px(34.))
                            .icon(IconName::ChevronRight)
                            .accessibility_label(self.text("前进", "Forward"))
                            .disabled(self.nav_index + 1 == self.nav_history.len())
                            .on_click(
                                cx.listener(|this, _, window, cx| this.go_forward(window, cx)),
                            ),
                    ),
            )
            .child(
                div()
                    .flex_1()
                    .h_full()
                    .flex()
                    .items_center()
                    .window_control_area(WindowControlArea::Drag)
                    .text_size(px(16.))
                    .font_weight(FontWeight::BOLD)
                    .child(self.tab.title(self.english())),
            )
            .when(
                matches!(self.tab, Tab::Shortcuts | Tab::History),
                |header| {
                    let history = self.tab == Tab::History;
                    header.child(
                        pill().size(px(32.)).child(
                            Button::new("page-actions")
                                .ghost()
                                .small()
                                .rounded(px(16.))
                                .size(px(30.))
                                .icon(IconName::Ellipsis)
                                .accessibility_label(if history {
                                    self.text("刷新录音", "Refresh recordings")
                                } else {
                                    self.text("高级热键参数", "Advanced hotkey tuning")
                                })
                                .on_click(cx.listener(move |this, _, _, cx| {
                                    if history {
                                        this.action("getRecordings", json!({}), cx);
                                    } else {
                                        this.toggle_section("shortcuts", cx);
                                    }
                                })),
                        ),
                    )
                },
            )
    }
}

impl Render for Settings {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        self.recover_rejected_action(window, cx);
        self.narrow = window.viewport_size().width < px(780.);
        let glass =
            crate::theme::UiPreferences::from_config(self.config()).interface_material == "glass";
        let mut root = h_flex()
            .relative()
            .size_full()
            .whitespace_normal()
            .font_family(cx.theme().font_family.clone())
            .text_color(cx.theme().foreground)
            .when(!glass, |root| root.bg(cx.theme().background));

        if get(self.config(), "ui.onboarding_completed") != true {
            root = root.child(
                div()
                    .id("onboarding-page")
                    .test_support()
                    .size_full()
                    .bg(cx.theme().background)
                    .overflow_y_scroll()
                    .p_6()
                    .pt(px(44.))
                    .child(self.onboarding(cx)),
            );
        } else {
            let page = match self.tab {
                Tab::Engine => self.engine_page(cx),
                Tab::General => self.general_page(cx),
                Tab::Audio => self.audio_page(cx),
                Tab::Appearance => self.appearance_page(cx),
                Tab::Recognition => self.recognition_page(cx),
                Tab::Polish => self.polish_page(cx),
                Tab::Shortcuts => self.shortcuts_page(cx),
                Tab::Dictionary => self.dictionary_page(cx),
                Tab::History => self.history_page(cx),
            };
            root = root.child(self.settings_sidebar(cx)).child(
                v_flex()
                    .flex_1()
                    .min_w_0()
                    .h_full()
                    .bg(if glass {
                        cx.theme().background.opacity(0.95)
                    } else {
                        cx.theme().background
                    })
                    .child(self.settings_header(cx))
                    .child(
                        v_flex()
                            .id("settings-page")
                            .test_support()
                            .track_scroll(&self.page_scroll)
                            .overflow_y_scroll()
                            .flex_1()
                            .min_h_0()
                            .w_full()
                            .px(px(24.))
                            .pt_1()
                            .pb_6()
                            .gap_5()
                            .child(page.flex_shrink_0()),
                    ),
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
