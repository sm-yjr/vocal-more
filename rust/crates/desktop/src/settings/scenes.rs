// SPDX-License-Identifier: GPL-3.0-only
use super::{
    Settings,
    components::row_divider,
    pages::{actions, note, panel, titled},
};
use gpui_kit::component::{
    Disableable, Sizable,
    button::{Button, ButtonVariants},
    input::{Input, InputEvent, InputState, Textarea, TextareaState},
    v_flex,
};
use gpui_kit::prelude::FluentBuilder;
use gpui_kit::*;
use serde_json::{Value, json};
use vocal_more_backend::scenes::{DESCRIPTION_LIMIT, NAME_LIMIT, Scene, Scenes};

pub(super) struct Editor {
    scene: Option<Scene>,
    name: Entity<InputState>,
    description: Entity<TextareaState>,
    confirm_delete: bool,
}

#[cfg(feature = "ui-test")]
impl Editor {
    pub(super) fn testing_input_ids(&self) -> (ElementId, ElementId) {
        (
            ("input", self.name.entity_id()).into(),
            ("input", self.description.entity_id()).into(),
        )
    }
}

impl Settings {
    fn edit_scene(&mut self, scene: Option<Scene>, window: &mut Window, cx: &mut Context<Self>) {
        let name = cx.new(|cx| {
            InputState::new(window, cx)
                .default_value(scene.as_ref().map_or("", |item| item.name.as_str()))
        });
        let description = cx.new(|cx| {
            TextareaState::new(window, cx)
                .default_value(scene.as_ref().map_or("", |item| item.description.as_str()))
                .placeholder(self.text(
                    "例如：我在玩《巫师 3》。常提到杰洛特、叶奈法、史凯利杰和昆特牌。",
                    "For example, I'm playing The Witcher 3. I often mention Geralt, Yennefer, Skellige, and Gwent.",
                ))
        });
        cx.subscribe(&name, |_, _, event: &InputEvent, cx| {
            if matches!(event, InputEvent::Change) {
                cx.notify();
            }
        })
        .detach();
        cx.subscribe(&description, |_, _, event: &InputEvent, cx| {
            if matches!(event, InputEvent::Change) {
                cx.notify();
            }
        })
        .detach();
        self.scene_editor = Some(Editor {
            scene,
            name,
            description,
            confirm_delete: false,
        });
        cx.notify();
    }

    fn scene_request(&mut self, method: &str, params: Value, cx: &mut Context<Self>) {
        match self.commands.request_checked(method, params) {
            Ok(id) => {
                self.pending.insert(id, method.into());
                self.error = None;
            }
            Err(error) => self.error = Some(error.to_string()),
        }
        cx.notify();
    }

