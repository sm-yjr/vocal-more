// SPDX-License-Identifier: GPL-3.0-only
use super::{Settings, schema::Tab};
use gpui_kit::{
    component::{
        Disableable,
        button::{Button, ButtonVariants},
        h_flex, v_flex,
    },
    *,
};
use serde_json::{Value, json};

#[derive(Default)]
pub(crate) struct State {
    pub status: Value,
    pub target: Option<String>,
    pub focus: Option<FocusHandle>,
    pub pending: Option<u64>,
    pub operation: String,
    pub message: Option<String>,
}
impl Settings {
    pub(super) fn linux_shortcut_request(
        &mut self,
        method: &str,
        params: Value,
        cx: &mut Context<Self>,
    ) {
        match self.commands.request_checked(method, params) {
            Ok(id) => {
                self.linux_shortcuts.pending = Some(id);
                self.linux_shortcuts.operation = method.into();
                self.linux_shortcuts.message = None;
            }
            Err(error) => self.linux_shortcuts.message = Some(error.to_string()),
        }
        cx.notify();
    }
    fn start_linux_capture(
        &mut self,
        target: Option<String>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        self.capture.active = true;
        self.capture.pending = None;
        self.linux_shortcuts.target = target;
        self.linux_shortcuts.message = None;
        let focus = self
            .linux_shortcuts
            .focus
            .get_or_insert_with(|| cx.focus_handle());
        window.focus(focus, cx);
        self.commands.request("begin_hotkey_capture", json!({}));
        cx.notify();
    }
    pub(super) fn finish_linux_capture(
        &mut self,
        accelerator: Option<String>,
        cx: &mut Context<Self>,
    ) {
        if !self.capture.active {
            return;
        }
        self.capture.active = false;
        self.capture.pending = None;
        self.commands.request("end_hotkey_capture", json!({}));
        if let Some(accelerator) = accelerator {
            let target = self.linux_shortcuts.target.take();
            self.linux_shortcut_request(
                "linux_shortcuts_save",
                json!({"id":target,"accelerator":accelerator}),
                cx,
            );
        } else {
            self.linux_shortcuts.target = None;
        }
        cx.notify();
    }
    pub(super) fn linux_key_down(
        &mut self,
        event: &KeyDownEvent,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        if !self.capture.active || self.tab != Tab::Shortcuts {
            return;
        }
        cx.stop_propagation();
        window.prevent_default();
        if event.is_held {
            return;
        }
        let key = &event.keystroke.key;
        if key == "escape" {
            self.finish_linux_capture(None, cx);
            return;
        }
        if matches!(
            key.as_str(),
            "control" | "alt" | "shift" | "super" | "cmd" | "fn"
        ) {
            return;
        }
        let m = &event.keystroke.modifiers;
        let accelerator = format!(
            "{}{}{}{}{key}",
            if m.control { "<Control>" } else { "" },
            if m.alt { "<Alt>" } else { "" },
            if m.shift { "<Shift>" } else { "" },
            if m.platform { "<Super>" } else { "" }
        );
        match crate::linux::shortcuts::canonical(&accelerator) {
            Ok(accelerator) => self.finish_linux_capture(Some(accelerator), cx),
            Err(error) => {
                self.linux_shortcuts.message = Some(error.to_string());
                cx.notify();
            }
        }
    }
    pub(super) fn linux_shortcuts_page(&self, cx: &mut Context<Self>) -> Div {
        use super::pages::{note, panel, titled};
        let available = self.linux_shortcuts.status["available"] == true;
        let busy = self.linux_shortcuts.pending.is_some();
        let items = self.linux_shortcuts.status["items"]
            .as_array()
            .cloned()
            .unwrap_or_default();
        let mut body = panel(cx).child(note(
            self.text(
                "按一次开始录音，再按一次结束。设置后立即全局生效。",
                "Press once to start recording and again to finish. Changes apply globally.",
            ),
            cx,
        ));
        for (index, item) in items.iter().enumerate() {
            let edit = item["id"].as_str().unwrap_or_default().to_owned();
            let remove = edit.clone();
            body = body.child(
                h_flex()
                    .items_center()
                    .justify_between()
                    .gap_3()
                    .child(
                        div()
                            .text_sm()
                            .font_weight(FontWeight::MEDIUM)
                            .child(item["display_name"].as_str().unwrap_or("Key").to_owned()),
                    )
                    .child(
                        h_flex()
                            .gap_2()
                            .child(
                                Button::new(("linux-edit-key", index))
                                    .label(self.text("修改", "Edit"))
                                    .disabled(busy || self.capture.active)
                                    .on_click(cx.listener(move |this, _, window, cx| {
                                        this.start_linux_capture(Some(edit.clone()), window, cx)
                                    })),
                            )
                            .child(
                                Button::new(("linux-remove-key", index))
                                    .label(self.text("移除", "Remove"))
                                    .disabled(busy || self.capture.active)
                                    .on_click(cx.listener(move |this, _, _, cx| {
                                        this.linux_shortcut_request(
                                            "linux_shortcuts_remove",
                                            json!({"id":remove}),
                                            cx,
                                        )
                                    })),
                            ),
                    ),
            );
        }
        if items.is_empty() {
            body = body.child(note(
                self.text("尚未设置快捷键", "No shortcut configured"),
                cx,
            ));
        }
        let hint = if !available {
            self.linux_shortcuts.status["error"]
                .as_str()
                .unwrap_or(self.text("正在读取系统快捷键…", "Loading shortcuts…"))
        } else if self.capture.active {
            self.text(
                "请按下功能键或组合键；Esc 取消。",
                "Press a function key or shortcut; Escape cancels.",
            )
        } else if busy {
            self.text("正在保存…", "Saving…")
        } else {
            self.text(
                "最多 8 个快捷键。支持 F1–F35，或含 Ctrl、Alt、Super 的组合键。",
                "Up to 8 shortcuts. Use F1–F35 or a combination with Ctrl, Alt or Super.",
            )
        };
        body = body.child(note(hint.to_owned(), cx));
        if let Some(message) = &self.linux_shortcuts.message {
            body = body.child(note(message.clone(), cx));
        }
        let mut controls = h_flex().gap_2().child(
            Button::new("linux-capture-key")
                .primary()
                .label(self.text(
                    if self.capture.active {
                        "取消录入"
                    } else {
                        "添加快捷键"
                    },
                    if self.capture.active {
                        "Cancel capture"
                    } else {
                        "Add shortcut"
                    },
                ))
                .disabled(!available || busy || (!self.capture.active && items.len() >= 8))
                .on_click(cx.listener(|this, _, window, cx| {
                    if this.capture.active {
                        this.finish_linux_capture(None, cx);
                    } else {
                        this.start_linux_capture(None, window, cx);
                    }
                })),
        );
        controls = controls.child(
            Button::new("linux-refresh-keys")
                .label(self.text("刷新", "Refresh"))
                .disabled(busy || self.capture.active)
                .on_click(cx.listener(|this, _, _, cx| {
                    this.linux_shortcut_request("linux_shortcuts_refresh", json!({}), cx)
                })),
        );
        v_flex()
            .gap_5()
            .child(titled(
                self.text("自定义快捷键", "Custom shortcuts"),
                body.child(controls),
                cx,
            ))
            .child(titled(self.text("按住说话与短按切换", "Hold to talk and tap to toggle"),
                panel(cx)
                .child(note(self.text(
                    if self.linux_shortcuts.status["gesture_active"]==true {"手势已启用：按住超过 350 毫秒后松开结束；短按进入免提，再按结束。按住说话模式下，松开立即结束。"}
                    else if self.linux_shortcuts.status["gesture_enabled"]==true {"正在等待系统快捷键授权。请在桌面的授权窗口中确认。"}
                    else {"启用后使用系统快捷键授权获取按下/松开事件；拒绝或失败会保留原有的一键切换。"},
                    if self.linux_shortcuts.status["gesture_active"]==true {"Hold and release to finish; tap for hands-free, then press again to finish. Push-to-talk mode always finishes on release."}
                    else {"Enable system-authorized press/release events. Existing toggle shortcuts are restored if authorization fails."}),cx))
                .child(Button::new("linux-enable-gestures")
                    .label(self.text(if self.linux_shortcuts.status["gesture_enabled"]==true {"停用按住/松开手势"}else{"启用按住/松开手势"},
                        if self.linux_shortcuts.status["gesture_enabled"]==true {"Disable hold gestures"}else{"Enable hold gestures"}))
                    .disabled(!available || busy || self.capture.active || items.is_empty())
                    .on_click(cx.listener(|this,_,_,cx| {
                        let enabled=this.linux_shortcuts.status["gesture_enabled"]==true;
                        this.linux_shortcut_request(if enabled {"linux_shortcuts_disable_gestures"}else{"linux_shortcuts_enable_gestures"},json!({}),cx);
                    })))
                .child(note(self.linux_shortcuts.status["gesture_message"].as_str().unwrap_or("").to_owned(),cx)),cx))
            .child(note(
                self.text(
                    "快捷键在应用后台运行时有效。",
                    "Shortcuts work while Vocal More is running.",
                ),
                cx,
            ))
    }
}

