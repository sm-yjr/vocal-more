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
        Self::parse_from(home, std::env::current_exe().ok(), std::env::args().skip(1))
    }
    fn parse_from(
        home: PathBuf,
        executable: Option<PathBuf>,
        arguments: impl Iterator<Item = String>,
    ) -> Result<Self> {
        let legacy_dir = home.join(".vocal-more");
        // The packaged fixed-text prototype must also be safe when opened
        // directly in Finder: isolated data, no credential import/audio/tap,
        // settings visible, and a bounded lifetime.
        let contents = executable.and_then(|p| p.parent()?.parent().map(PathBuf::from));
        let fixed_text_prototype = contents
            .as_ref()
            .is_some_and(|p| p.join("Resources/BluetoothFixedTextPrototype").is_file());
        let product_candidate = contents
            .as_ref()
            .is_some_and(|p| p.join("Resources/BluetoothProductCandidate").is_file());
        let isolated = fixed_text_prototype || product_candidate;
        let mut data = isolated.then(|| {
            home.join(if product_candidate {
                "Library/Application Support/Vocal More Bluetooth Candidate"
            } else {
                "Library/Application Support/Vocal More Bluetooth Dev V2"
            })
        });
        let mut native = None;
        let mut no_import = isolated;
        let mut no_hotkeys = isolated;
        let mut no_audio = isolated;
        let mut show_settings = isolated;
        let mut allow_test_sources = false;
        let mut fixture_http = None;
        let mut fixture_ws = None;
        let mut quit_after_ms = isolated.then_some(15 * 60 * 1000);
        let mut capsule_fixtures = None;
        let mut args = arguments;
        while let Some(flag) = args.next() {
            match flag.as_str() {
                "--version" => {
                    println!("Vocal More {}", vocal_more_backend::PRODUCT_VERSION);
                    std::process::exit(0);
                }
                "--no-import" => no_import = true,
                "--no-hotkeys" => no_hotkeys = true,
                "--no-audio" => no_audio = true,
                "--enable-candidate-dictation" => {
                    ensure!(
                        product_candidate,
                        "dictation activation requires an isolated candidate bundle"
                    );
                    no_hotkeys = false;
                    no_audio = false;
                    quit_after_ms = None;
                }
                "--show-settings" => show_settings = true,
                "--allow-test-sources" => allow_test_sources = true,
                "--help" | "-h" => {
                    println!(
                        "Vocal More Rust macOS desktop\n\n--show-settings\n--data-dir PATH\n--native-library PATH\n--no-import\n--no-hotkeys\n--version\n\nAcceptance only: --no-audio --allow-test-sources --fixture-http URL --fixture-websocket URL\n--capsule-fixtures PATH --quit-after-ms MS"
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
        if capsule_fixtures.is_some() || allow_test_sources || quit_after_ms.is_some() || no_audio {
            ensure!(
                explicit_data && no_import && no_hotkeys,
                "acceptance mode requires an explicit --data-dir, --no-import and --no-hotkeys"
            );
        }
        let mut backend = Options::new(data.unwrap_or_else(|| legacy_dir.join("rust-backend")));
        if !no_import && !explicit_data {
            backend.import_from = Some(legacy_dir);
        }
        backend.native = if no_audio {
            None
        } else {
            native
                .or_else(find_native_library)
                .map(NativeAudio::load)
                .transpose()?
        };
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
        } else if !product_candidate
            && !no_audio
            && !allow_test_sources
            && quit_after_ms.is_none()
            && capsule_fixtures.is_none()
        {
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn candidate_defaults_are_isolated_passive_and_do_not_import_credentials() {
        let fixture = tempfile::tempdir().unwrap();
        let contents = fixture.path().join("Candidate.app/Contents");
        std::fs::create_dir_all(contents.join("Resources")).unwrap();
        std::fs::write(
            contents.join("Resources/BluetoothProductCandidate"),
            b"candidate",
        )
        .unwrap();
        let home = fixture.path().join("synthetic-home");
        let options = DesktopOptions::parse_from(
            home.clone(),
            Some(contents.join("MacOS/Candidate")),
            std::iter::empty(),
        )
        .unwrap();
        assert_eq!(
            options.backend.data_dir,
            home.join("Library/Application Support/Vocal More Bluetooth Candidate")
        );
        assert!(
            options.backend.import_from.is_none()
                && options.backend.environment_key.is_none()
                && options.backend.native.is_none()
        );
        assert!(options.no_hotkeys && options.show_settings);
        assert_eq!(options.quit_after_ms, Some(900_000));
        assert!(!home.exists());
    }
    #[test]
    fn dictation_activation_is_rejected_outside_the_candidate_bundle() {
        let fixture = tempfile::tempdir().unwrap();
        assert!(
            DesktopOptions::parse_from(
                fixture.path().into(),
                None,
                ["--enable-candidate-dictation".to_owned()].into_iter()
            )
            .is_err()
        );
    }

    #[test]
    fn finder_prototype_start_keeps_v2_pairing_profile_and_disables_private_sources() {
        let fixture = tempfile::tempdir().unwrap();
        let contents = fixture.path().join("Development.app/Contents");
        std::fs::create_dir_all(contents.join("Resources")).unwrap();
        std::fs::write(
            contents.join("Resources/BluetoothFixedTextPrototype"),
            b"fixed only",
        )
        .unwrap();
        let home = fixture.path().join("synthetic-home");
        let options = DesktopOptions::parse_from(
            home.clone(),
            Some(contents.join("MacOS/Development")),
            std::iter::empty(),
        )
        .unwrap();
        assert_eq!(
            options.backend.data_dir,
            home.join("Library/Application Support/Vocal More Bluetooth Dev V2")
        );
        assert!(options.backend.import_from.is_none());
        assert!(options.backend.environment_key.is_none());
        assert!(options.backend.native.is_none());
        assert!(options.no_hotkeys && options.show_settings);
        assert_eq!(options.quit_after_ms, Some(900_000));
    }

    #[test]
    fn unmarked_audio_disabled_acceptance_cannot_fall_back_to_real_user_data() {
        let fixture = tempfile::tempdir().unwrap();
        let flags = ["--no-audio", "--no-import", "--no-hotkeys"];
        assert!(
            DesktopOptions::parse_from(
                fixture.path().join("synthetic-home"),
                Some(fixture.path().join("unmarked-development")),
                flags.into_iter().map(str::to_owned),
            )
            .is_err()
        );
    }
}
