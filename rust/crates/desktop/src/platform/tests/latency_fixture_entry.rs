// SPDX-License-Identifier: GPL-3.0-only
#[cfg(target_os = "macos")]
#[path = "latency_fixture.rs"]
mod macos;

fn main() -> anyhow::Result<()> {
    #[cfg(target_os = "macos")]
    return macos::main();
    #[cfg(not(target_os = "macos"))]
    eprintln!("This acceptance tool requires macOS.");
    #[cfg(not(target_os = "macos"))]
    Ok(())
}
