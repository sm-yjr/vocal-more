// SPDX-License-Identifier: GPL-3.0-only
//! Personal Dictionary workbench: compact top auto-learning bar + three focused
//! segments (Terms, Suggestions, Privacy & File) with inline editing, search,
//! batch import, immediate delete with 8-second lossless restore, and preset
//! privacy exclusions.
use super::{
    Control, Settings,
    components::{
        Tone, badge, banner, card, localized_learning_reason, localized_learning_status,
        readable_unix_timestamp, status_dot,
    },
    pages::titled,
    schema::{self, get},
};
use gpui_kit::assets::IconName;
use gpui_kit::component::{
    ActiveTheme, Disableable, Icon, Selectable, Sizable,
    button::{Button, ButtonVariants},
    h_flex,
    input::{Input, InputEvent, InputState, Textarea, TextareaState},
    switch::Switch,
    v_flex,
};
use gpui_kit::prelude::FluentBuilder;
use gpui_kit::*;
use serde_json::{Value, json};
use std::time::Duration;

const PRIVACY_PRESETS: &[(&str, &str, &str)] = &[
    ("com.1password.1password", "1Password", "1Password"),
    ("com.bitwarden.desktop", "Bitwarden", "Bitwarden"),
    ("com.apple.keychainaccess", "钥匙串访问", "Keychain Access"),
    ("com.apple.Terminal", "终端", "Terminal"),
    ("com.googlecode.iterm2", "iTerm2", "iTerm2"),
    ("dev.warp.Warp-Stable", "Warp", "Warp"),
    ("com.tencent.xinWeChat", "微信", "WeChat"),
    ("com.electron.lark", "飞书", "Lark"),
];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum DictionaryTab {
    Terms,
    Suggestions,
    Privacy,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum LearningFilter {
    Review,
    Applied,
    History,
}

pub(super) struct TermEditor {
    pub old_term: String,
    pub original_aliases: Vec<String>,
    pub term: Entity<InputState>,
    pub aliases: Entity<InputState>,
    pub pending_request_id: Option<u64>,
}

pub(super) struct ReviewEditor {
    pub id: String,
    pub term: Entity<InputState>,
    pub aliases: Entity<InputState>,
    pub pending_request_id: Option<u64>,
}

pub(super) struct DeletedTermUndo {
    pub removed: Vec<Value>,
    pub label: String,
    pub epoch: u64,
    pub pending_restore_id: Option<u64>,
}

pub(super) struct State {
    pub sub_tab: DictionaryTab,
    pub search: Entity<InputState>,
    pub sort_alpha: bool,
    pub batch_open: bool,
    pub batch_input: Entity<TextareaState>,
    pub term_editor: Option<TermEditor>,
    pub review_editor: Option<ReviewEditor>,
    pub learning_filter: LearningFilter,
    pub learning_filter_explicit: bool,
    pub undo_deleted: Option<DeletedTermUndo>,
    pub undo_epoch: u64,
    pub notice: Option<(String, Tone, u64)>,
    pub notice_epoch: u64,
}

impl State {
    pub(super) fn new(window: &mut Window, cx: &mut Context<Settings>) -> Self {
        let search = cx.new(|cx| InputState::new(window, cx));
        let batch_input = cx.new(|cx| TextareaState::new(window, cx));
        cx.subscribe(&search, |_, _, _: &InputEvent, cx| cx.notify())
            .detach();
        cx.subscribe(&batch_input, |_, _, event: &InputEvent, cx| {
            if matches!(event, InputEvent::Change) {
                cx.notify();
            }
        })
        .detach();
        Self {
            sub_tab: DictionaryTab::Terms,
            search,
            sort_alpha: false,
            batch_open: false,
            batch_input,
            term_editor: None,
            review_editor: None,
            learning_filter: LearningFilter::Review,
            learning_filter_explicit: false,
            undo_deleted: None,
            undo_epoch: 0,
            notice: None,
            notice_epoch: 0,
        }
    }

    pub(super) fn sync_placeholders(
        &self,
        english: bool,
        window: &mut Window,
        cx: &mut Context<Settings>,
    ) {
        let search_placeholder = if english {
            "Search terms or aliases…"
        } else {
            "搜索词条或别名…"
        };
        self.search.update(cx, |input, cx| {
            input.set_placeholder(search_placeholder, window, cx)
        });
        let batch_placeholder = if english {
            "One term per line. Optional aliases after ':'\nKubernetes\nVocal More: vocal more, vocalmore"
        } else {
            "每行一条词条，可选用冒号填写误识别别名：\nKubernetes\n阿里云百炼: 阿里云白练, 阿里百炼"
        };
        self.batch_input.update(cx, |input, cx| {
            input.set_placeholder(batch_placeholder, window, cx)
        });
    }
}

#[cfg(feature = "ui-test")]
impl State {
    pub(super) fn testing_term_editor_ids(&self) -> Option<(ElementId, ElementId)> {
        let editor = self.term_editor.as_ref()?;
        Some((
            ("input", editor.term.entity_id()).into(),
            ("input", editor.aliases.entity_id()).into(),
        ))
    }

    pub(super) fn testing_batch_input_id(&self) -> ElementId {
        ("input", self.batch_input.entity_id()).into()
    }

    pub(super) fn testing_review_editor_ids(&self) -> Option<(ElementId, ElementId)> {
        let editor = self.review_editor.as_ref()?;
        Some((
            ("input", editor.term.entity_id()).into(),
            ("input", editor.aliases.entity_id()).into(),
        ))
    }
}

pub(super) fn parse_batch_lines(text: &str) -> Vec<(String, Vec<String>)> {
    let mut result: Vec<(String, Vec<String>)> = Vec::new();
    for raw_line in text.lines() {
        let line = raw_line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let (raw_term, raw_aliases) = split_batch_line(line);
        let term = raw_term.trim();
        if term.is_empty() {
            continue;
        }
        let aliases: Vec<String> = raw_aliases
            .map(|value| {
                value
                    .split([',', '，', '、', ';', '；'])
                    .map(str::trim)
                    .filter(|alias| !alias.is_empty() && *alias != term)
                    .fold(Vec::new(), |mut list, alias| {
                        if !list.iter().any(|existing| existing == alias) {
                            list.push(alias.to_owned());
                        }
                        list
                    })
            })
            .unwrap_or_default();
        if let Some((_, existing)) = result
            .iter_mut()
            .find(|(existing_term, _)| existing_term == term)
        {
            for alias in aliases {
                if !existing.contains(&alias) {
                    existing.push(alias);
                }
            }
        } else {
            result.push((term.to_owned(), aliases));
        }
    }
    result
}

fn split_batch_line(line: &str) -> (&str, Option<&str>) {
    let mut best: Option<(usize, usize)> = None;
    for sep in ["\t", "：", ":"] {
        if let Some(pos) = line.find(sep) {
            let candidate = (pos, sep.len());
            if best.is_none_or(|(best_pos, _)| pos < best_pos) {
                best = Some(candidate);
            }
        }
    }
    if let Some((pos, len)) = best {
        (&line[..pos], Some(&line[pos + len..]))
    } else {
        (line, None)
    }
}

pub(super) fn preview_batch_counts(
    parsed: &[(String, Vec<String>)],
    existing: &[Value],
) -> (usize, usize) {
    let mut new_terms = 0usize;
    let mut updated_terms = 0usize;
    for (term, aliases) in parsed {
        if let Some(entry) = existing
            .iter()
            .find(|entry| entry["term"].as_str() == Some(term.as_str()))
        {
            let existing_aliases = entry["aliases"]
                .as_array()
                .map(|list| list.iter().filter_map(Value::as_str).collect::<Vec<_>>())
                .unwrap_or_default();
            if aliases
                .iter()
                .any(|alias| !existing_aliases.contains(&alias.as_str()))
            {
                updated_terms += 1;
            }
        } else {
            new_terms += 1;
        }
    }
    (new_terms, updated_terms)
}

pub(super) fn remove_alias_from_csv(raw: &str, target: &str) -> String {
    raw.split([',', '，', '\n'])
        .map(str::trim)
        .filter(|alias| !alias.is_empty() && *alias != target)
        .collect::<Vec<_>>()
        .join(", ")
}

pub(super) fn merge_excluded_ids(current_input: &str, toggle_bundle_id: &str) -> Vec<String> {
    let mut items: Vec<String> = Vec::new();
    for piece in current_input
        .split([',', '，', '\n'])
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        if !items.iter().any(|existing| existing == piece) {
            items.push(piece.to_owned());
            if items.len() == 100 {
                break;
            }
        }
    }
    if let Some(pos) = items.iter().position(|item| item == toggle_bundle_id) {
        items.remove(pos);
    } else if items.len() < 100 {
        items.push(toggle_bundle_id.to_owned());
    }
    items
}

