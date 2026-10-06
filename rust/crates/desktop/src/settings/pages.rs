// SPDX-License-Identifier: GPL-3.0-only
use super::{
    Control, MicState, Settings, calibration,
    schema::{self, Kind, Tab, get},
    shortcut,
};
use gpui_kit::component::{
    ActiveTheme, Disableable, Selectable, Sizable,
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

fn card(title: impl Into<SharedString>, cx: &App) -> Div {
    v_flex()
        .gap_3()
        .p_4()
        .w_full()
        .bg(cx.theme().background)
        .border_1()
        .border_color(cx.theme().border)
        .rounded_lg()
        .child(
            div()
                .text_sm()
                .font_weight(FontWeight::SEMIBOLD)
                .child(title.into()),
        )
}
fn readout(label: impl Into<SharedString>, value: impl Into<SharedString>, cx: &App) -> Div {
    h_flex()
        .justify_between()
        .gap_5()
        .py_1()
        .child(
            div()
                .text_sm()
                .text_color(cx.theme().muted_foreground)
                .child(label.into()),
        )
        .child(div().text_sm().child(value.into()))
}
fn number(value: &Value) -> f64 {
    value.as_f64().unwrap_or_default()
}
fn bytes(value: &Value) -> String {
    let bytes = number(value);
    if bytes < 1024. {
        format!("{bytes:.0} B")
    } else if bytes < 1048576. {
        format!("{:.1} KB", bytes / 1024.)
    } else {
        format!("{:.1} MB", bytes / 1048576.)
    }
}
/// Escape markdown punctuation so recording content remains literal selectable
/// text. Link detection/parsing must not turn a transcript into an action.
fn literal_markdown(text: &str) -> String {
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

impl Settings {
    fn field(&self, key: &'static str, cx: &mut Context<Self>) -> AnyElement {
        let field = *schema::FIELDS
            .iter()
            .find(|field| field.key == key)
            .expect("settings field exists");
        let title = field.title(self.english());
        let disabled = self.disabled(key);
        let element =
            match &self.controls[key] {
                Control::Toggle => Switch::new(key)
                    .accessibility_label(title)
                    .checked(get(self.config(), key) == true)
                    .disabled(disabled)
                    .on_change(cx.listener(move |this, checked, window, cx| {
                        this.set_config(key, json!(checked), window, cx)
                    }))
                    .into_any_element(),
                Control::Input(input) => {
                    let mut element = Input::new(input)
                        .id(key)
                        .aria_label(title)
                        .accessibility_id(key)
                        .w(px(if matches!(field.kind, Kind::Secret) {
                            230.
                        } else {
                            310.
                        }))
                        .disabled(disabled);
                    if matches!(field.kind, Kind::Secret) {
                        element = element
                            .content_type(gpui_kit::component::input::InputContentType::Password);
                        h_flex()
                            .gap_2()
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
                    .w(px(310.))
                    .disabled(disabled)
                    .into_any_element(),
                Control::Slider(slider) => {
                    let Kind::Slider { unit, .. } = field.kind else {
                        unreachable!()
                    };
                    let value = slider.read(cx).value().start();
                    h_flex()
                        .gap_3()
                        .w(px(310.))
                        .child(Slider::new(slider).w(px(215.)).disabled(disabled))
                        .child(div().text_sm().min_w(px(72.)).child(
                            if value.fract().abs() < 0.001 {
                                format!("{value:.0} {unit}")
                            } else {
                                format!("{value:.2} {unit}")
                            },
                        ))
                        .into_any_element()
                }
            };
        let hint = if key == "api_key" && get(self.config(), "_api_key_set") == true {
            self.text(
                "密钥已保存。点击显示读取；点击清除删除已保存的密钥。",
                "API key saved. Show reveals it; Clear removes the saved key.",
            )
        } else if key == "enable_polish" && self.native_asr() {
            self.text(
                "当前模型为原生 ASR，直接转写并跳过润色。",
                "The selected native ASR model transcribes directly and skips polish.",
            )
        } else {
            field.hint(self.english())
        };
        h_flex()
            .id(SharedString::from(format!("setting-row-{key}")))
            .test_support()
            .w_full()
            .min_w_0()
            .items_center()
            .justify_between()
            .gap_6()
            .when(self.narrow || matches!(field.kind, Kind::Secret), |row| {
                row.flex_col().items_start().gap_2()
            })
            .py_3()
            .border_b_1()
            .border_color(cx.theme().border)
            .child(
                v_flex()
                    .flex_1()
                    .min_w_0()
                    .w_full()
                    .gap_1()
                    .child(div().text_sm().font_weight(FontWeight::MEDIUM).child(title))
                    .child(
                        div()
                            .text_xs()
                            .whitespace_normal()
                            .text_color(cx.theme().muted_foreground)
                            .child(hint),
                    ),
            )
            .child(div().flex_shrink_0().child(element))
            .into_any_element()
    }
    fn fields(&self, tab: Tab, cx: &mut Context<Self>) -> Div {
        let advanced = get(self.config(), "ui.advanced_settings") == true;
        v_flex().gap_1().children(
            schema::FIELDS
                .iter()
                .filter(|field| field.tab == tab && (!field.advanced || advanced))
                .map(|field| self.field(field.key, cx))
                .collect::<Vec<_>>(),
        )
    }
    fn general_page(&self, cx: &mut Context<Self>) -> Div {
        let advanced = get(self.config(), "ui.advanced_settings") == true;
        let mut page = v_flex().gap_4().child(self.fields(Tab::General, cx));
        if advanced {
            let mut checks = card(self.text("服务验证", "Provider checks"), cx).child(
                h_flex()
                    .gap_2()
                    .child(
                        Button::new("check-models")
                            .small()
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
                        Button::new("obtain-api-key")
                            .small()
                            .label(self.text("获取 API Key", "Get API key"))
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.action(
                                    "openExternal",
                                    json!({"url":"https://dashscope.console.aliyun.com/apiKey"}),
                                    cx,
                                );
                            })),
                    ),
            );
            if let Some(results) = self.model_results.as_array() {
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
                    checks = checks.child(readout(name, message, cx));
                }
            }
            page = page.child(checks);
        }
        let mut info =
            card(self.text("应用与首次设置", "Application and setup"), cx).child(readout(
                self.text("版本", "Version"),
                self.snapshot["version"].as_str().unwrap_or("—"),
                cx,
            ));
        if get(self.config(), "ui.onboarding_skipped") == true {
            info = info.child(
                div()
                    .text_sm()
                    .text_color(cx.theme().warning)
                    .child(self.text(
                        "首次设置尚未全部完成。",
                        "Initial setup has unfinished items.",
                    )),
            );
        }
        let mut actions = h_flex().gap_2();
        if advanced {
            actions = actions.child(
                Button::new("open-config")
                    .small()
                    .label(self.text("打开配置文件", "Open configuration file"))
                    .on_click(cx.listener(|this, _, _, cx| {
                        this.action("openConfigFile", json!({}), cx);
                    })),
            );
        }
        if self.rerun_confirm {
            actions = actions
                .child(
                    Button::new("rerun-confirm")
                        .small()
                        .label(self.text("确认重新设置", "Confirm setup"))
                        .on_click(cx.listener(|this, _, window, cx| {
                            this.rerun_confirm = false;
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
            actions = actions.child(
                Button::new("rerun-setup")
                    .small()
                    .label(self.text("重新运行首次设置", "Run setup again"))
                    .on_click(cx.listener(|this, _, _, cx| {
                        this.rerun_confirm = true;
                        cx.notify();
                    })),
            );
        }
        page.child(info.child(actions))
    }
    fn audio_page(&self, cx: &mut Context<Self>) -> Div {
        let input = &self.snapshot["audio_input_status"];
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
            (
                "preferred_microphone_mode",
                "首选麦克风模式",
                "Preferred microphone mode",
            ),
            ("source_sample_rate_hz", "源采样率", "Source sample rate"),
            ("source_channels", "源声道", "Source channels"),
            ("output_sample_rate_hz", "输出采样率", "Output sample rate"),
            ("converter_name", "转换器", "Converter"),
            ("gain_control_verified", "运行验证", "Runtime verification"),
            ("queue_dropped_blocks", "丢弃音频块", "Dropped audio blocks"),
            ("runtime_fault_count", "运行故障", "Runtime faults"),
        ];
        let mut status = card(self.text("输入运行状态", "Input runtime status"), cx);
        for (key, zh, en) in labels {
            if !input[key].is_null() {
                status = status.child(readout(
                    self.text(zh, en),
                    schema::display_value(&input[key]),
                    cx,
                ));
            }
        }
        if let Some(reason) = input["fallback_reason"].as_str() {
            status = status.child(
                div()
                    .text_sm()
                    .text_color(cx.theme().warning)
                    .child(reason.to_owned()),
            );
        }
        if get(self.config(), "ui.advanced_settings") == true {
            // Full diagnostics include the verified last session and new
            // native-audio fields without truncating future backend evidence.
            status = status.child(
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
        status = status.child(
            h_flex()
                .gap_2()
                .child(
                    Button::new("refresh-devices")
                        .small()
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
        let presets = card(self.text("低声输入预设", "Low-voice presets"), cx).child(
            h_flex()
                .gap_2()
                .children(
                    [
                        ("whisper", "低声", "Whisper"),
                        ("normal", "正常", "Normal"),
                        ("noisy", "嘈杂环境", "Noisy room"),
                    ]
                    .into_iter()
                    .map(|(id, zh, en)| {
                        Button::new(id)
                            .small()
                            .label(self.text(zh, en))
                            .disabled(self.audio_busy())
                            .on_click(
                                cx.listener(move |this, _, window, cx| this.preset(id, window, cx)),
                            )
                            .into_any_element()
                    }),
                )
                .child(
                    Button::new("calibrate-whisper")
                        .small()
                        .primary()
                        .label(self.text("低声校准", "Whisper calibration"))
                        .disabled(self.audio_busy())
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.calibration.open = true;
                            cx.notify();
                        })),
                ),
        );
        v_flex()
            .gap_4()
            .child(status)
            .child(presets)
            .child(self.fields(Tab::Audio, cx))
            .child(self.mic_card(cx))
    }
    fn mic_card(&self, cx: &mut Context<Self>) -> Div {
        let mut test = card(self.text("测试录音", "Microphone test"), cx);
        if matches!(self.mic, MicState::Starting | MicState::Recording) {
            let level = calibration::waveform(
                self.mic_level,
                get(self.config(), "audio.waveform_ceiling_dbfs")
                    .as_f64()
                    .unwrap_or(-6.),
            ) * 100.;
            test = test
                .child(
                    Progress::new("mic-level")
                        .accessibility_label(self.text("麦克风电平", "Microphone level"))
                        .value(level),
                )
                .child(readout(
                    self.text("输入电平", "Input level"),
                    format!("{:.1} dBFS", calibration::dbfs(self.mic_level)),
                    cx,
                ))
                .child(
                    Button::new("stop-mic-test")
                        .small()
                        .danger()
                        .label(self.text("停止", "Stop"))
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.stop_mic();
                            cx.notify();
                        })),
                );
        } else {
            if let Some(error) = &self.mic_error {
                test = test.child(
                    div()
                        .text_sm()
                        .text_color(cx.theme().danger)
                        .child(error.clone()),
                );
            }
            let mut actions = h_flex().gap_2().child(
                Button::new("start-mic-test")
                    .small()
                    .primary()
                    .label(self.text(
                        if self.mic == MicState::Done {
                            "重新测试"
                        } else {
                            "开始测试"
                        },
                        if self.mic == MicState::Done {
                            "Test again"
                        } else {
                            "Start test"
                        },
                    ))
                    .disabled(self.audio_busy())
                    .on_click(cx.listener(|this, _, _, cx| this.start_mic(cx))),
            );
            if self.mic_playable {
                actions = actions.child(
                    Button::new("replay-mic-test")
                        .small()
                        .label(self.text(
                            if self.mic_playing {
                                "停止回放"
                            } else {
                                "回放录音"
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
            test = test.child(actions);
        }
        test
    }
    fn recognition_page(&self, cx: &mut Context<Self>) -> Div {
        let selected = self
            .array("asr_models")
            .iter()
            .find(|model| &model["id"] == get(self.config(), "asr.model"));
        let mut page = v_flex().gap_4().child(self.fields(Tab::Recognition, cx));
        if let Some(model) = selected {
            let mut capabilities = card(self.text("模型能力", "Model capabilities"), cx);
            for (key, zh, en) in [
                ("transport", "传输方式", "Transport"),
                ("pipeline", "处理流水线", "Pipeline"),
                ("language_count", "支持语言数量", "Languages"),
                ("chinese_dialect_count", "中文方言数量", "Chinese dialects"),
                ("supports_screen_context", "屏幕上下文", "Screen context"),
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
                ("max_audio_turns", "最大音频轮数", "Audio turn limit"),
                (
                    "rollover_audio_seconds",
                    "长录音滚动边界（秒）",
                    "Rollover seconds",
                ),
                ("supports_instant_hotwords", "即时词典", "Instant hotwords"),
                ("handles_inline_polish", "模型内润色", "Inline polish"),
            ] {
                if !model[key].is_null() {
                    capabilities = capabilities.child(readout(
                        self.text(zh, en),
                        schema::display_value(&model[key]),
                        cx,
                    ));
                }
            }
            page = page.child(capabilities);
        }
        page.child(
            Button::new("public-endpoint")
                .small()
                .label(self.text("恢复公共实时端点", "Restore public realtime endpoint"))
                .on_click(cx.listener(|this, _, window, cx| {
                    this.set_config("asr.realtime_url", json!(""), window, cx);
                    this.sync_controls(window, cx, true);
                })),
        )
    }
    fn polish_page(&self, cx: &mut Context<Self>) -> Div {
        let mut page = v_flex().gap_4().child(self.fields(Tab::Polish, cx));
        if get(self.config(), "ui.advanced_settings") == true {
            let category = self.prompt_category;
            let enabled = self.prompt_enabled(category);
            let disabled = self.disabled("llm.prompt_overrides");
            let categories =
                h_flex()
                    .gap_1()
                    .flex_wrap()
                    .children(schema::PROMPT_CATEGORIES.iter().map(|&(category, zh, en)| {
                        Button::new(category)
                            .small()
                            .label(self.text(zh, en))
                            .selected(self.prompt_category == category)
                            .on_click(cx.listener(move |this, _, window, cx| {
                                this.prompt_category = category;
                                this.sync_controls(window, cx, false);
                                cx.notify();
                            }))
                            .into_any_element()
                    }));
            let custom = Switch::new("custom-prompt-enabled")
                .label(self.text("使用自定义提示词", "Use custom prompt"))
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
            page=page.child(card(self.text("自定义提示词","Custom prompts"),cx).child(categories).child(custom)
                .child(Textarea::new(&self.prompts[category]).aria_label(self.text("提示词内容","Prompt text")).h(px(210.)).readonly(!enabled).disabled(disabled))
                .child(Button::new("reload-prompt-preset").small().label(self.text("重新载入系统预设","Reload system preset")).disabled(disabled||!enabled).on_click(cx.listener(move |this,_,window,cx|{let preset=this.prompt_preset(category);this.set_prompt(category,true,preset,window,cx);})))
                .child(div().text_xs().text_color(cx.theme().muted_foreground).child(self.text("系统预设随当前输出类型、程度、语气和风格改变。编辑结束时保存。","System presets follow the selected output, level, tone and persona. Edits save when focus leaves the editor."))));
        }
        page
    }
    fn shortcuts_page(&self, cx: &mut Context<Self>) -> Div {
        let active = get(self.config(), "hotkey.active_hotkeys")
            .as_array()
            .cloned()
            .unwrap_or_else(|| vec![json!("fn")]);
        let fn_enabled = active.iter().any(|key| key == "fn");
        let built_in = card(self.text("内置快捷键", "Built-in shortcut"), cx)
            .child(
                Switch::new("fn-hotkey")
                    .label("Fn / Globe")
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
                        this.action("setActiveHotkeys", json!({"hotkeys":active}), cx);
                        cx.notify();
                    })),
            )
            .child(
                div()
                    .text_xs()
                    .text_color(cx.theme().muted_foreground)
                    .child(self.text(
                        "按住说话，松开结束；短按松开进入免提；Esc 取消。",
                        "Hold to speak and release to finish; tap for hands free; Escape cancels.",
                    )),
            );
        let keys = shortcut::custom_keys(self.config());
        let mut custom = card(self.text("自定义触发键", "Custom trigger keys"), cx);
        for key in &keys {
            let code = key["key_code"].clone();
            custom = custom.child(
                h_flex()
                    .justify_between()
                    .items_center()
                    .child(
                        div()
                            .text_sm()
                            .child(key["display_name"].as_str().unwrap_or("Key").to_owned()),
                    )
                    .child(
                        Button::new(("remove-key", code.as_u64().unwrap_or_default() as usize))
                            .small()
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
        let hint = if self.capture.active {
            if self.capture.pending.is_some() {
                self.text(
                    "再次按下同一修饰键确认；Esc 取消。",
                    "Press the same modifier again to confirm; Escape cancels.",
                )
            } else {
                self.text(
                    "请按下触发键；修饰键需按两次确认。",
                    "Press a trigger key; bare modifiers require two presses.",
                )
            }
        } else {
            self.text(
                "最多 8 个独立触发键，支持左右修饰键。",
                "Up to 8 independent trigger keys, including left and right modifiers.",
            )
        };
        custom = custom
            .child(
                div()
                    .text_sm()
                    .text_color(cx.theme().muted_foreground)
                    .child(hint),
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
            );
        v_flex()
            .gap_4()
            .child(built_in)
            .child(custom)
            .child(self.fields(Tab::Shortcuts, cx))
    }
    fn dictionary_page(&self, cx: &mut Context<Self>) -> Div {
        let mut terms = card(self.text("自定义词条", "Custom terms"), cx);
        for (index, entry) in self.array("dictionary").iter().enumerate() {
            let term = entry["term"].as_str().unwrap_or_default().to_owned();
            let display = format!(
                "{}{}",
                term,
                entry["aliases"]
                    .as_array()
                    .filter(|aliases| !aliases.is_empty())
                    .map(|aliases| format!(
                        " · {}",
                        aliases
                            .iter()
                            .filter_map(Value::as_str)
                            .collect::<Vec<_>>()
                            .join(", ")
                    ))
                    .unwrap_or_default()
            );
            terms = terms.child(
                h_flex()
                    .justify_between()
                    .items_center()
                    .child(div().text_sm().child(display))
                    .child(
                        Button::new(("remove-term", index))
                            .small()
                            .label(self.text("移除", "Remove"))
                            .on_click(cx.listener(move |this, _, _, cx| {
                                this.action("removeDictEntry", json!({"term":term}), cx);
                            })),
                    ),
            );
        }
        terms = terms.child(
            v_flex()
                .gap_2()
                .child(
                    Input::new(&self.term)
                        .id("dictionary-term")
                        .aria_label(self.text("词条", "Term")),
                )
                .child(
                    Input::new(&self.aliases)
                        .id("dictionary-aliases")
                        .aria_label(self.text("别名，以逗号分隔", "Aliases, separated by commas")),
                )
                .child(
                    h_flex()
                        .gap_2()
                        .child(
                            Button::new("add-term")
                                .small()
                                .primary()
                                .label(self.text("添加词条", "Add term"))
                                .on_click(cx.listener(|this, _, window, cx| {
                                    let term = this.term.read(cx).value().trim().to_owned();
                                    if term.is_empty() {
                                        return;
                                    }
                                    let aliases =
                                        schema::list_from_text(&this.aliases.read(cx).value());
                                    if this.action(
                                        "addDictEntry",
                                        json!({"term":term,"aliases":aliases}),
                                        cx,
                                    ) == 0
                                    {
                                        return;
                                    }
                                    this.term
                                        .update(cx, |input, cx| input.set_value("", window, cx));
                                    this.aliases
                                        .update(cx, |input, cx| input.set_value("", window, cx));
                                })),
                        )
                        .child(
                            Button::new("open-dictionary")
                                .small()
                                .label(self.text("打开词典文件", "Open dictionary file"))
                                .on_click(cx.listener(|this, _, _, cx| {
                                    this.action("openDictFile", json!({}), cx);
                                })),
                        ),
                ),
        );
        let mut learning = card(self.text("学习记录", "Learning records"), cx);
        for (index, record) in self.array("dictionary_learning_records").iter().enumerate() {
            let id = record["id"].as_str().unwrap_or_default().to_owned();
            let summary = format!(
                "{} · {} · {}",
                record["term"].as_str().unwrap_or_default(),
                record["status"].as_str().unwrap_or_default(),
                record["reason_code"].as_str().unwrap_or_default()
            );
            let mut actions = h_flex().gap_1();
            for (action, zh, en) in [
                ("approveDictionaryLearning", "接受", "Approve"),
                ("rejectDictionaryLearning", "拒绝", "Reject"),
                ("undoDictionaryLearning", "撤销学习", "Undo"),
            ] {
                let id = id.clone();
                actions = actions.child(
                    Button::new((action, index))
                        .small()
                        .label(self.text(zh, en))
                        .on_click(cx.listener(move |this, _, _, cx| {
                            this.action(action, json!({"id":id}), cx);
                        })),
                );
            }
            learning = learning.child(
                v_flex()
                    .gap_2()
                    .py_2()
                    .border_b_1()
                    .border_color(cx.theme().border)
                    .child(div().text_sm().child(summary))
                    .child(
                        div()
                            .text_xs()
                            .text_color(cx.theme().muted_foreground)
                            .child(format!(
                                "{} · {}",
                                schema::display_value(&record["aliases"]),
                                record["confidence"]
                            )),
                    )
                    .child(actions),
            );
        }
        v_flex()
            .gap_4()
            .child(self.fields(Tab::Dictionary, cx))
            .child(terms)
            .child(learning)
    }
    fn history_page(&self, cx: &mut Context<Self>) -> Div {
        let storage = &self.snapshot["recording_storage"];
        let compression = card(self.text("历史录音存储", "Recording storage"), cx)
            .child(readout(
                self.text("文件与压缩", "Files and compression"),
                format!(
                    "{} / {} · {}",
                    storage["compressed_count"],
                    storage["recording_count"],
                    bytes(&storage["stored_bytes"])
                ),
                cx,
            ))
            .child(readout(
                self.text("节省空间", "Space saved"),
                bytes(&storage["bytes_saved"]),
                cx,
            ))
            .child(
                Button::new("compact-history")
                    .small()
                    .label(self.text(
                        if self.compacting {
                            "正在压缩…"
                        } else {
                            "压缩较早录音"
                        },
                        if self.compacting {
                            "Compressing…"
                        } else {
                            "Compress older recordings"
                        },
                    ))
                    .disabled(self.compacting || number(&storage["recording_count"]) <= 3.)
                    .on_click(cx.listener(|this, _, _, cx| {
                        this.action("compactRecordingHistory", json!({}), cx);
                        cx.notify();
                    })),
            );
        let mut page = v_flex().gap_4().child(compression).child(
            Input::new(&self.filter)
                .id("history-filter")
                .aria_label(self.text("搜索录音文本", "Search recording text")),
        );
        if self.pending_deletion.is_some() {
            page = page.child(
                h_flex()
                    .justify_between()
                    .items_center()
                    .child(div().text_sm().child(self.text(
                        "录音将于 5 秒后删除。",
                        "Recording will be deleted in 5 seconds.",
                    )))
                    .child(
                        Button::new("undo-deletion")
                            .small()
                            .label(self.text("撤销删除", "Undo delete"))
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.pending_deletion = None;
                                cx.notify();
                            })),
                    ),
            );
        }
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
        page = page.child(
            h_flex()
                .gap_3()
                .child(
                    card(self.text("总费用", "Total cost"), cx)
                        .flex_1()
                        .child(format!("¥{total:.4}")),
                )
                .child(
                    card(self.text("识别费用", "Recognition cost"), cx)
                        .flex_1()
                        .child(format!("¥{asr:.4}")),
                )
                .child(
                    card(self.text("润色费用", "Polish cost"), cx)
                        .flex_1()
                        .child(format!("¥{polish:.4}")),
                ),
        );
        let query = self.filter.read(cx).value().to_lowercase();
        let mut ordered = recordings.iter().enumerate().collect::<Vec<_>>();
        // A deep-linked recording is visible at the start even with many
        // entries. Its highlight stays attached to its stable recording ID.
        ordered.sort_by_key(|(_, record)| self.focus_recording.as_deref() != record["id"].as_str());
        for (index, record) in ordered {
            let id = record["id"].as_str().unwrap_or_default().to_owned();
            if self
                .pending_deletion
                .as_ref()
                .is_some_and(|(pending, _)| pending == &id)
            {
                continue;
            }
            if !query.is_empty() && !record.to_string().to_lowercase().contains(&query) {
                continue;
            }
            let duration = record
                .get("duration_seconds")
                .or(record.get("duration"))
                .map(number)
                .unwrap_or_default();
            let timestamp = record["created_at"]
                .as_str()
                .or(record["timestamp"].as_str())
                .unwrap_or("—");
            let model = self
                .array("asr_models")
                .iter()
                .find(|model| model["id"] == record["asr_model"])
                .and_then(|model| model["display_name"].as_str())
                .or(record["asr_model"].as_str())
                .unwrap_or("—");
            let mut item = card(timestamp.to_owned(), cx).id(("recording", index));
            if self.focus_recording.as_deref() == Some(&id) {
                item = item.border_color(cx.theme().primary);
            }
            item = item.child(
                div()
                    .text_xs()
                    .text_color(cx.theme().muted_foreground)
                    .child(format!(
                        "{} · {} · {:.0}s · {}",
                        record["mode"].as_str().unwrap_or_default(),
                        model,
                        duration,
                        record["status"].as_str().unwrap_or("pending")
                    )),
            );
            let retrying = record["status"] == "retrying";
            let mut actions = h_flex().gap_2().flex_wrap();
            for (action, zh, en) in [
                (
                    if self.playing.as_deref() == Some(&id) {
                        "stopRecording"
                    } else {
                        "playRecording"
                    },
                    if self.playing.as_deref() == Some(&id) {
                        "停止回放"
                    } else {
                        "回放"
                    },
                    if self.playing.as_deref() == Some(&id) {
                        "Stop playback"
                    } else {
                        "Play"
                    },
                ),
                ("retryTranscription", "重新识别", "Retry"),
                (
                    "copyTranscript",
                    if self.copied.as_deref() == Some(&id) {
                        "已复制"
                    } else {
                        "复制文本"
                    },
                    if self.copied.as_deref() == Some(&id) {
                        "Copied"
                    } else {
                        "Copy text"
                    },
                ),
            ] {
                let id = id.clone();
                let no_text = record["transcript"].as_str().unwrap_or_default().is_empty();
                actions = actions.child(
                    Button::new((action, index))
                        .small()
                        .label(self.text(zh, en))
                        .disabled(
                            (retrying && action != "copyTranscript")
                                || (action == "copyTranscript" && no_text),
                        )
                        .on_click(cx.listener(move |this, _, _, cx| {
                            this.action(action, json!({"id":id}), cx);
                            cx.notify();
                        })),
                );
            }
            let delete_id = id.clone();
            actions = actions.child(
                Button::new(("delete-recording", index))
                    .small()
                    .danger()
                    .label(self.text("删除", "Delete"))
                    .disabled(retrying)
                    .on_click(cx.listener(move |this, _, window, cx| {
                        this.stage_delete(delete_id.clone(), window, cx)
                    })),
            );
            item = item.child(actions);
            if let Some(error) = record["error"].as_str() {
                item = item.child(
                    div()
                        .text_sm()
                        .text_color(cx.theme().danger)
                        .child(error.to_owned()),
                );
            }
            if let Some(text) = record["transcript"]
                .as_str()
                .filter(|text| !text.is_empty())
            {
                item = item.child(
                    TextView::markdown(("transcript", index), literal_markdown(text))
                        .selectable(true),
                );
            }
            if let Some(meeting) = record["meeting"].as_object() {
                let meeting = Value::Object(meeting.clone());
                item = item.child(self.meeting(&meeting, index, cx));
            }
            if !record["billing"].is_null() {
                item = item.child(
                    div()
                        .text_xs()
                        .text_color(cx.theme().muted_foreground)
                        .child(format!(
                            "{}: ¥{:.4} · ASR ¥{:.4} · {} ¥{:.4}",
                            self.text("费用", "Cost"),
                            number(&record["billing"]["total_cost_cny"]),
                            number(&record["billing"]["asr_cost_cny"]),
                            self.text("润色", "Polish"),
                            number(&record["billing"]["polish_cost_cny"])
                        )),
                );
            }
            page = page.child(item);
        }
        if recordings.is_empty() {
            page = page.child(
                card(self.text("暂无录音", "No recordings"), cx).child(self.text(
                    "录音完成后可在这里回放、复制或重新识别。",
                    "Completed recordings can be played, copied or transcribed again here.",
                )),
            );
        }
        page
    }
    fn meeting(&self, meeting: &Value, index: usize, cx: &App) -> Div {
        let mut view = v_flex().gap_2();
        if let Some(segments) = meeting["segments"].as_array() {
            for (segment_index, segment) in segments.iter().enumerate() {
                let speaker = segment["speaker_label"]
                    .as_str()
                    .or(segment["speaker"].as_str())
                    .unwrap_or_default();
                let timestamp = segment["timestamp"]
                    .as_str()
                    .map(str::to_owned)
                    .unwrap_or_else(|| format!("{:.1}s", number(&segment["start_seconds"])));
                view = view
                    .child(
                        div()
                            .text_xs()
                            .text_color(cx.theme().muted_foreground)
                            .child(format!("{timestamp} · {speaker}")),
                    )
                    .child(
                        TextView::markdown(
                            SharedString::from(format!("meeting-segment-{index}-{segment_index}")),
                            literal_markdown(segment["text"].as_str().unwrap_or_default()),
                        )
                        .selectable(true),
                    );
            }
        } else if let Some(text) = meeting["transcript"].as_str() {
            view = view.child(
                TextView::markdown(("meeting-transcript", index), literal_markdown(text))
                    .selectable(true),
            );
        }
        let minutes = if meeting["minutes"].is_object() {
            &meeting["minutes"]
        } else {
            meeting
        };
        if let Some(summary) = minutes["summary"].as_str() {
            view = view
                .child(
                    div()
                        .text_sm()
                        .font_weight(FontWeight::SEMIBOLD)
                        .child(self.text("会议摘要", "Meeting summary")),
                )
                .child(
                    TextView::markdown(("minutes-summary", index), literal_markdown(summary))
                        .selectable(true),
                );
        }
        for (key, zh, en) in [
            ("key_points", "要点", "Key points"),
            ("action_items", "待办事项", "Action items"),
        ] {
            if let Some(items) = minutes[key].as_array() {
                view = view.child(
                    div()
                        .text_sm()
                        .font_weight(FontWeight::MEDIUM)
                        .child(self.text(zh, en)),
                );
                for item in items {
                    view = view.child(
                        div()
                            .text_sm()
                            .child(format!("• {}", item.as_str().unwrap_or_default())),
                    );
                }
            }
        }
        for value in [meeting, minutes] {
            if let Some(error) = value["error"].as_str() {
                view = view.child(
                    div()
                        .text_sm()
                        .text_color(cx.theme().danger)
                        .child(error.to_owned()),
                );
            }
        }
        view
    }
    fn readiness(&self, key: &str) -> bool {
        self.array("environment_checks")
            .iter()
            .any(|check| check["key"] == key && check["status"] == "ok")
    }
    fn onboarding(&self, cx: &mut Context<Self>) -> Div {
        let mut page = v_flex()
            .gap_4()
            .child(
                div()
                    .text_2xl()
                    .font_weight(FontWeight::SEMIBOLD)
                    .child(self.text("欢迎使用 Vocal More", "Welcome to Vocal More")),
            )
            .child(
                div()
                    .text_sm()
                    .text_color(cx.theme().muted_foreground)
                    .child(self.text(
                        "连接语音服务、确认系统权限，然后试一次低声输入。",
                        "Connect the speech service, verify permissions, then try low-voice input.",
                    )),
            )
            .child(
                card(
                    self.text("1. 配置语音服务", "1. Connect speech service"),
                    cx,
                )
                .child(self.field("api_key", cx))
                .child(
                    Button::new("onboarding-api-console")
                        .small()
                        .label(self.text("获取 API Key", "Get API key"))
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.action(
                                "openExternal",
                                json!({"url":"https://dashscope.console.aliyun.com/apiKey"}),
                                cx,
                            );
                        })),
                ),
            );
        let mut permissions = card(self.text("2. 系统权限", "2. System permissions"), cx);
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
            permissions = permissions.child(readout(
                self.text(zh, en),
                if self.readiness(key) {
                    self.text("已就绪", "Ready")
                } else {
                    self.text("需要处理", "Needs attention")
                },
                cx,
            ));
        }
        permissions = permissions.child(
            h_flex()
                .gap_2()
                .child(
                    Button::new("onboarding-accessibility")
                        .small()
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
                        .label(self.text("重新检查", "Check again"))
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.action("refreshEnvironment", json!({}), cx);
                            this.action("refreshDevices", json!({}), cx);
                        })),
                ),
        );
        page = page
            .child(permissions)
            .child(
                card(self.text("3. 第一次录音", "3. First recording"), cx)
                    .child(self.field("audio.input_device", cx))
                    .child(
                        Button::new("onboarding-whisper-preset")
                            .small()
                            .label(self.text("采用低声预设", "Use whisper preset"))
                            .disabled(self.audio_busy())
                            .on_click(cx.listener(|this, _, window, cx| {
                                this.preset("whisper", window, cx)
                            })),
                    ),
            )
            .child(self.mic_card(cx));
        let can_finish = get(self.config(), "_api_key_set") == true
            && !self.array("devices").is_empty()
            && self.readiness("input_device")
            && self.readiness("accessibility")
            && self.readiness("hotkey_listener")
            && self.mic == MicState::Done;
        page.child(
            h_flex()
                .justify_end()
                .gap_2()
                .child(
                    Button::new("skip-onboarding")
                        .small()
                        .label(self.text("稍后设置", "Set up later"))
                        .on_click(cx.listener(|this, _, window, cx| {
                            this.stop_mic();
                            this.set_config("ui.onboarding_completed", json!(true), window, cx);
                            this.set_config("ui.onboarding_skipped", json!(true), window, cx);
                        })),
                )
                .child(
                    Button::new("finish-onboarding")
                        .small()
                        .primary()
                        .label(self.text("完成设置", "Finish setup"))
                        .disabled(!can_finish)
                        .on_click(cx.listener(|this, _, window, cx| {
                            this.set_config("ui.onboarding_completed", json!(true), window, cx);
                            this.set_config("ui.onboarding_skipped", json!(false), window, cx);
                        })),
                ),
        )
    }
    fn calibration_sheet(&self, cx: &mut Context<Self>) -> Div {
        let mut contents = card(self.text("低声输入校准", "Whisper calibration"), cx)
            .w(px(540.))
            .shadow_lg()
            .child(div().text_sm().child(self.text(
                "保持房间安静，再用日常低声朗读：“今天的工作安排已经准备好了。”",
                "Keep the room quiet, then whisper: ‘My work plan for today is ready.’",
            )));
        if let Some(phase) = self.calibration.phase {
            contents = contents
                .child(div().text_lg().child(match phase {
                    calibration::Phase::Quiet => {
                        self.text("步骤 1：保持安静 3 秒", "Step 1: stay quiet for 3 seconds")
                    }
                    calibration::Phase::Whisper => {
                        self.text("步骤 2：低声朗读 4.5 秒", "Step 2: whisper for 4.5 seconds")
                    }
                }))
                .child(
                    Progress::new("calibration-level").value(
                        calibration::waveform(
                            self.mic_level,
                            get(self.config(), "audio.waveform_ceiling_dbfs")
                                .as_f64()
                                .unwrap_or(-6.),
                        ) * 100.,
                    ),
                )
                .child(readout(
                    self.text("输入电平", "Input level"),
                    format!("{:.1} dBFS", calibration::dbfs(self.mic_level)),
                    cx,
                ));
        } else if let Some(result) = &self.calibration.result {
            match result {
                Ok(result) => {
                    contents = contents
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
                        contents = contents.child(readout(
                            schema::FIELDS
                                .iter()
                                .find(|field| field.key == key)
                                .map(|field| field.title(self.english()))
                                .unwrap_or(key),
                            schema::display_value(&value),
                            cx,
                        ));
                    }
                    if result.clamped {
                        contents =
                            contents.child(div().text_sm().text_color(cx.theme().warning).child(
                                self.text(
                                    "建议增益已限制在安全范围 1–50。",
                                    "Recommended gain was clamped to the safe range 1–50.",
                                ),
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
                    contents=contents.child(div().text_sm().text_color(cx.theme().warning).child(if *reason=="low-snr"{self.text("低声与背景噪声差异不足，请靠近麦克风或降低环境噪声。","Whisper level is too close to room noise. Move closer to the microphone or reduce noise.")}else{self.text("有效样本不足，请重新测量。","Not enough valid samples. Measure again.")}));
                }
            }
        }
        if let Some(error) = &self.mic_error {
            contents = contents.child(
                div()
                    .text_sm()
                    .text_color(cx.theme().danger)
                    .child(error.clone()),
            );
        }
        let mut footer = h_flex().justify_end().gap_2().child(
            Button::new("close-calibration")
                .small()
                .label(self.text("关闭", "Close"))
                .on_click(cx.listener(|this, _, _, cx| {
                    this.close_calibration();
                    cx.notify();
                })),
        );
        if self.calibration.phase.is_none() {
            footer = footer.child(
                Button::new("start-calibration")
                    .small()
                    .primary()
                    .label(self.text("开始测量", "Start measurement"))
                    .on_click(cx.listener(|this, _, _, cx| this.start_calibration(cx))),
            );
        }
        div()
            .absolute()
            .inset_0()
            .bg(cx.theme().background.opacity(0.9))
            .flex()
            .items_center()
            .justify_center()
            .child(contents.child(footer))
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
                    .overflow_y_scroll()
                    .p_6()
                    .child(self.onboarding(cx)),
            );
        } else {
            let mut sidebar = v_flex()
                .w(px(162.))
                .h_full()
                .flex_shrink_0()
                .px_2()
                .py_4()
                .gap_1()
                .bg(cx.theme().sidebar)
                .border_r_1()
                .border_color(cx.theme().border)
                .child(
                    div()
                        .text_sm()
                        .font_weight(FontWeight::SEMIBOLD)
                        .px_3()
                        .py_3()
                        .child("Vocal More"),
                );
            for tab in Tab::ALL {
                // Every settings section remains reachable in the native UI;
                // advanced mode controls detail density within each section.
                sidebar =
                    sidebar.child(
                        Button::new(tab.id())
                            .small()
                            .w_full()
                            .label(tab.title(self.english()))
                            .selected(self.tab == tab)
                            .on_click(cx.listener(move |this, _, window, cx| {
                                this.switch_tab(tab, window, cx)
                            })),
                    );
            }
            sidebar = sidebar.child(div().flex_1()).child(
                div()
                    .text_xs()
                    .px_3()
                    .text_color(cx.theme().muted_foreground)
                    .child(format!(
                        "v{}",
                        self.snapshot["version"].as_str().unwrap_or("—")
                    )),
            );
            let page = match self.tab {
                Tab::General => self.general_page(cx),
                Tab::Audio => self.audio_page(cx),
                Tab::Recognition => self.recognition_page(cx),
                Tab::Polish => self.polish_page(cx),
                Tab::Shortcuts => self.shortcuts_page(cx),
                Tab::Dictionary => self.dictionary_page(cx),
                Tab::History => self.history_page(cx),
            };
            root = root.child(sidebar).child(
                v_flex()
                    .id("settings-page")
                    .test_support()
                    .flex_1()
                    .min_w_0()
                    .h_full()
                    .overflow_y_scroll()
                    .p_6()
                    .gap_5()
                    .child(
                        div()
                            .text_2xl()
                            .font_weight(FontWeight::SEMIBOLD)
                            .child(self.tab.title(self.english())),
                    )
                    .child(page),
            );
        }
        if let Some(error) = &self.error {
            root = root.child(
                h_flex()
                    .absolute()
                    .bottom_3()
                    .left_3()
                    .right_3()
                    .p_3()
                    .gap_3()
                    .rounded_lg()
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
