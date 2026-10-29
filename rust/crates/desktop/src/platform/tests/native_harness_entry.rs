// SPDX-License-Identifier: GPL-3.0-only
#[cfg(target_os = "macos")]
#[path = "native_harness.rs"]
mod macos;

fn main() {
    #[cfg(target_os = "macos")]
    macos::main();
    #[cfg(not(target_os = "macos"))]
    eprintln!("This acceptance tool requires macOS.");
}
