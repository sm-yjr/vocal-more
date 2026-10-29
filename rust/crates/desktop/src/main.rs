// SPDX-License-Identifier: GPL-3.0-only
fn main() {
    if let Err(error) = run() {
        eprintln!("Vocal More: {error:#}");
        std::process::exit(1);
    }
}

fn run() -> anyhow::Result<()> {
    #[cfg(target_os = "linux")]
    if vocal_more_desktop::linux::control::handle_cli()? {
        return Ok(());
    }
    let options = vocal_more_desktop::options::DesktopOptions::parse()?;
    #[cfg(target_os = "macos")]
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
    #[cfg(target_os = "linux")]
    let server = match vocal_more_desktop::linux::control::Server::bind(&options.backend.data_dir) {
        Ok(server) => server,
        Err(error) => {
            let command = if options.background {
                "ping"
            } else {
                "settings"
            };
            if vocal_more_desktop::linux::control::send(&options.backend.data_dir, command).is_ok()
            {
                return Ok(());
            }
            return Err(error);
        }
    };
    let application = gpui_kit::application();
    #[cfg(target_os = "linux")]
    let application = {
        let mut display = gpui_kit::GraphicalEnvironment::detect();
        // Keep the real process/session environment intact for portals, audio,
        // clipboard and accessibility. Only GPUI rendering uses Xwayland so its
        // focus-free popup works before a new GNOME extension is loaded.
        if display.x11_display.is_some()
            && !std::env::var("GPUI_BACKEND")
                .is_ok_and(|value| value.eq_ignore_ascii_case("wayland"))
        {
            display.wayland_display = None;
        }
        application
            .with_quit_mode(gpui_kit::QuitMode::Explicit)
            .with_windowing(gpui_kit::WindowingRequest::Windowed(display))
    };
    application
        .with_assets(vocal_more_desktop::assets::Assets)
        .run(move |cx| {
            gpui_kit::init(cx);
            if let Err(error) = vocal_more_desktop::theme::install(cx) {
                eprintln!("Vocal More: {error:#}");
            }
            #[cfg(target_os = "macos")]
            let result = vocal_more_desktop::host::DesktopHost::start(options, cx);
            #[cfg(target_os = "linux")]
            let result = vocal_more_desktop::linux::start(options, server, cx);
            if let Err(error) = result {
                eprintln!("Vocal More: {error:#}");
                cx.quit();
            }
        });
    Ok(())
}
