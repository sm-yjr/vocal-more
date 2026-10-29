// SPDX-License-Identifier: GPL-3.0-only
pub mod assets;
pub mod bridge;
#[cfg(target_os = "macos")]
pub mod capsule;
pub mod delivery;
#[cfg(target_os = "macos")]
pub mod host;
pub mod options;
#[cfg(target_os = "macos")]
pub mod platform;
pub mod settings;
pub mod theme;

#[cfg(target_os = "linux")]
pub mod linux;
