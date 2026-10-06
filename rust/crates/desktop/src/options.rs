// SPDX-License-Identifier: GPL-3.0-only
use anyhow::{Context, Result, bail, ensure};
use std::path::PathBuf;
use vocal_more_backend::{application::Options, provider::Endpoints};
use vocal_more_core::audio::NativeAudio;

pub struct DesktopOptions {
    pub backend: Options,
    pub no_hotkeys: bool,
    pub show_settings: bool,
    pub quit_after_ms: Option<u64>,
    pub capsule_fixtures: Option<PathBuf>,
}

impl DesktopOptions {
    pub fn parse() -> Result<Self> {
        let home = std::env::var_os("HOME")
            .map(PathBuf::from)
            .context("HOME is unavailable")?;
        let legacy_dir = home.join(".vocal-more");
        let mut data = None;
        let mut native = None;
        let mut no_import = false;
        let mut no_hotkeys = false;
        let mut show_settings = false;
        let mut allow_test_sources = false;
        let mut fixture_http = None;
        let mut fixture_ws = None;
        let mut quit_after_ms = None;
        let mut capsule_fixtures = None;
        let mut args = std::env::args().skip(1);
        while let Some(flag) = args.next() {
            match flag.as_str() {
                "--version" => {
                    println!("Vocal More {}", vocal_more_backend::PRODUCT_VERSION);
                    std::process::exit(0);
                }
                "--no-import" => no_import = true,
                "--no-hotkeys" => no_hotkeys = true,
                "--show-settings" => show_settings = true,
                "--allow-test-sources" => allow_test_sources = true,
                "--help" | "-h" => {
                    println!(
                        "Vocal More Rust macOS desktop\n\n--show-settings\n--data-dir PATH\n--native-library PATH\n--no-import\n--no-hotkeys\n--version\n\nAcceptance only: --allow-test-sources --fixture-http URL --fixture-websocket URL\n--capsule-fixtures PATH --quit-after-ms MS"
                    );
                    std::process::exit(0);
                }
                _ => {
                    let value = args
                        .next()
                        .with_context(|| format!("missing value for {flag}"))?;
                    match flag.as_str() {
                        "--data-dir" | "--backend-data-dir" => data = Some(PathBuf::from(value)),
                        "--native-library" => native = Some(PathBuf::from(value)),
                        "--fixture-http" => fixture_http = Some(value),
                        "--fixture-websocket" => fixture_ws = Some(value),
                        "--quit-after-ms" => {
                            quit_after_ms = Some(value.parse().context("invalid quit deadline")?)
                        }
                        "--capsule-fixtures" => capsule_fixtures = Some(PathBuf::from(value)),
                        _ => bail!("unknown option: {flag}"),
                    }
                }
            }
        }
        let explicit_data = data.is_some();
        if capsule_fixtures.is_some() || allow_test_sources || quit_after_ms.is_some() {
            ensure!(
                explicit_data && no_import && no_hotkeys,
                "acceptance mode requires an explicit --data-dir, --no-import and --no-hotkeys"
            );
        }
        let mut backend = Options::new(data.unwrap_or_else(|| legacy_dir.join("rust-backend")));
        if !no_import && !explicit_data {
            backend.import_from = Some(legacy_dir);
        }
        backend.native = native
            .or_else(find_native_library)
            .map(NativeAudio::load)
            .transpose()?;
        backend.allow_test_sources = allow_test_sources;
        if fixture_http.is_some() || fixture_ws.is_some() {
            ensure!(
                allow_test_sources,
                "fixture endpoints require --allow-test-sources"
            );
            backend.endpoints = Endpoints::loopback(
                &fixture_http.context("--fixture-http is required")?,
                &fixture_ws.context("--fixture-websocket is required")?,
            )?;
        } else if !allow_test_sources && quit_after_ms.is_none() && capsule_fixtures.is_none() {
            backend.environment_key = std::env::var("DASHSCOPE_API_KEY").ok();
        }
        Ok(Self {
            backend,
            no_hotkeys,
            show_settings,
            quit_after_ms,
            capsule_fixtures,
        })
    }
}

fn find_native_library() -> Option<PathBuf> {
    let mut paths = Vec::new();
    if let Some(path) = std::env::var_os("VOCAL_MORE_NATIVE_AUDIO_LIBRARY") {
        paths.push(PathBuf::from(path));
    }
    if let Ok(binary) = std::env::current_exe()
        && let Some(macos) = binary.parent()
    {
        if let Some(contents) = macos.parent() {
            paths.push(contents.join("Frameworks/libvocal_more_audio.dylib"));
        }
        paths.push(macos.join("libvocal_more_audio.dylib"));
    }
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
    paths.extend([
        root.join(".build/native/libvocal_more_audio.dylib"),
        root.join(".build/rust-desktop/libvocal_more_audio.dylib"),
        root.join(".build/rust-host/libvocal_more_audio.dylib"),
        root.join("rust/target/debug/libvocal_more_audio.dylib"),
        root.join("build/native/libvocal_more_audio.dylib"),
    ]);
    paths.into_iter().find(|path| path.is_file())
}
