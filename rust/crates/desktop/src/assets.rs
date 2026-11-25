// SPDX-License-Identifier: GPL-3.0-only
//! Asset source for the desktop app: GPUI Kit's default component icons plus
//! the Lucide icons used by Vocal More's navigation, status banners, and actions.
use gpui_kit::{AssetSource, Result, SharedString, assets::icon_assets};
use std::borrow::Cow;

icon_assets!(
    SettingsIcons,
    [
        AudioLines,
        BookA,
        BookOpen,
        Check,
        ChevronDown,
        ChevronRight,
        CircleAlert,
        CircleCheck,
        Clock,
        Copy,
        House,
        Keyboard,
        Mic,
        Play,
        RefreshCw,
        RotateCcwClock,
        Settings,
        SlidersHorizontal,
        Sparkles,
        Square,
        TriangleAlert,
        WandSparkles,
    ]
);

#[derive(Clone, Copy, Debug, Default)]
pub struct Assets;
impl AssetSource for Assets {
    fn load(&self, path: &str) -> Result<Option<Cow<'static, [u8]>>> {
        match SettingsIcons.load(path)? {
            Some(bytes) => Ok(Some(bytes)),
            None => gpui_kit::assets::Assets.load(path),
        }
    }
    fn list(&self, path: &str) -> Result<Vec<SharedString>> {
        let mut paths = gpui_kit::assets::Assets.list(path)?;
        for extra in SettingsIcons.list(path)? {
            if !paths.contains(&extra) {
                paths.push(extra);
            }
        }
        Ok(paths)
    }
}