impl Settings {
    pub(super) fn linux_input_panel(&self, cx: &mut Context<Self>) -> Div {
        use super::pages::{note, panel, titled};
        let status = &self.snapshot["linux_input"];
        let enabled = status["enabled"] == true;
        let busy = self.linux_shortcuts.pending.is_some() || status["pending"] == true;
        titled(self.text("兼容自动插入","Compatible automatic input"),panel(cx)
            .child(note(self.text("为 Codex、Chrome 和 Ghostty 启用系统授权的自动粘贴。写入前核对原目标与焦点，并恢复原剪贴板。Ghostty 不读取终端内容、不参与自动纠错学习；多行文本可能需要在终端中确认粘贴。","Enable authorized automatic paste for Codex, Chrome and Ghostty. The original target and focus are checked and the clipboard is restored. Ghostty contents are never read or used for correction learning; multiline text may require terminal paste confirmation."),cx))
            .child(Button::new("linux-input-enable").label(self.text(if enabled{"停用兼容插入"}else{"启用兼容插入"},if enabled{"Disable compatible input"}else{"Enable compatible input"})).disabled(busy)
                .on_click(cx.listener(|this,_,_,cx|{let enabled=this.snapshot["linux_input"]["enabled"]==true;this.linux_shortcut_request(if enabled{"linux_input_disable"}else{"linux_input_enable"},json!({}),cx);})))
            .child(note(status["message"].as_str().unwrap_or("").to_owned(),cx))
            .child(Button::new("linux-input-codex").label(self.text("配置 Codex 兼容启动","Configure Codex accessibility")).disabled(busy||status["codex_configured"]==true)
                .on_click(cx.listener(|this,_,_,cx|this.linux_shortcut_request("linux_input_codex",json!({}),cx))))
            .child(note(self.text(if status["codex_configured"]==true{"Codex 兼容启动已配置，重启 Codex 后生效。"}else{"首次使用 Codex 需要配置辅助功能启动参数，再重启一次 Codex。"},if status["codex_configured"]==true{"Configured. Restart Codex once to apply."}else{"Configure accessibility and restart Codex once."}),cx))
            .child(note(self.linux_shortcuts.message.clone().unwrap_or_default(),cx)),cx)
    }
}