pub(super) fn preset_label(bundle_id: &str, english: bool) -> Option<&'static str> {
    PRIVACY_PRESETS
        .iter()
        .find(|(id, _, _)| *id == bundle_id)
        .map(|(_, zh, en)| if english { *en } else { *zh })
}

fn entry_aliases(entry: &Value) -> Vec<String> {
    entry["aliases"]
        .as_array()
        .map(|list| {
            list.iter()
                .filter_map(Value::as_str)
                .map(str::to_owned)
                .collect()
        })
        .unwrap_or_default()
}

fn is_actionable_review(record: &Value) -> bool {
    record["status"].as_str() == Some("review")
        && record["term"]
            .as_str()
            .is_some_and(|term| !term.trim().is_empty())
}

fn is_applied_record(record: &Value) -> bool {
    matches!(record["status"].as_str(), Some("applied" | "approved"))
}

fn is_clearable_history_record(record: &Value) -> bool {
    matches!(
        record["status"].as_str(),
        Some("ignored" | "reverted" | "failed")
    )
}

impl Settings {
    pub(super) fn show_dictionary_notice(
        &mut self,
        message: impl Into<String>,
        tone: Tone,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        self.dict.notice_epoch = self.dict.notice_epoch.wrapping_add(1);
        let epoch = self.dict.notice_epoch;
        self.dict.notice = Some((message.into(), tone, epoch));
        cx.notify();
        cx.spawn_in(window, async move |this, cx| {
            cx.background_executor().timer(Duration::from_secs(4)).await;
            _ = this.update_in(cx, |this, _, cx| {
                if this
                    .dict
                    .notice
                    .as_ref()
                    .is_some_and(|(_, _, current)| *current == epoch)
                {
                    this.dict.notice = None;
                    cx.notify();
                }
            });
        })
        .detach();
    }

    pub(super) fn start_dictionary_undo(
        &mut self,
        removed: Vec<Value>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let Some(first_term) = removed.first().and_then(|item| item["term"].as_str()) else {
            return;
        };
        let label = first_term.to_owned();
        self.dict.undo_epoch = self.dict.undo_epoch.wrapping_add(1);
        let epoch = self.dict.undo_epoch;
        self.dict.undo_deleted = Some(DeletedTermUndo {
            removed,
            label,
            epoch,
            pending_restore_id: None,
        });
        cx.notify();
        cx.spawn_in(window, async move |this, cx| {
            cx.background_executor().timer(Duration::from_secs(8)).await;
            _ = this.update_in(cx, |this, _, cx| {
                if this
                    .dict
                    .undo_deleted
                    .as_ref()
                    .is_some_and(|undo| undo.epoch == epoch)
                {
                    this.dict.undo_deleted = None;
                    cx.notify();
                }
            });
        })
        .detach();
    }

    pub(super) fn submit_quick_add_term(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let term = self.term.read(cx).value().trim().to_owned();
        if term.is_empty() {
            return;
        }
        let aliases = schema::list_from_text(&self.aliases.read(cx).value());
        if self.action(
            "addDictEntry",
            json!({"term": term, "aliases": aliases}),
            cx,
        ) == 0
        {
            return;
        }
        self.term
            .update(cx, |input, cx| input.set_value("", window, cx));
        self.aliases
            .update(cx, |input, cx| input.set_value("", window, cx));
    }

    fn begin_term_edit(
        &mut self,
        term_value: String,
        aliases_value: Vec<String>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let term_input = cx.new(|cx| {
            InputState::new(window, cx)
                .default_value(term_value.clone())
                .placeholder(self.text("标准词条", "Standard term"))
        });
        let aliases_text = aliases_value.join(", ");
        let aliases_input = cx.new(|cx| {
            InputState::new(window, cx)
                .default_value(aliases_text)
                .placeholder(self.text(
                    "误识别别名，逗号分隔（选填）",
                    "Misrecognition aliases, comma-separated",
                ))
        });
        cx.subscribe_in(
            &term_input,
            window,
            |this, _, event: &InputEvent, _, cx| match event {
                InputEvent::Change => cx.notify(),
                InputEvent::PressEnter { .. } => this.submit_term_edit(cx),
                _ => {}
            },
        )
        .detach();
        cx.subscribe_in(
            &aliases_input,
            window,
            |this, _, event: &InputEvent, _, cx| match event {
                InputEvent::Change => cx.notify(),
                InputEvent::PressEnter { .. } => this.submit_term_edit(cx),
                _ => {}
            },
        )
        .detach();
        self.dict.term_editor = Some(TermEditor {
            old_term: term_value,
            original_aliases: aliases_value,
            term: term_input,
            aliases: aliases_input,
            pending_request_id: None,
        });
        cx.notify();
    }

    fn submit_term_edit(&mut self, cx: &mut Context<Self>) {
        let Some(editor) = &self.dict.term_editor else {
            return;
        };
        if editor.pending_request_id.is_some() {
            return;
        }
        let new_term = editor.term.read(cx).value().trim().to_owned();
        if new_term.is_empty() {
            return;
        }
        let aliases = schema::list_from_text(&editor.aliases.read(cx).value());
        let old_term = editor.old_term.clone();
        let expected_aliases = editor.original_aliases.clone();
        let request = self.action(
            "updateDictEntry",
            json!({
                "old_term": old_term,
                "term": new_term,
                "aliases": aliases,
                "expected_aliases": expected_aliases,
            }),
            cx,
        );
        if request != 0
            && let Some(editor) = self.dict.term_editor.as_mut()
        {
            editor.pending_request_id = Some(request);
            cx.notify();
        }
    }

    fn begin_review_edit(
        &mut self,
        id: String,
        term_value: String,
        aliases_value: String,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let term_input = cx.new(|cx| {
            InputState::new(window, cx)
                .default_value(term_value)
                .placeholder(self.text("标准词条", "Standard term"))
        });
        let aliases_input = cx.new(|cx| {
            InputState::new(window, cx)
                .default_value(aliases_value)
                .placeholder(self.text(
                    "误识别别名，逗号分隔",
                    "Misrecognition aliases, comma-separated",
                ))
        });
        cx.subscribe_in(
            &term_input,
            window,
            |this, _, event: &InputEvent, _, cx| match event {
                InputEvent::Change => cx.notify(),
                InputEvent::PressEnter { .. } => this.submit_review_edit(cx),
                _ => {}
            },
        )
        .detach();
        cx.subscribe_in(
            &aliases_input,
            window,
            |this, _, event: &InputEvent, _, cx| match event {
                InputEvent::Change => cx.notify(),
                InputEvent::PressEnter { .. } => this.submit_review_edit(cx),
                _ => {}
            },
        )
        .detach();
        self.dict.review_editor = Some(ReviewEditor {
            id,
            term: term_input,
            aliases: aliases_input,
            pending_request_id: None,
        });
        cx.notify();
    }

    fn submit_review_edit(&mut self, cx: &mut Context<Self>) {
        let Some(editor) = &self.dict.review_editor else {
            return;
        };
        if editor.pending_request_id.is_some() {
            return;
        }
        let term = editor.term.read(cx).value().trim().to_owned();
        if term.is_empty() {
            return;
        }
        let aliases = schema::list_from_text(&editor.aliases.read(cx).value());
        let id = editor.id.clone();
        let request = self.action(
            "approveDictionaryLearning",
            json!({"id": id, "term": term, "aliases": aliases}),
            cx,
        );
        if request != 0
            && let Some(editor) = self.dict.review_editor.as_mut()
        {
            editor.pending_request_id = Some(request);
            cx.notify();
        }
    }

    fn toggle_excluded_bundle_id(
        &mut self,
        bundle_id: &str,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let current_draft = match self.controls.get("dictionary_learning.excluded_bundle_ids") {
            Some(Control::Input(input)) => input.read(cx).value().to_string(),
            _ => schema::display_value(get(
                self.config(),
                "dictionary_learning.excluded_bundle_ids",
            )),
        };
        let next = merge_excluded_ids(&current_draft, bundle_id);
        let formatted = next.join(", ");
        if let Some(Control::Input(input)) =
            self.controls.get("dictionary_learning.excluded_bundle_ids")
        {
            input.update(cx, |input, cx| input.set_value(formatted, window, cx));
        }
        self.set_config(
            "dictionary_learning.excluded_bundle_ids",
            json!(next),
            window,
            cx,
        );
    }

