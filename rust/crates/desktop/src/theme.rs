// SPDX-License-Identifier: GPL-3.0-only
//! Vercel Geist look for the settings window: embedded Geist Sans/Mono fonts
//! and light/dark palettes taken from the Geist color tokens.
use anyhow::{Context, Result};
use gpui_kit::component::{Theme, ThemeConfig, ThemeMode, ThemeSet};
use gpui_kit::{App, Window};
use std::{borrow::Cow, rc::Rc};

const FONTS: [&[u8]; 5] = [
    include_bytes!("../assets/fonts/Geist-Regular.ttf"),
    include_bytes!("../assets/fonts/Geist-Medium.ttf"),
    include_bytes!("../assets/fonts/Geist-SemiBold.ttf"),
    include_bytes!("../assets/fonts/GeistMono-Regular.ttf"),
    include_bytes!("../assets/fonts/GeistMono-Medium.ttf"),
];

fn palette(mode: ThemeMode) -> Result<ThemeConfig> {
    let set: ThemeSet = serde_json::from_str(include_str!("../assets/themes/geist.json"))
        .context("Geist theme file is invalid")?;
    set.themes
        .into_iter()
        .find(|theme| theme.mode.is_dark() == mode.is_dark())
        .context("Geist theme file lacks a palette")
}

/// Call once after `gpui_kit::init`, before opening windows.
pub fn install(cx: &mut App) -> Result<()> {
    cx.text_system()
        .add_fonts(FONTS.iter().map(|font| Cow::Borrowed(*font)).collect())
        .context("could not load the Geist fonts")?;
    let light = palette(ThemeMode::Light)?;
    let dark = palette(ThemeMode::Dark)?;
    let theme = Theme::global_mut(cx);
    theme.light_theme = Rc::new(light);
    theme.dark_theme = Rc::new(dark);
    let appearance = cx.window_appearance();
    Theme::change(appearance, None, cx);
    Ok(())
}

/// Follow the system light/dark appearance for this window.
pub fn follow_appearance(window: &mut Window, cx: &mut App) {
    Theme::sync_system_appearance(Some(window), cx);
}
