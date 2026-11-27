// SPDX-License-Identifier: GPL-3.0-only
//! Asset source for the desktop app: GPUI Kit's default component icons plus
//! the Lucide icons used by Vocal More's navigation, status banners, and actions.
use gpui_kit::{AssetSource, Result, SharedString, assets::icon_assets};
use std::borrow::Cow;

icon_assets!(
    SettingsIcons,
    [
        AppWindow,
        ArrowUpDown,
        AudioLines,
        BookA,
        BookOpen,
        Check,
        ChevronDown,
        ChevronLeft,
        ChevronRight,
        CircleAlert,
        CircleCheck,
        Clock,
        Cloud,
        Command,
        Copy,
        Ellipsis,
        ExternalLink,
        FolderOpen,
        Globe,
        House,
        Info,
        Keyboard,
        ListPlus,
        Mic,
        Palette,
        PanelLeft,
        Pause,
        Pencil,
        Play,
        Plus,
        RefreshCw,
        RotateCcwClock,
        Search,
        Settings,
        Shield,
        SlidersHorizontal,
        Sparkles,
        Square,
        Trash,
        TriangleAlert,
        Volume2,
        WandSparkles,
        X,
    ]
);

const APP_ICONS: [(&str, &str, &[u8]); 4] = [
    (
        "default",
        "icons/default.png",
        include_bytes!("../assets/icons/default.png"),
    ),
    (
        "night",
        "icons/night.png",
        include_bytes!("../assets/icons/night.png"),
    ),
    (
        "wave",
        "icons/wave.png",
        include_bytes!("../assets/icons/wave.png"),
    ),
    (
        "minimal",
        "icons/minimal.png",
        include_bytes!("../assets/icons/minimal.png"),
    ),
];

pub fn app_icon_bytes(id: &str) -> &'static [u8] {
    APP_ICONS
        .iter()
        .find(|(name, _, _)| *name == id)
        .unwrap_or(&APP_ICONS[0])
        .2
}

#[derive(Clone, Copy, Debug, Default)]
pub struct Assets;
impl AssetSource for Assets {
    fn load(&self, path: &str) -> Result<Option<Cow<'static, [u8]>>> {
        if let Some((_, _, bytes)) = APP_ICONS.iter().find(|(_, name, _)| *name == path) {
            return Ok(Some(Cow::Borrowed(bytes)));
        }
        match SettingsIcons.load(path)? {
            Some(bytes) => Ok(Some(bytes)),
            None => gpui_kit::assets::Assets.load(path),
        }
    }
    fn list(&self, path: &str) -> Result<Vec<SharedString>> {
        let mut paths = gpui_kit::assets::Assets.list(path)?;
        let app_icons = APP_ICONS
            .iter()
            .filter(|(_, name, _)| name.starts_with(path))
            .map(|(_, name, _)| SharedString::from(*name));
        for extra in SettingsIcons.list(path)?.into_iter().chain(app_icons) {
            if !paths.contains(&extra) {
                paths.push(extra);
            }
        }
        Ok(paths)
    }
}