    fn current_excluded_ids(&self, cx: &Context<Self>) -> Vec<String> {
        let raw = match self.controls.get("dictionary_learning.excluded_bundle_ids") {
            Some(Control::Input(input)) => input.read(cx).value().to_string(),
            _ => schema::display_value(get(
                self.config(),
                "dictionary_learning.excluded_bundle_ids",
            )),
        };
        raw.split([',', '，', '\n'])
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .fold(Vec::new(), |mut list, item| {
                if list.len() < 100 && !list.iter().any(|existing| existing == item) {
                    list.push(item.to_owned());
                }
                list
            })
    }

    pub(super) fn on_dictionary_rpc_response(
        &mut self,
        request_id: u64,
        action: &str,
        result: &Value,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        match action {
            "updateDictEntry" => {
                if self
                    .dict
                    .term_editor
                    .as_ref()
                    .is_some_and(|editor| editor.pending_request_id == Some(request_id))
                {
                    self.dict.term_editor = None;
                }
                let message = self.text("词条已更新", "Term updated").to_owned();
                self.show_dictionary_notice(message, Tone::Success, window, cx);
            }
            "batchAddDictEntries" => {
                self.dict.batch_open = false;
                self.dict
                    .batch_input
                    .update(cx, |input, cx| input.set_value("", window, cx));
                let added = result["summary"]["added_terms"].as_u64().unwrap_or(0);
                let updated = result["summary"]["updated_terms"].as_u64().unwrap_or(0);
                let message = if self.english() {
                    format!("Imported {added} new, updated {updated}")
                } else {
                    format!("已导入 {added} 条新词，更新 {updated} 条已有词条")
                };
                self.show_dictionary_notice(message, Tone::Success, window, cx);
            }
            "removeDictEntry" => {
                if let Some(removed) = result["removed"].as_array()
                    && !removed.is_empty()
                {
                    if let Some(editor) = &self.dict.term_editor
                        && removed
                            .iter()
                            .any(|item| item["term"].as_str() == Some(editor.old_term.as_str()))
                    {
                        self.dict.term_editor = None;
                    }
                    self.start_dictionary_undo(removed.clone(), window, cx);
                }
            }
            "restoreDictEntries" => {
                if self
                    .dict
                    .undo_deleted
                    .as_ref()
                    .is_some_and(|undo| undo.pending_restore_id == Some(request_id))
                {
                    self.dict.undo_deleted = None;
                }
                let message = self
                    .text("已恢复词条与别名", "Restored term and aliases")
                    .to_owned();
                self.show_dictionary_notice(message, Tone::Success, window, cx);
            }
            "reloadDict" => {
                let count = result["count"].as_u64().unwrap_or(0);
                let message = if self.english() {
                    format!("Reloaded {count} terms from dictionary.yaml")
                } else {
                    format!("已从本地文件重新加载 {count} 条词条")
                };
                self.show_dictionary_notice(message, Tone::Success, window, cx);
            }
            "approveDictionaryLearning" => {
                let ok = result["ok"] == true;
                if let Some(editor) = self.dict.review_editor.as_mut()
                    && editor.pending_request_id == Some(request_id)
                {
                    if ok {
                        self.dict.review_editor = None;
                    } else {
                        editor.pending_request_id = None;
                    }
                }
                if ok {
                    let message = self
                        .text("已加入个人词典", "Added to dictionary")
                        .to_owned();
                    self.show_dictionary_notice(message, Tone::Success, window, cx);
                } else {
                    let message = self
                        .text(
                            "该建议已处理或已失效，请刷新列表",
                            "This suggestion is no longer pending review",
                        )
                        .to_owned();
                    self.show_dictionary_notice(message, Tone::Warning, window, cx);
                }
            }
            "clearDictionaryLearningHistory" => {
                let cleared = result["cleared"].as_u64().unwrap_or(0);
                let message = if self.english() {
                    format!("Cleared {cleared} history records")
                } else {
                    format!("已清理 {cleared} 条历史记录")
                };
                self.show_dictionary_notice(message, Tone::Success, window, cx);
            }
            _ => {}
        }
    }

    pub(super) fn on_dictionary_rpc_error(&mut self, request_id: u64, action: Option<&str>) {
        if let Some(editor) = self.dict.term_editor.as_mut()
            && (editor.pending_request_id == Some(request_id) || action == Some("updateDictEntry"))
        {
            editor.pending_request_id = None;
        }
        if let Some(editor) = self.dict.review_editor.as_mut()
            && (editor.pending_request_id == Some(request_id)
                || action == Some("approveDictionaryLearning"))
        {
            editor.pending_request_id = None;
        }
        if let Some(undo) = self.dict.undo_deleted.as_mut()
            && (undo.pending_restore_id == Some(request_id) || action == Some("restoreDictEntries"))
        {
            undo.pending_restore_id = None;
        }
    }

    pub(super) fn dictionary_page(&self, cx: &mut Context<Self>) -> Div {
        let entries = self.array("dictionary");
        let records = self.array("dictionary_learning_records");
        let review_count = records
            .iter()
            .filter(|record| is_actionable_review(record))
            .count();
        let excluded_count = self.current_excluded_ids(cx).len();
        let learning_on = get(self.config(), "dictionary_learning.enabled") == true;

        // 1. Compact shared Auto-Learning control strip
        let top_bar = card(cx).py_2p5().px_4().gap_0().child(
            h_flex()
                .w_full()
                .min_w_0()
                .justify_between()
                .items_center()
                .gap_3()
                .when(self.narrow, |row| row.flex_col().items_start().gap_2())
                .child(
                    v_flex()
                        .flex_1()
                        .min_w_0()
                        .gap_0p5()
                        .child(
                            h_flex()
                                .items_center()
                                .gap_2()
                                .child(status_dot(
                                    if learning_on {
                                        Tone::Success
                                    } else {
                                        Tone::Neutral
                                    },
                                    cx,
                                ))
                                .child(
                                    div()
                                        .text_sm()
                                        .font_weight(FontWeight::MEDIUM)
                                        .child(self.text("启用自动学习", "Enable automatic learning")),
                                ),
                        )
                        .child(
                            div()
                                .text_xs()
                                .whitespace_normal()
                                .text_color(cx.theme().muted_foreground)
                                .child(self.text(
                                    "本地观察粘贴后的手动修正，自动跳过密码框与隐私排除应用。",
                                    "Observes local post-paste edits; skips password fields and excluded apps.",
                                )),
                        ),
                )
                .child(
                    div().flex_shrink_0().child(
                        Switch::new("dictionary_learning.enabled")
                            .accessibility_label(
                                self.text("自动学习词典", "Automatic dictionary learning"),
                            )
                            .checked(learning_on)
                            .on_change(cx.listener(|this, checked, window, cx| {
                                this.set_config(
                                    "dictionary_learning.enabled",
                                    json!(checked),
                                    window,
                                    cx,
                                )
                            })),
                    ),
                ),
        );

        // 2. Segmented control bar (Terms / Suggestions / Privacy & File)
        let segment = |id: &'static str,
                       tab: DictionaryTab,
                       label: String,
                       count_badge: Option<(String, Tone)>,
                       cx: &mut Context<Self>| {
            let active = self.dict.sub_tab == tab;
            let mut inner = h_flex().items_center().justify_center().gap_1p5().child(
                div()
                    .text_xs()
                    .font_weight(if active {
                        FontWeight::SEMIBOLD
                    } else {
                        FontWeight::MEDIUM
                    })
                    .child(label),
            );
            if let Some((text, tone)) = count_badge {
                inner = inner.child(badge(text, tone, cx));
            }
            Button::new(id)
                .small()
                .ghost()
                .flex_1()
                .rounded(cx.theme().radius)
                .selected(active)
                .when(active, |button| {
                    button
                        .bg(cx.theme().group_box)
                        .border_1()
                        .border_color(cx.theme().border)
                })
                .on_click(cx.listener(move |this, _, _, cx| {
                    this.dict.sub_tab = tab;
                    cx.notify();
                }))
                .child(inner)
        };

        let segmented_bar = h_flex()
            .w_full()
            .min_w_0()
            .p_1()
            .gap_1()
            .rounded(cx.theme().radius_lg)
            .bg(cx.theme().secondary.opacity(0.65))
            .border_1()
            .border_color(cx.theme().border.opacity(0.75))
            .child(segment(
                "dict-tab-terms",
                DictionaryTab::Terms,
                format!("{} · {}", self.text("词条库", "Terms"), entries.len()),
                None,
                cx,
            ))
            .child(segment(
                "dict-tab-suggestions",
                DictionaryTab::Suggestions,
                self.text("学习建议", "Suggestions").to_owned(),
                if review_count > 0 {
                    Some((format!("{review_count}"), Tone::Primary))
                } else {
                    None
                },
                cx,
            ))
            .child(segment(
                "dict-tab-privacy",
                DictionaryTab::Privacy,
                if excluded_count > 0 {
                    format!(
                        "{} · {}",
                        self.text("隐私与文件", "Privacy & File"),
                        excluded_count
                    )
                } else {
                    self.text("隐私与文件", "Privacy & File").to_owned()
                },
                None,
                cx,
            ));

        let mut page = v_flex()
            .w_full()
            .min_w_0()
            .gap_3()
            .child(titled(
                self.text("自动学习词典", "Automatic Dictionary Learning"),
                top_bar,
                cx,
            ))
            .child(segmented_bar);

        if let Some((message, tone, _)) = &self.dict.notice {
            page = page.child(banner(message.clone(), *tone, cx));
        }

        let body = match self.dict.sub_tab {
            DictionaryTab::Terms => self.dictionary_terms_view(cx),
            DictionaryTab::Suggestions => self.dictionary_suggestions_view(cx),
            DictionaryTab::Privacy => self.dictionary_privacy_view(cx),
        };

        page.child(body)
    }

