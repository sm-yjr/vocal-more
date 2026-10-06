// SPDX-License-Identifier: GPL-3.0-only
//! Read-only identities for pointer tests against the production controls.
//! This supplies no state override and is excluded from normal desktop builds.
use super::{Control, Settings};
use gpui_kit::{App, ElementId, SharedString};

impl Settings {
    pub fn testing_pending_count(&self) -> usize {
        self.pending.len()
    }
    pub fn testing_mic_state(&self) -> &'static str {
        match self.mic {
            super::MicState::Idle => "idle",
            super::MicState::Starting => "starting",
            super::MicState::Recording => "recording",
            super::MicState::Done => "done",
            super::MicState::Error => "error",
        }
    }
    pub fn testing_control_id(&self, key: &str) -> Option<ElementId> {
        Some(match self.controls.get(key)? {
            Control::Slider(state) => ("slider", state.entity_id()).into(),
            _ => key.to_owned().into(),
        })
    }
    pub fn testing_select_cursor(&self, key: &str, cx: &App) -> Option<usize> {
        match self.controls.get(key)? {
            Control::Select(select) => select.read(cx).selected_index(cx).map(|index| index.row),
            _ => None,
        }
    }
    pub fn testing_prompt_id(&self, category: &str) -> Option<ElementId> {
        Some(("input", self.prompts.get(category)?.entity_id()).into())
    }
    pub fn testing_input_value(&self, key: &str, cx: &App) -> Option<SharedString> {
        match self.controls.get(key)? {
            Control::Input(input) => Some(input.read(cx).value()),
            _ => None,
        }
    }
    pub fn testing_input_masked(&self, key: &str, cx: &App) -> Option<bool> {
        match self.controls.get(key)? {
            Control::Input(input) => Some(input.read(cx).presentation().is_masked()),
            _ => None,
        }
    }
}
