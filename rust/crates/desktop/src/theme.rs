// SPDX-License-Identifier: GPL-3.0-only
//! Tahoe settings palettes and effective UI preferences.
use anyhow::{Context, Result};
use gpui_kit::component::{Theme, ThemeConfig, ThemeMode, ThemeSet};
use gpui_kit::{App, Window, WindowBackgroundAppearance};
use objc2_app_kit::NSWorkspace;
use serde_json::Value;
use std::rc::Rc;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct UiPreferences {
    pub color_scheme: &'static str,
    pub interface_material: &'static str,
    pub reduce_motion: bool,
    pub app_icon: &'static str,
    pub icon_visibility: &'static str,
    pub capsule_position: &'static str,
}

impl UiPreferences {
    pub fn from_config(config: &Value) -> Self {
        let ui = &config["ui"];
        let choice = |key: &str, alternatives: &[&'static str], default| {
            alternatives
                .iter()
                .copied()
                .find(|option| ui[key].as_str() == Some(*option))
                .unwrap_or(default)
        };
        Self {
            color_scheme: choice("color_scheme", &["light", "dark"], "auto"),
            interface_material: choice("interface_material", &["classic"], "glass"),
            reduce_motion: ui["reduce_motion"].as_bool().unwrap_or(false),
            app_icon: choice("app_icon", &["night", "wave", "minimal"], "default"),
            icon_visibility: choice(
                "icon_visibility",
                &["menu_and_dock", "dock_only"],
                "menu_only",
            ),
            capsule_position: choice("capsule_position", &["top", "custom"], "bottom"),
        }
    }
}

fn palette(mode: ThemeMode) -> Result<ThemeConfig> {
    let set: ThemeSet = serde_json::from_str(include_str!("../assets/themes/geist.json"))
        .context("Tahoe theme file is invalid")?;
    set.themes
        .into_iter()
        .find(|theme| theme.mode.is_dark() == mode.is_dark())
        .context("Tahoe theme file lacks a palette")
}

/// Call once after `gpui_kit::init`, before opening windows.
pub fn install(cx: &mut App) -> Result<()> {
    let light = palette(ThemeMode::Light)?;
    let dark = palette(ThemeMode::Dark)?;
    let theme = Theme::global_mut(cx);
    theme.light_theme = Rc::new(light);
    theme.dark_theme = Rc::new(dark);
    let appearance = cx.window_appearance();
    Theme::change(appearance, None, cx);
    Ok(())
}

pub fn apply_appearance(prefs: &UiPreferences, mut window: Option<&mut Window>, cx: &mut App) {
    match prefs.color_scheme {
        "light" => Theme::change(ThemeMode::Light, window.as_deref_mut(), cx),
        "dark" => Theme::change(ThemeMode::Dark, window.as_deref_mut(), cx),
        _ => Theme::sync_system_appearance(window.as_deref_mut(), cx),
    }
    if let Some(window) = window {
        window.set_background_appearance(window_background(prefs));
    }
    let system_reduce_motion =
        NSWorkspace::sharedWorkspace().accessibilityDisplayShouldReduceMotion();
    cx.set_reduce_motion(prefs.reduce_motion || system_reduce_motion);
}

pub fn window_background(prefs: &UiPreferences) -> WindowBackgroundAppearance {
    if prefs.interface_material == "classic" {
        WindowBackgroundAppearance::Opaque
    } else {
        WindowBackgroundAppearance::Blurred
    }
}