    fn dictionary_terms_view(&self, cx: &mut Context<Self>) -> Div {
        let entries = self.array("dictionary");
        let query_raw = self.dict.search.read(cx).value().to_string();
        let query = query_raw.trim().to_lowercase();
        let draft_term = self.term.read(cx).value().trim().to_owned();
        let draft_exists = !draft_term.is_empty()
            && entries
                .iter()
                .any(|entry| entry["term"].as_str() == Some(draft_term.as_str()));

        // Toolbar: search + sort + batch toggle
        let mut search_box = h_flex().flex_1().min_w_0().items_center().gap_1p5().child(
            div().flex_1().min_w_0().child(
                Input::new(&self.dict.search)
                    .id("dictionary-search")
                    .aria_label(self.text("搜索词条或别名", "Search terms or aliases")),
            ),
        );
        if !query_raw.is_empty() {
            search_box = search_box.child(
                Button::new("dictionary-search-clear")
                    .xsmall()
                    .ghost()
                    .icon(IconName::X)
                    .accessibility_label(self.text("清除搜索", "Clear search"))
                    .on_click(cx.listener(|this, _, window, cx| {
                        this.dict
                            .search
                            .update(cx, |input, cx| input.set_value("", window, cx));
                        cx.notify();
                    })),
            );
        }

        let toolbar = h_flex()
            .w_full()
            .min_w_0()
            .gap_2()
            .items_center()
            .when(self.narrow, |row| row.flex_col().items_stretch())
            .child(search_box)
            .child(
                h_flex()
                    .gap_1p5()
                    .flex_shrink_0()
                    .child(
                        Button::new("dictionary-sort")
                            .small()
                            .ghost()
                            .icon(IconName::ArrowUpDown)
                            .selected(self.dict.sort_alpha)
                            .label(if self.dict.sort_alpha {
                                self.text("A-Z", "A-Z")
                            } else {
                                self.text("默认顺序", "Default order")
                            })
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.dict.sort_alpha = !this.dict.sort_alpha;
                                cx.notify();
                            })),
                    )
                    .child(
                        Button::new("dictionary-batch-toggle")
                            .small()
                            .icon(IconName::ListPlus)
                            .selected(self.dict.batch_open)
                            .label(self.text("批量添加", "Batch add"))
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.dict.batch_open = !this.dict.batch_open;
                                cx.notify();
                            })),
                    ),
            );

        // Compact inline Quick-Add strip (no nested heavy box, Enter submits)
        let quick_add_row = h_flex()
            .w_full()
            .min_w_0()
            .gap_2()
            .items_center()
            .when(self.narrow, |row| row.flex_col().items_stretch())
            .child(
                div().flex_1().min_w_0().child(
                    Input::new(&self.term)
                        .id("dictionary-term")
                        .aria_label(self.text("标准词条", "Standard term")),
                ),
            )
            .child(
                div().flex_1().min_w_0().child(
                    Input::new(&self.aliases)
                        .id("dictionary-aliases")
                        .aria_label(self.text("误识别别名，逗号分隔", "Aliases, comma-separated")),
                ),
            )
            .child(
                Button::new("add-term")
                    .small()
                    .primary()
                    .icon(IconName::Plus)
                    .disabled(draft_term.is_empty())
                    .label(if draft_exists {
                        self.text("追加别名", "Merge aliases")
                    } else {
                        self.text("添加词条", "Add term")
                    })
                    .on_click(cx.listener(|this, _, window, cx| {
                        this.submit_quick_add_term(window, cx);
                    })),
            );

        let mut quick_add = v_flex().w_full().min_w_0().gap_1p5().child(quick_add_row);
        if draft_exists {
            quick_add = quick_add.child(div().text_xs().text_color(cx.theme().primary).child(
                self.text(
                    "词条已存在：按回车或点击“追加别名”将合并新别名。",
                    "Term already exists: submitting will merge any new aliases.",
                ),
            ));
        }

        let mut container = v_flex().w_full().min_w_0().gap_3().child(toolbar);

        // Collapsible batch import card
        if self.dict.batch_open {
            let batch_text = self.dict.batch_input.read(cx).value().to_string();
            let parsed = parse_batch_lines(&batch_text);
            let (new_count, updated_count) = preview_batch_counts(&parsed, entries);
            let total_parsed = parsed.len();
            let can_import = new_count > 0 || updated_count > 0;
            let preview = if total_parsed == 0 {
                self.text(
                    "每行一条：纯词条或「标准词条: 别名1, 别名2」",
                    "One per line: term or 'Term: alias1, alias2'",
                )
                .to_owned()
            } else if self.english() {
                format!(
                    "Parsed {total_parsed} entries ({new_count} new, {updated_count} alias updates)"
                )
            } else {
                format!(
                    "已识别 {total_parsed} 条（{new_count} 条新增，{updated_count} 条追加别名）"
                )
            };
            let batch_card = card(cx)
                .gap_2()
                .child(
                    h_flex()
                        .justify_between()
                        .items_center()
                        .gap_2()
                        .flex_wrap()
                        .child(
                            div()
                                .text_xs()
                                .font_weight(FontWeight::SEMIBOLD)
                                .child(self.text("批量导入词条", "Batch Import Terms")),
                        )
                        .child(
                            div()
                                .text_xs()
                                .text_color(cx.theme().muted_foreground)
                                .child(preview),
                        ),
                )
                .child(
                    Textarea::new(&self.dict.batch_input)
                        .aria_label(self.text("批量词条列表", "Batch terms list"))
                        .h(px(108.)),
                )
                .child(
                    h_flex()
                        .gap_2()
                        .child(
                            Button::new("dictionary-batch-import")
                                .small()
                                .primary()
                                .disabled(!can_import)
                                .label(self.text("导入词条", "Import terms"))
                                .on_click(cx.listener(|this, _, _, cx| {
                                    let raw = this.dict.batch_input.read(cx).value().to_string();
                                    let parsed = parse_batch_lines(&raw);
                                    if parsed.is_empty() {
                                        return;
                                    }
                                    let payload: Vec<Value> = parsed
                                        .into_iter()
                                        .map(|(term, aliases)| {
                                            json!({"term": term, "aliases": aliases})
                                        })
                                        .collect();
                                    this.action(
                                        "batchAddDictEntries",
                                        json!({"entries": payload}),
                                        cx,
                                    );
                                })),
                        )
                        .child(
                            Button::new("dictionary-batch-cancel")
                                .small()
                                .ghost()
                                .label(self.text("取消", "Cancel"))
                                .on_click(cx.listener(|this, _, _, cx| {
                                    this.dict.batch_open = false;
                                    cx.notify();
                                })),
                        ),
                );
            container = container.child(batch_card);
        }

        // 8-second lossless undo banner after delete
        if let Some(undo) = &self.dict.undo_deleted {
            let undo_entries = undo.removed.clone();
            let undo_text = if self.english() {
                format!("Removed \"{}\". Restore within 8 seconds.", undo.label)
            } else {
                format!("已移除词条「{}」，8 秒内可一键原位恢复。", undo.label)
            };
            container = container.child(
                h_flex()
                    .w_full()
                    .min_w_0()
                    .justify_between()
                    .items_center()
                    .gap_2()
                    .flex_wrap()
                    .px_3()
                    .py_2p5()
                    .rounded(cx.theme().radius)
                    .bg(cx.theme().yellow_light)
                    .border_1()
                    .border_color(cx.theme().yellow.opacity(0.4))
                    .child(
                        div()
                            .flex_1()
                            .min_w_0()
                            .text_xs()
                            .whitespace_normal()
                            .child(undo_text),
                    )
                    .child(
                        h_flex()
                            .gap_1p5()
                            .flex_shrink_0()
                            .child(
                                Button::new("dictionary-undo-delete")
                                    .xsmall()
                                    .primary()
                                    .disabled(undo.pending_restore_id.is_some())
                                    .label(self.text("撤销移除", "Undo remove"))
                                    .on_click(cx.listener(move |this, _, _, cx| {
                                        let request = this.action(
                                            "restoreDictEntries",
                                            json!({"entries": undo_entries}),
                                            cx,
                                        );
                                        if request != 0
                                            && let Some(undo) = this.dict.undo_deleted.as_mut()
                                        {
                                            undo.pending_restore_id = Some(request);
                                            cx.notify();
                                        }
                                    })),
                            )
                            .child(
                                Button::new("dictionary-dismiss-undo")
                                    .xsmall()
                                    .ghost()
                                    .icon(IconName::X)
                                    .accessibility_label(self.text("关闭提示", "Dismiss"))
                                    .on_click(cx.listener(|this, _, _, cx| {
                                        this.dict.undo_deleted = None;
                                        cx.notify();
                                    })),
                            ),
                    ),
            );
        }

        // Main Terms card: inline quick-add at top + filtered/sorted list below
        let mut terms_card = card(cx).gap_3().child(quick_add);

        let mut visible: Vec<(usize, &Value)> = entries
            .iter()
            .enumerate()
            .filter(|(_, entry)| {
                if query.is_empty() {
                    return true;
                }
                let term = entry["term"].as_str().unwrap_or_default().to_lowercase();
                if term.contains(&query) {
                    return true;
                }
                entry_aliases(entry)
                    .iter()
                    .any(|alias| alias.to_lowercase().contains(&query))
            })
            .collect();
        if self.dict.sort_alpha {
            visible.sort_by(|(_, a), (_, b)| {
                a["term"]
                    .as_str()
                    .unwrap_or_default()
                    .to_lowercase()
                    .cmp(&b["term"].as_str().unwrap_or_default().to_lowercase())
            });
        }

        if entries.is_empty() {
            terms_card = terms_card.child(
                v_flex()
                    .w_full()
                    .min_w_0()
                    .gap_1()
                    .pt_3()
                    .border_t_1()
                    .border_color(cx.theme().border.opacity(0.7))
                    .child(
                        div()
                            .text_xs()
                            .font_weight(FontWeight::MEDIUM)
                            .child(self.text("还没有自定义词条", "No custom terms yet")),
                    )
                    .child(
                        div()
                            .text_xs()
                            .whitespace_normal()
                            .text_color(cx.theme().muted_foreground)
                            .child(self.text(
                                "在上方输入专有名词、人名或项目代号并按回车即可添加；误识别别名可留空，单独词条也能提升语音识别命中率。",
                                "Type a domain term, name, or acronym above and press Enter. Aliases are optional—standalone terms still boost recognition.",
                            )),
                    ),
            );
        } else if visible.is_empty() {
            let add_query = query_raw.trim().to_owned();
            let add_label = if self.english() {
                format!("Add \"{add_query}\"")
            } else {
                format!("添加「{add_query}」到词典")
            };
            terms_card = terms_card.child(
                h_flex()
                    .w_full()
                    .min_w_0()
                    .justify_between()
                    .items_center()
                    .gap_2()
                    .flex_wrap()
                    .pt_3()
                    .border_t_1()
                    .border_color(cx.theme().border.opacity(0.7))
                    .child(
                        div()
                            .text_xs()
                            .text_color(cx.theme().muted_foreground)
                            .child(if self.english() {
                                format!("No matching terms for \"{add_query}\".")
                            } else {
                                format!("未找到匹配「{add_query}」的词条。")
                            }),
                    )
                    .child(
                        Button::new("dictionary-quick-add-search")
                            .xsmall()
                            .primary()
                            .icon(IconName::Plus)
                            .label(add_label)
                            .on_click(cx.listener(move |this, _, window, cx| {
                                if this.action(
                                    "addDictEntry",
                                    json!({"term": add_query, "aliases": []}),
                                    cx,
                                ) != 0
                                {
                                    this.dict
                                        .search
                                        .update(cx, |input, cx| input.set_value("", window, cx));
                                }
                            })),
                    ),
            );
        } else {
            let mut list = v_flex()
                .w_full()
                .min_w_0()
                .border_t_1()
                .border_color(cx.theme().border.opacity(0.7));

            for (row_idx, (orig_index, entry)) in visible.into_iter().enumerate() {
                let term = entry["term"].as_str().unwrap_or_default().to_owned();
                let aliases = entry_aliases(entry);
                let is_editing = self
                    .dict
                    .term_editor
                    .as_ref()
                    .is_some_and(|editor| editor.old_term == term);

                if is_editing && let Some(editor) = &self.dict.term_editor {
                    list = list.child(self.dictionary_term_editor_row(row_idx, editor, cx));
                    continue;
                }

                let edit_term = term.clone();
                let edit_aliases = aliases.clone();
                let remove_term = term.clone();
                let mut meta = h_flex()
                    .flex_1()
                    .min_w_0()
                    .items_center()
                    .gap_2()
                    .flex_wrap()
                    .child(div().text_sm().font_weight(FontWeight::MEDIUM).child(term));
                if aliases.is_empty() {
                    meta = meta.child(
                        div()
                            .text_xs()
                            .text_color(cx.theme().muted_foreground)
                            .child(self.text("热词提升", "Hotword")),
                    );
                } else {
                    meta = meta
                        .child(
                            div()
                                .text_xs()
                                .text_color(cx.theme().muted_foreground)
                                .child("←"),
                        )
                        .children(
                            aliases
                                .into_iter()
                                .map(|alias| badge(alias, Tone::Neutral, cx)),
                        );
                }

                list = list.child(
                    h_flex()
                        .w_full()
                        .min_w_0()
                        .justify_between()
                        .items_center()
                        .gap_3()
                        .py_2()
                        .when(row_idx > 0, |row| {
                            row.border_t_1()
                                .border_color(cx.theme().border.opacity(0.55))
                        })
                        .child(meta)
                        .child(
                            h_flex()
                                .gap_1()
                                .flex_shrink_0()
                                .child(
                                    Button::new(("edit-term", orig_index))
                                        .xsmall()
                                        .ghost()
                                        .icon(IconName::Pencil)
                                        .label(self.text("编辑", "Edit"))
                                        .on_click(cx.listener(move |this, _, window, cx| {
                                            this.begin_term_edit(
                                                edit_term.clone(),
                                                edit_aliases.clone(),
                                                window,
                                                cx,
                                            );
                                        })),
                                )
                                .child(
                                    Button::new(("remove-term", orig_index))
                                        .xsmall()
                                        .ghost()
                                        .label(self.text("移除", "Remove"))
                                        .on_click(cx.listener(move |this, _, _, cx| {
                                            this.action(
                                                "removeDictEntry",
                                                json!({"term": remove_term}),
                                                cx,
                                            );
                                        })),
                                ),
                        ),
                );
            }
            terms_card = terms_card.child(list);
        }

        container.child(terms_card)
    }

    fn dictionary_term_editor_row(
        &self,
        row_idx: usize,
        editor: &TermEditor,
        cx: &mut Context<Self>,
    ) -> Div {
        let busy = editor.pending_request_id.is_some();
        let term_draft = editor.term.read(cx).value().trim().to_owned();
        let aliases_draft = editor.aliases.read(cx).value().to_string();
        let parsed_aliases: Vec<String> = aliases_draft
            .split([',', '，', '\n'])
            .map(str::trim)
            .filter(|alias| !alias.is_empty() && *alias != term_draft.as_str())
            .fold(Vec::new(), |mut list, alias| {
                if !list.iter().any(|existing| existing == alias) {
                    list.push(alias.to_owned());
                }
                list
            });

        let mut form = v_flex()
            .w_full()
            .min_w_0()
            .gap_2()
            .my_1()
            .p_3()
            .rounded(cx.theme().radius)
            .bg(cx.theme().secondary.opacity(0.5))
            .border_1()
            .border_color(cx.theme().primary.opacity(0.35))
            .when(row_idx > 0, |row| row.mt_2())
            .child(
                h_flex()
                    .w_full()
                    .min_w_0()
                    .gap_2()
                    .when(self.narrow, |row| row.flex_col())
                    .child(
                        v_flex()
                            .flex_1()
                            .min_w_0()
                            .gap_1()
                            .child(
                                div()
                                    .text_xs()
                                    .font_weight(FontWeight::MEDIUM)
                                    .text_color(cx.theme().muted_foreground)
                                    .child(self.text("标准词条", "Standard term")),
                            )
                            .child(
                                Input::new(&editor.term)
                                    .aria_label(self.text("编辑标准词条", "Edit standard term"))
                                    .disabled(busy),
                            ),
                    )
                    .child(
                        v_flex()
                            .flex_1()
                            .min_w_0()
                            .gap_1()
                            .child(
                                div()
                                    .text_xs()
                                    .font_weight(FontWeight::MEDIUM)
                                    .text_color(cx.theme().muted_foreground)
                                    .child(self.text(
                                        "误识别别名（逗号分隔）",
                                        "Aliases (comma-separated)",
                                    )),
                            )
                            .child(
                                Input::new(&editor.aliases)
                                    .aria_label(self.text("编辑别名", "Edit aliases"))
                                    .disabled(busy),
                            ),
                    ),
            );

        if !parsed_aliases.is_empty() {
            form =
                form.child(
                    h_flex()
                        .w_full()
                        .min_w_0()
                        .gap_1p5()
                        .flex_wrap()
                        .items_center()
                        .children(parsed_aliases.into_iter().enumerate().map(
                            |(alias_idx, alias)| {
                                let remove_target = alias.clone();
                                h_flex()
                                    .items_center()
                                    .gap_1()
                                    .pl_2()
                                    .pr_1()
                                    .py_0p5()
                                    .rounded_full()
                                    .bg(cx.theme().group_box)
                                    .border_1()
                                    .border_color(cx.theme().border)
                                    .child(div().text_xs().child(alias))
                                    .child(
                                        Button::new(("dictionary-edit-remove-alias", alias_idx))
                                            .xsmall()
                                            .ghost()
                                            .icon(IconName::X)
                                            .disabled(busy)
                                            .accessibility_label(
                                                self.text("删除此别名", "Remove alias"),
                                            )
                                            .on_click(cx.listener(move |this, _, window, cx| {
                                                if let Some(editor) = &this.dict.term_editor {
                                                    let next = remove_alias_from_csv(
                                                        &editor.aliases.read(cx).value(),
                                                        &remove_target,
                                                    );
                                                    editor.aliases.update(cx, |input, cx| {
                                                        input.set_value(next, window, cx)
                                                    });
                                                    cx.notify();
                                                }
                                            })),
                                    )
                            },
                        )),
                );
        }

        form.child(
            h_flex()
                .gap_2()
                .child(
                    Button::new("dictionary-edit-save")
                        .xsmall()
                        .primary()
                        .disabled(busy || term_draft.is_empty())
                        .label(self.text("保存", "Save"))
                        .on_click(cx.listener(|this, _, _, cx| this.submit_term_edit(cx))),
                )
                .child(
                    Button::new("dictionary-edit-cancel")
                        .xsmall()
                        .ghost()
                        .disabled(busy)
                        .label(self.text("取消", "Cancel"))
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.dict.term_editor = None;
                            cx.notify();
                        })),
                ),
        )
    }

    fn dictionary_suggestions_view(&self, cx: &mut Context<Self>) -> Div {
        let records = self.array("dictionary_learning_records");
        let learning_on = get(self.config(), "dictionary_learning.enabled") == true;
        let review_records: Vec<(usize, &Value)> = records
            .iter()
            .enumerate()
            .filter(|(_, r)| is_actionable_review(r))
            .collect();
        let applied_records: Vec<(usize, &Value)> = records
            .iter()
            .enumerate()
            .filter(|(_, r)| is_applied_record(r))
            .collect();
        let history_records: Vec<(usize, &Value)> = records
            .iter()
            .enumerate()
            .filter(|(_, r)| !is_actionable_review(r) && !is_applied_record(r))
            .collect();
        let clearable_count = records
            .iter()
            .filter(|r| is_clearable_history_record(r))
            .count();

        let active_filter = if self.dict.learning_filter_explicit {
            self.dict.learning_filter
        } else if !review_records.is_empty() {
            LearningFilter::Review
        } else if !applied_records.is_empty() {
            LearningFilter::Applied
        } else {
            LearningFilter::Review
        };

        let mut filter_bar = h_flex()
            .w_full()
            .min_w_0()
            .justify_between()
            .items_center()
            .gap_2()
            .flex_wrap()
            .child(
                h_flex()
                    .gap_1()
                    .flex_wrap()
                    .child(
                        Button::new("dict-learning-review")
                            .small()
                            .ghost()
                            .selected(active_filter == LearningFilter::Review)
                            .label(format!(
                                "{} ({})",
                                self.text("待审核", "Needs review"),
                                review_records.len()
                            ))
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.dict.learning_filter = LearningFilter::Review;
                                this.dict.learning_filter_explicit = true;
                                cx.notify();
                            })),
                    )
                    .child(
                        Button::new("dict-learning-applied")
                            .small()
                            .ghost()
                            .selected(active_filter == LearningFilter::Applied)
                            .label(format!(
                                "{} ({})",
                                self.text("已加入", "Added"),
                                applied_records.len()
                            ))
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.dict.learning_filter = LearningFilter::Applied;
                                this.dict.learning_filter_explicit = true;
                                cx.notify();
                            })),
                    )
                    .child(
                        Button::new("dict-learning-history")
                            .small()
                            .ghost()
                            .selected(active_filter == LearningFilter::History)
                            .label(format!(
                                "{} ({})",
                                self.text("处理中与历史", "Activity & History"),
                                history_records.len()
                            ))
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.dict.learning_filter = LearningFilter::History;
                                this.dict.learning_filter_explicit = true;
                                cx.notify();
                            })),
                    ),
            );

        if active_filter == LearningFilter::History && clearable_count > 0 {
            filter_bar = filter_bar.child(
                Button::new("dict-learning-clear-history")
                    .xsmall()
                    .icon(IconName::Trash)
                    .label(self.text("清理历史记录", "Clear history"))
                    .on_click(cx.listener(|this, _, _, cx| {
                        this.action("clearDictionaryLearningHistory", json!({}), cx);
                    })),
            );
        }

        let selected_rows = match active_filter {
            LearningFilter::Review => review_records,
            LearningFilter::Applied => applied_records,
            LearningFilter::History => history_records,
        };

        let mut list_card = card(cx).gap_2();
        if selected_rows.is_empty() {
            let (title, hint) = match active_filter {
                LearningFilter::Review => {
                    if !learning_on {
                        (
                            self.text("自动学习未开启", "Automatic learning is off"),
                            self.text(
                                "开启上方“自动学习词典”后，系统会在你手动修正粘贴结果时自动识别专有名词建议。",
                                "Turn on Automatic dictionary learning above to capture reusable terms from your post-paste edits.",
                            ),
                        )
                    } else {
                        (
                            self.text("暂无待审核建议", "No suggestions waiting for review"),
                            self.text(
                                "系统会在本机观察你的手动修正，高置信同音词会自动生效，需要确认的候选项会出现在这里。",
                                "Local post-paste corrections are observed quietly; clear matches apply automatically and ambiguous ones appear here.",
                            ),
                        )
                    }
                }
                LearningFilter::Applied => (
                    self.text("还没有已加入的学习记录", "No learned entries added yet"),
                    self.text(
                        "自动生效或你手动确认的词条都会显示在这里，并支持随时一键撤销。",
                        "Terms added automatically or approved manually will appear here with one-click undo.",
                    ),
                ),
                LearningFilter::History => (
                    self.text("暂无历史记录", "No activity or history records"),
                    self.text(
                        "被本地规则忽略、已撤销或处理失败的记录会归档在这里，不会干扰词条库。",
                        "Ignored, undone, or failed learning attempts are kept here out of your main term list.",
                    ),
                ),
            };
            list_card = list_card
                .child(div().text_sm().font_weight(FontWeight::MEDIUM).child(title))
                .child(
                    div()
                        .text_xs()
                        .whitespace_normal()
                        .text_color(cx.theme().muted_foreground)
                        .child(hint),
                );
        } else {
            for (row_idx, (orig_index, record)) in selected_rows.into_iter().enumerate() {
                let id = record["id"].as_str().unwrap_or_default().to_owned();
                let term = record["term"]
                    .as_str()
                    .unwrap_or_default()
                    .trim()
                    .to_owned();
                let aliases = schema::display_value(&record["aliases"]);
                let status = record["status"].as_str().unwrap_or("pending");
                let reason_code = record["reason_code"].as_str().unwrap_or_default();
                let error_text = record["error"]
                    .as_str()
                    .unwrap_or_default()
                    .trim()
                    .to_owned();
                let apply_origin = record["apply_origin"].as_str().unwrap_or("automatic");
                let time_label = readable_unix_timestamp(
                    record["created_at"].as_f64().unwrap_or_default(),
                    self.english(),
                );
                let (status_label, status_tone) = localized_learning_status(status, self.english());

                let mut header = h_flex().items_center().gap_1p5().flex_wrap();
                if !term.is_empty() {
                    if !aliases.is_empty() {
                        header = header
                            .child(badge(aliases.clone(), Tone::Neutral, cx))
                            .child(
                                div()
                                    .text_xs()
                                    .text_color(cx.theme().muted_foreground)
                                    .child("→"),
                            );
                    }
                    header = header.child(badge(
                        term.clone(),
                        if status == "review" {
                            Tone::Primary
                        } else if is_applied_record(record) {
                            Tone::Success
                        } else {
                            Tone::Neutral
                        },
                        cx,
                    ));
                }
                header = header.child(badge(status_label, status_tone, cx));
                if is_applied_record(record) {
                    header = header.child(badge(
                        if apply_origin == "review" {
                            self.text("手动确认", "Manual")
                        } else {
                            self.text("自动加入", "Auto")
                        },
                        Tone::Neutral,
                        cx,
                    ));
                }

                let mut details = Vec::new();
                if let Some(reason) = localized_learning_reason(reason_code, self.english()) {
                    details.push(reason.to_owned());
                }
                if !error_text.is_empty() && status == "failed" {
                    details.push(error_text);
                }
                if time_label != "—" {
                    details.push(time_label);
                }

                let mut row_actions = h_flex().gap_1p5().flex_shrink_0();
                if is_actionable_review(record) {
                    let approve_id = id.clone();
                    let edit_id = id.clone();
                    let edit_term = term.clone();
                    let edit_aliases = aliases.clone();
                    let reject_id = id.clone();
                    row_actions = row_actions
                        .child(
                            Button::new(("approveDictionaryLearning", orig_index))
                                .xsmall()
                                .primary()
                                .label(self.text("加入词典", "Approve"))
                                .on_click(cx.listener(move |this, _, _, cx| {
                                    this.action(
                                        "approveDictionaryLearning",
                                        json!({"id": approve_id}),
                                        cx,
                                    );
                                })),
                        )
                        .child(
                            Button::new(("editDictionaryLearning", orig_index))
                                .xsmall()
                                .label(self.text("编辑后加入", "Edit & Add"))
                                .on_click(cx.listener(move |this, _, window, cx| {
                                    this.begin_review_edit(
                                        edit_id.clone(),
                                        edit_term.clone(),
                                        edit_aliases.clone(),
                                        window,
                                        cx,
                                    );
                                })),
                        )
                        .child(
                            Button::new(("rejectDictionaryLearning", orig_index))
                                .xsmall()
                                .ghost()
                                .label(self.text("忽略", "Ignore"))
                                .on_click(cx.listener(move |this, _, _, cx| {
                                    this.action(
                                        "rejectDictionaryLearning",
                                        json!({"id": reject_id}),
                                        cx,
                                    );
                                })),
                        );
                } else if is_applied_record(record) {
                    let undo_id = id.clone();
                    row_actions = row_actions.child(
                        Button::new(("undoDictionaryLearning", orig_index))
                            .xsmall()
                            .label(self.text("撤销学习", "Undo"))
                            .on_click(cx.listener(move |this, _, _, cx| {
                                this.action("undoDictionaryLearning", json!({"id": undo_id}), cx);
                            })),
                    );
                }

                let mut row_block = v_flex()
                    .w_full()
                    .min_w_0()
                    .gap_2()
                    .py_2()
                    .when(row_idx > 0, |row| {
                        row.border_t_1()
                            .border_color(cx.theme().border.opacity(0.6))
                    })
                    .child(
                        h_flex()
                            .w_full()
                            .min_w_0()
                            .justify_between()
                            .items_center()
                            .gap_3()
                            .flex_wrap()
                            .child(v_flex().flex_1().min_w_0().gap_1().child(header).when(
                                !details.is_empty(),
                                |col| {
                                    col.child(
                                        div()
                                            .text_xs()
                                            .whitespace_normal()
                                            .text_color(cx.theme().muted_foreground)
                                            .child(details.join(" · ")),
                                    )
                                },
                            ))
                            .child(row_actions),
                    );

                if let Some(editor) = &self.dict.review_editor
                    && editor.id == id
                {
                    let busy = editor.pending_request_id.is_some();
                    let can_save = !editor.term.read(cx).value().trim().is_empty();
                    row_block = row_block.child(
                        v_flex()
                            .w_full()
                            .min_w_0()
                            .gap_2()
                            .p_3()
                            .rounded(cx.theme().radius)
                            .bg(cx.theme().secondary.opacity(0.5))
                            .border_1()
                            .border_color(cx.theme().primary.opacity(0.35))
                            .child(
                                h_flex()
                                    .w_full()
                                    .min_w_0()
                                    .gap_2()
                                    .when(self.narrow, |row| row.flex_col())
                                    .child(
                                        div().flex_1().min_w_0().child(
                                            Input::new(&editor.term)
                                                .aria_label(
                                                    self.text("修订词条", "Edit suggested term"),
                                                )
                                                .disabled(busy),
                                        ),
                                    )
                                    .child(
                                        div().flex_1().min_w_0().child(
                                            Input::new(&editor.aliases)
                                                .aria_label(
                                                    self.text("修订别名", "Edit suggested aliases"),
                                                )
                                                .disabled(busy),
                                        ),
                                    ),
                            )
                            .child(
                                h_flex()
                                    .gap_2()
                                    .child(
                                        Button::new("dict-review-edit-save")
                                            .xsmall()
                                            .primary()
                                            .disabled(busy || !can_save)
                                            .label(self.text("确认加入", "Confirm & Add"))
                                            .on_click(cx.listener(|this, _, _, cx| {
                                                this.submit_review_edit(cx);
                                            })),
                                    )
                                    .child(
                                        Button::new("dict-review-edit-cancel")
                                            .xsmall()
                                            .ghost()
                                            .disabled(busy)
                                            .label(self.text("取消", "Cancel"))
                                            .on_click(cx.listener(|this, _, _, cx| {
                                                this.dict.review_editor = None;
                                                cx.notify();
                                            })),
                                    ),
                            ),
                    );
                }

                list_card = list_card.child(row_block);
            }
        }

        v_flex()
            .w_full()
            .min_w_0()
            .gap_3()
            .child(filter_bar)
            .child(titled(
                self.text("最近学习活动", "Recent Learning Activity"),
                list_card,
                cx,
            ))
    }

    fn dictionary_privacy_view(&self, cx: &mut Context<Self>) -> Div {
        let excluded = self.current_excluded_ids(cx);
        let english = self.english();

        let mut excluded_chips = h_flex().w_full().min_w_0().gap_1p5().flex_wrap();
        if excluded.is_empty() {
            excluded_chips = excluded_chips.child(
                div()
                    .text_xs()
                    .text_color(cx.theme().muted_foreground)
                    .child(self.text(
                        "尚未添加排除应用（系统密码框始终自动跳过）。",
                        "No excluded applications yet (system password fields are always skipped).",
                    )),
            );
        } else {
            for (index, bundle_id) in excluded.iter().enumerate() {
                let target = bundle_id.clone();
                let display = match preset_label(bundle_id, english) {
                    Some(name) => format!("{name} · {bundle_id}"),
                    None => bundle_id.clone(),
                };
                excluded_chips = excluded_chips.child(
                    h_flex()
                        .items_center()
                        .gap_1()
                        .pl_2p5()
                        .pr_1()
                        .py_0p5()
                        .rounded_full()
                        .bg(cx.theme().secondary)
                        .border_1()
                        .border_color(cx.theme().border)
                        .child(div().text_xs().child(display))
                        .child(
                            Button::new(("dict-excluded-remove", index))
                                .xsmall()
                                .ghost()
                                .icon(IconName::X)
                                .accessibility_label(
                                    self.text("移出排除列表", "Remove excluded app"),
                                )
                                .on_click(cx.listener(move |this, _, window, cx| {
                                    this.toggle_excluded_bundle_id(&target, window, cx);
                                })),
                        ),
                );
            }
        }

        let presets_row =
            h_flex()
                .w_full()
                .min_w_0()
                .gap_1p5()
                .flex_wrap()
                .children(PRIVACY_PRESETS.iter().map(|(bundle_id, zh, en)| {
                    let selected = excluded.iter().any(|item| item == bundle_id);
                    let target = *bundle_id;
                    Button::new(SharedString::from(format!("dict-preset-{bundle_id}")))
                        .xsmall()
                        .selected(selected)
                        .when(selected, |button| button.icon(IconName::Check))
                        .label(self.text(zh, en))
                        .on_click(cx.listener(move |this, _, window, cx| {
                            this.toggle_excluded_bundle_id(target, window, cx);
                        }))
                }));

        let privacy_card = card(cx)
            .gap_3()
            .child(
                v_flex()
                    .gap_0p5()
                    .child(
                        h_flex()
                            .items_center()
                            .gap_1p5()
                            .child(Icon::new(IconName::Shield).small())
                            .child(
                                div()
                                    .text_sm()
                                    .font_weight(FontWeight::SEMIBOLD)
                                    .child(self.text("隐私排除应用", "Excluded Applications")),
                            ),
                    )
                    .child(
                        div()
                            .text_xs()
                            .whitespace_normal()
                            .text_color(cx.theme().muted_foreground)
                            .child(self.text(
                                "列表中的应用将同时跳过自动词典学习与光标上下文读取；密码框始终自动跳过。",
                                "Apps below are skipped by both automatic dictionary learning and cursor context; password fields are always skipped.",
                            )),
                    ),
            )
            .child(excluded_chips)
            .child(
                v_flex()
                    .gap_1p5()
                    .pt_2()
                    .border_t_1()
                    .border_color(cx.theme().border.opacity(0.65))
                    .child(
                        div()
                            .text_xs()
                            .font_weight(FontWeight::MEDIUM)
                            .text_color(cx.theme().muted_foreground)
                            .child(self.text("常见敏感应用快捷切换", "Common sensitive apps")),
                    )
                    .child(presets_row),
            )
            .child(
                v_flex()
                    .w_full()
                    .min_w_0()
                    .gap_1p5()
                    .pt_2()
                    .border_t_1()
                    .border_color(cx.theme().border.opacity(0.65))
                    .child(
                        div()
                            .text_xs()
                            .font_weight(FontWeight::MEDIUM)
                            .text_color(cx.theme().muted_foreground)
                            .child(self.text("手动编辑 Bundle ID", "Manual Bundle IDs")),
                    )
                    .when_some(
                        self.controls.get("dictionary_learning.excluded_bundle_ids"),
                        |col, control| {
                            if let Control::Input(input) = control {
                                col.child(
                                    Input::new(input)
                                        .id("dictionary_learning.excluded_bundle_ids")
                                        .aria_label(self.text("隐私排除应用", "Excluded applications"))
                                        .accessibility_id("dictionary_learning.excluded_bundle_ids")
                                        .w_full(),
                                )
                            } else {
                                col
                            }
                        },
                    )
                    .child(
                        div()
                            .text_xs()
                            .whitespace_normal()
                            .text_color(cx.theme().muted_foreground)
                            .child(self.text(
                                "用逗号分隔多个 Bundle ID，失焦或按回车保存；与上方快捷开关实时同步。",
                                "Comma-separated Bundle IDs, saved on blur or Enter; stays in sync with the presets above.",
                            )),
                    ),
            );

        let dict_path = self.snapshot["dictionary_path"]
            .as_str()
            .filter(|path| !path.is_empty())
            .map(str::to_owned)
            .unwrap_or_else(|| {
                let dir = self.snapshot["backend_data_dir"]
                    .as_str()
                    .unwrap_or("~/.vocal-more");
                format!("{dir}/dictionary.yaml")
            });

        let file_card = card(cx)
            .gap_2p5()
            .child(
                v_flex()
                    .gap_0p5()
                    .child(
                        div()
                            .text_sm()
                            .font_weight(FontWeight::SEMIBOLD)
                            .child(self.text("本地词典文件", "Local Dictionary File")),
                    )
                    .child(
                        div()
                            .text_xs()
                            .whitespace_normal()
                            .text_color(cx.theme().muted_foreground)
                            .child(self.text(
                                "支持直接编辑 YAML 词典文件，保存后点击“重新加载词典”立即校验并生效。",
                                "Edit dictionary.yaml directly in your editor, then click Reload to validate and apply changes.",
                            )),
                    ),
            )
            .child(
                div()
                    .px_2p5()
                    .py_1p5()
                    .rounded(cx.theme().radius)
                    .bg(cx.theme().secondary.opacity(0.6))
                    .text_xs()
                    .font_family(cx.theme().mono_font_family.clone())
                    .text_color(cx.theme().muted_foreground)
                    .whitespace_normal()
                    .child(dict_path),
            )
            .child(
                h_flex()
                    .gap_2()
                    .flex_wrap()
                    .child(
                        Button::new("open-dictionary")
                            .small()
                            .icon(IconName::FolderOpen)
                            .label(self.text("打开词典文件", "Open dictionary file"))
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.action("openDictFile", json!({}), cx);
                            })),
                    )
                    .child(
                        Button::new("reload-dictionary")
                            .small()
                            .icon(IconName::RefreshCw)
                            .label(self.text("重新加载词典", "Reload dictionary"))
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.action("reloadDict", json!({}), cx);
                            })),
                    ),
            );

        v_flex()
            .w_full()
            .min_w_0()
            .gap_3()
            .child(privacy_card)
            .child(file_card)
    }
}