    pub(super) fn dictation_scenes(&self, cx: &mut Context<Self>) -> Div {
        let scenes: Scenes =
            serde_json::from_value(self.config()["asr"]["scenes"].clone()).unwrap_or_default();
        let busy = self.pending.values().any(|method| {
            matches!(
                method.as_str(),
                "save_dictation_scene" | "select_dictation_scene" | "delete_dictation_scene"
            )
        });
        let editing = self.scene_editor.is_some();
        let mut content = panel(cx)
            .child(note(
                self.text(
                    "所选场景会在下次听写时发送给识别服务，帮助识别专有名词和同音词。也可以从菜单栏切换。",
                    "The selected scene is sent to the recognition service on your next dictation to help with names and similar-sounding words. You can also switch from the menu bar.",
                ),
                cx,
            ))
            .child(
                actions()
                    .child(
                        Button::new("scene-none")
                            .small()
                            .label(self.text("无场景", "No Scene"))
                            .when(scenes.active_id.is_none(), |button| button.primary())
                            .disabled(busy || scenes.active_id.is_none())
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.scene_request("select_dictation_scene", json!({"id":null}), cx);
                            })),
                    )
                    .child(
                        Button::new("scene-add")
                            .small()
                            .label(self.text("新建场景", "New Scene"))
                            .disabled(busy || editing)
                            .on_click(cx.listener(|this, _, window, cx| {
                                this.edit_scene(None, window, cx);
                            })),
                    ),
            );

        if let Some(editor) = &self.scene_editor
            && editor.scene.is_none()
        {
            content = content.child(self.scene_form(editor, busy, cx));
        }
        for scene in scenes.entries {
            let id = scene.id;
            let active = Some(id) == scenes.active_id;
            let id_label = id.to_string();
            content = content.child(row_divider(cx)).child(
                v_flex()
                    .gap_2()
                    .child(
                        div()
                            .text_sm()
                            .font_weight(FontWeight::SEMIBOLD)
                            .child(scene.name.clone()),
                    )
                    .child(
                        actions()
                            .child(
                                Button::new(format!("scene-select-{id_label}"))
                                    .small()
                                    .label(if active {
                                        self.text("使用中", "Active")
                                    } else {
                                        self.text("使用此场景", "Use Scene")
                                    })
                                    .when(active, |button| button.primary())
                                    .disabled(busy || active)
                                    .on_click(cx.listener(move |this, _, _, cx| {
                                        this.scene_request(
                                            "select_dictation_scene",
                                            json!({"id":id}),
                                            cx,
                                        );
                                    })),
                            )
                            .child(
                                Button::new(format!("scene-edit-{id_label}"))
                                    .small()
                                    .label(self.text("编辑", "Edit"))
                                    .disabled(busy || editing)
                                    .on_click(cx.listener(move |this, _, window, cx| {
                                        this.edit_scene(Some(scene.clone()), window, cx);
                                    })),
                            ),
                    ),
            );
            if let Some(editor) = &self.scene_editor
                && editor.scene.as_ref().is_some_and(|scene| scene.id == id)
            {
                content = content.child(self.scene_form(editor, busy, cx));
            }
        }
        titled(self.text("自定义听写场景", "Dictation Scenes"), content, cx)
    }

    fn scene_form(&self, editor: &Editor, busy: bool, cx: &mut Context<Self>) -> Div {
        let name = self.text("场景名称", "Scene name");
        let description = self.text("场景描述", "Scene description");
        let name_value = editor.name.read(cx).value();
        let description_value = editor.description.read(cx).value();
        let name_count = name_value.trim().chars().count();
        let description_count = description_value.trim().chars().count();
        let valid = (1..=NAME_LIMIT).contains(&name_count)
            && (1..=DESCRIPTION_LIMIT).contains(&description_count);
        let buttons = actions()
            .child(
                Button::new("scene-save")
                    .small()
                    .primary()
                    .label(self.text("保存场景", "Save Scene"))
                    .disabled(busy || !valid)
                    .on_click(cx.listener(|this, _, _, cx| {
                        if let Some(editor) = &this.scene_editor {
                            let params = json!({
                                "id":editor.scene.as_ref().map(|scene| scene.id),
                                "name":editor.name.read(cx).value().as_ref(),
                                "description":editor.description.read(cx).value().as_ref(),
                            });
                            this.scene_request("save_dictation_scene", params, cx);
                        }
                    })),
            )
            .child(
                Button::new("scene-cancel")
                    .small()
                    .label(self.text("取消", "Cancel"))
                    .disabled(busy)
                    .on_click(cx.listener(|this, _, _, cx| {
                        this.scene_editor = None;
                        cx.notify();
                    })),
            );
        let buttons = if let Some(scene) = &editor.scene {
            let id = scene.id;
            let confirm = editor.confirm_delete;
            buttons.child(
                Button::new("scene-delete")
                    .small()
                    .danger()
                    .label(if confirm {
                        self.text("确认删除", "Confirm Delete")
                    } else {
                        self.text("删除场景", "Delete Scene")
                    })
                    .disabled(busy)
                    .on_click(cx.listener(move |this, _, _, cx| {
                        if confirm {
                            this.scene_request("delete_dictation_scene", json!({"id":id}), cx);
                        } else if let Some(editor) = this.scene_editor.as_mut() {
                            editor.confirm_delete = true;
                            cx.notify();
                        }
                    })),
            )
        } else {
            buttons
        };
        v_flex()
            .gap_2()
            .child(note(format!("{name} · {name_count}/{NAME_LIMIT}"), cx))
            .child(Input::new(&editor.name).aria_label(name).disabled(busy))
            .child(note(
                format!("{description} · {description_count}/{DESCRIPTION_LIMIT}"),
                cx,
            ))
            .child(
                Textarea::new(&editor.description)
                    .aria_label(description)
                    .h(px(94.))
                    .disabled(busy),
            )
            .child(buttons)
    }
}
