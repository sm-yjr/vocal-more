// SPDX-License-Identifier: GPL-3.0-only
//! Deterministic product data; never activates, records, reads AX or pastes.
use anyhow::{Context, Result};
use objc2::{MainThreadMarker, rc::autoreleasepool};
use vocal_more_backend::application::Options;
use vocal_more_desktop::{bridge::BackendDriver, platform};
fn main() -> Result<()> {
    let data = std::env::args_os()
        .nth(1)
        .context("supply a fresh isolated data directory")?;
    let data = std::path::PathBuf::from(data);
    anyhow::ensure!(!data.exists(), "benchmark directory must not exist");
    let mtm = MainThreadMarker::new().context("main thread required")?;
    let app = objc2_app_kit::NSApplication::sharedApplication(mtm);
    app.setActivationPolicy(objc2_app_kit::NSApplicationActivationPolicy::Accessory);
    let (driver, commands, _) = BackendDriver::start(Options::new(data))?;
    let report = autoreleasepool(|_| platform::measure_status_updates(mtm, commands));
    driver.close();
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
    while !driver.finished() {
        anyhow::ensure!(
            std::time::Instant::now() < deadline,
            "isolated backend did not shut down"
        );
        std::thread::sleep(std::time::Duration::from_millis(1));
    }
    println!("{}", serde_json::to_string_pretty(&report)?);
    Ok(())
}
