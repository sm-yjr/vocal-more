// SPDX-License-Identifier: GPL-3.0-only
fn main() {
    if let Err(error) = run() {
        eprintln!("Vocal More: {error:#}");
        std::process::exit(1);
    }
}

fn run() -> anyhow::Result<()> {
    let options = vocal_more_desktop::options::DesktopOptions::parse()?;
    if let Some(directory) = &options.capsule_fixtures {
        let directory = directory.clone();
        let mtm = objc2::MainThreadMarker::new().expect("main thread");
        let app = objc2_app_kit::NSApplication::sharedApplication(mtm);
        app.setActivationPolicy(objc2_app_kit::NSApplicationActivationPolicy::Accessory);
        let (driver, sink, _events) =
            vocal_more_desktop::bridge::BackendDriver::start(options.backend)?;
        let mut capsule = vocal_more_desktop::capsule::Capsule::new(mtm, sink)?;
        let report = capsule.export_parity_fixtures(&directory)?;
        driver.close();
        println!("{}", serde_json::to_string_pretty(&report)?);
        return Ok(());
    }
    gpui_kit::application()
        .with_assets(gpui_kit::assets::Assets)
        .run(move |cx| {
            gpui_kit::init(cx);
            if let Err(error) = vocal_more_desktop::host::DesktopHost::start(options, cx) {
                eprintln!("Vocal More: {error:#}");
                cx.quit();
            }
        });
    Ok(())
}