#[cfg(test)]
mod tests {
    use super::{merge_excluded_ids, parse_batch_lines, remove_alias_from_csv};

    #[test]
    fn batch_parser_supports_colons_tabs_comments_and_dedup() {
        let input = "\n# comment\nKubernetes\n阿里云百炼: 阿里云白练, 阿里百炼\nVocal More：vocalmore、vocal more\nGPUI\tgp ui; GPUI\nKubernetes: k8s, K8S\n";
        let parsed = parse_batch_lines(input);
        assert_eq!(
            parsed,
            vec![
                ("Kubernetes".into(), vec!["k8s".into(), "K8S".into()]),
                (
                    "阿里云百炼".into(),
                    vec!["阿里云白练".into(), "阿里百炼".into()]
                ),
                (
                    "Vocal More".into(),
                    vec!["vocalmore".into(), "vocal more".into()]
                ),
                ("GPUI".into(), vec!["gp ui".into()]),
            ]
        );
    }

    #[test]
    fn alias_csv_removal_and_excluded_merge_preserve_order() {
        assert_eq!(
            remove_alias_from_csv("alpha, beta， gamma", "beta"),
            "alpha, gamma"
        );
        assert_eq!(
            merge_excluded_ids("com.example.custom", "com.1password.1password"),
            vec!["com.example.custom", "com.1password.1password"]
        );
        assert_eq!(
            merge_excluded_ids(
                "com.example.custom, com.1password.1password",
                "com.1password.1password"
            ),
            vec!["com.example.custom"]
        );
    }
}
