// SPDX-License-Identifier: GPL-3.0-only
#![cfg(unix)]
use anyhow::{Result, ensure};
use std::{path::Path, process::Command, time::Duration};
use tokio::time::{sleep, timeout};
use vocal_more_core::{
    audio::{Dsp, NativeAudio, Source},
    recording::RecordingStore,
    runtime::{Host, Phase, StartRequest, wait_terminal},
};

struct Gate(libloading::Library);
impl Gate {
    fn count(&self, name: &str) -> i32 {
        unsafe {
            self.0
                .get::<unsafe extern "C" fn() -> i32>(name.as_bytes())
                .unwrap()()
        }
    }
    fn switch_route(&self) {
        unsafe {
            self.0
                .get::<unsafe extern "C" fn()>(b"vm_test_switch_route\0")
                .unwrap()()
        }
    }
    fn release(&self) {
        // SAFETY: test-only zero-argument functions from our compiled fixture.
        unsafe {
            self.0
                .get::<unsafe extern "C" fn()>(b"vm_test_release\0")
                .unwrap()()
        }
    }
    fn entered(&self) -> bool {
        unsafe {
            self.0
                .get::<unsafe extern "C" fn() -> i32>(b"vm_test_entered\0")
                .unwrap()()
                != 0
        }
    }
}
impl Drop for Gate {
    fn drop(&mut self) {
        self.release();
    }
}

fn fixture(dir: &Path) -> Result<(NativeAudio, Gate)> {
    fixture_mode(dir, false)
}
fn fixture_mode(dir: &Path, warm: bool) -> Result<(NativeAudio, Gate)> {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let path = dir.join("fixture.dylib");
    let output = Command::new("cc")
        .arg(if cfg!(target_os = "macos") {
            "-dynamiclib"
        } else {
            "-shared"
        })
        .args(["-fPIC", "-pthread", "-O1", "-I"])
        .arg(manifest.join("../../../native/audio/include"))
        .arg(if warm {
            "-DVM_TEST_WARM"
        } else {
            "-DVM_TEST_LEGACY"
        })
        .arg(manifest.join("tests/native_fixture.c"))
        .arg("-o")
        .arg(&path)
        .output()?;
    ensure!(
        output.status.success(),
        "C fixture compile failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let native = NativeAudio::load(&path)?;
    // SAFETY: the library was compiled immediately above from the test fixture.
    let gate = Gate(unsafe { libloading::Library::new(path)? });
    Ok((native, gate))
}

#[tokio::test]
async fn prepared_graph_is_silent_and_reuse_resets_audio_and_invalidates_changed_routes()
-> Result<()> {
    let temp = tempfile::tempdir()?;
    let (native, gate) = fixture_mode(temp.path(), true)?;
    let store = RecordingStore::open(temp.path().join("store")).await?;
    let mut host = Host::new(store.clone(), Some(native.clone()), None);
    assert!(native.prepare_idle(&request(8.0).source)?);
    until(&host, |_| native.diagnostics()["warm_prepared"] == true).await?;
    assert_eq!(gate.count("vm_test_prepares"), 1);
    assert_eq!(gate.count("vm_test_starts"), 0);
    assert_eq!(
        gate.count("vm_test_reads"),
        0,
        "idle preparation captured audio"
    );
    for index in 0..2 {
        let started = std::time::Instant::now();
        let generation = host.start(request(8.0)).await?.generation;
        until(&host, |h| h.status().pcm_bytes >= 6400).await?;
        assert!(host.status().startup_timing_ms.first_pcm_ms.unwrap() < 150.0);
        println!(
            "prepared session {index}: first PCM {:?} ms, 5 blocks {:?}",
            host.status().startup_timing_ms.first_pcm_ms,
            started.elapsed()
        );
        host.finish(generation)?;
        let status = wait_terminal(&host, Duration::from_secs(2)).await?;
        assert_eq!(status.phase, Phase::Completed, "{status:?}");
        until(&host, |h| !h.status().native_busy).await?;
        assert_eq!(native.diagnostics()["last_session"]["warm_reused"], true);
        assert_eq!(native.diagnostics()["warm_prepared"], true);
        let record = store
            .list()
            .await?
            .into_iter()
            .find(|r| Some(r.id) == status.recording_id)
            .unwrap();
        let mut wav = hound::WavReader::open(store.directory().join(&record.filename))?;
        // Each new session starts at block 42, followed by 43, 44... . No tail
        // from the previous paused graph may leak into the next recording.
        let samples: Vec<i16> = wav
            .samples::<i16>()
            .collect::<std::result::Result<_, _>>()?;
        assert!(
            samples
                .chunks(640)
                .enumerate()
                .all(|(n, b)| b.iter().all(|s| *s == 42 + n as i16))
        );
        let reads = gate.count("vm_test_reads");
        sleep(Duration::from_millis(40)).await;
        assert_eq!(
            gate.count("vm_test_reads"),
            reads,
            "paused graph kept capturing"
        );
    }
    assert_eq!(gate.count("vm_test_creates"), 1);
    assert_eq!(gate.count("vm_test_destroys"), 0);
    gate.switch_route();
    let generation = host.start(request(8.0)).await?.generation;
    until(&host, |h| h.status().pcm_bytes > 0).await?;
    assert_eq!(gate.count("vm_test_creates"), 2);
    assert_eq!(gate.count("vm_test_destroys"), 1);
    assert_eq!(native.diagnostics()["warm_reused"], false);
    host.finish(generation)?;
    wait_terminal(&host, Duration::from_secs(2)).await?;
    until(&host, |h| !h.status().native_busy).await?;
    let generation = host.start(request(1.0)).await?.generation;
    until(&host, |h| h.status().pcm_bytes > 0).await?;
    assert_eq!(
        gate.count("vm_test_creates"),
        3,
        "config change reused stale DSP"
    );
    host.cancel(generation)?;
    wait_terminal(&host, Duration::from_secs(2)).await?;
    host.shutdown().await?;
    drop(host);
    drop(native);
    timeout(Duration::from_secs(2), async {
        while gate.count("vm_test_destroys") != 3 {
            sleep(Duration::from_millis(5)).await;
        }
    })
    .await?;
    Ok(())
}

#[tokio::test]
async fn cancelled_capture_during_prepare_never_starts_late_and_shutdown_releases_graph()
-> Result<()> {
    let temp = tempfile::tempdir()?;
    let (native, gate) = fixture_mode(temp.path(), true)?;
    let mut host = Host::new(
        RecordingStore::open(temp.path().join("store")).await?,
        Some(native.clone()),
        None,
    );
    assert!(native.prepare_idle(&request(7.0).source)?);
    until(&host, |_| gate.entered()).await?;
    let generation = host.start(request(7.0)).await?.generation;
    until(&host, |h| h.status().native_busy).await?;
    let started = std::time::Instant::now();
    host.cancel(generation)?;
    assert_eq!(
        wait_terminal(&host, Duration::from_secs(2)).await?.phase,
        Phase::Cancelled
    );
    assert!(started.elapsed() < Duration::from_millis(500));
    assert!(host.start(request(1.0)).await.is_err());
    gate.release();
    until(&host, |h| !h.status().native_busy).await?;
    assert_eq!(gate.count("vm_test_starts"), 0);
    assert_eq!(gate.count("vm_test_reads"), 0);
    host.shutdown().await?;
    drop(host);
    drop(native);
    timeout(Duration::from_secs(2), async {
        while gate.count("vm_test_destroys") != 1 {
            sleep(Duration::from_millis(5)).await;
        }
    })
    .await?;
    Ok(())
}

#[tokio::test]
async fn warm_pause_stall_and_failure_preserve_cleanup_quarantine() -> Result<()> {
    for gain in [3.0, 6.0] {
        let temp = tempfile::tempdir()?;
        let (native, gate) = fixture_mode(temp.path(), true)?;
        let mut host = Host::new(
            RecordingStore::open(temp.path().join("store")).await?,
            Some(native.clone()),
            None,
        );
        let generation = host.start(request(gain)).await?.generation;
        until(&host, |h| h.status().pcm_bytes > 0).await?;
        host.finish(generation)?;
        assert_eq!(
            wait_terminal(&host, Duration::from_secs(2)).await?.phase,
            Phase::Failed
        );
        assert!(host.status().native_busy);
        assert!(host.start(request(1.0)).await.is_err());
        assert_eq!(gate.count("vm_test_destroys"), 0);
        if gain == 3.0 {
            gate.release();
            until(&host, |h| !h.status().native_busy).await?;
            assert_eq!(gate.count("vm_test_destroys"), 1);
            assert_eq!(native.diagnostics()["warm_prepared"], false);
        } else {
            assert!(host.status().native_quarantined);
        }
        host.shutdown().await?;
    }
    Ok(())
}
fn request(gain: f32) -> StartRequest {
    StartRequest {
        source: Source::Native {
            dsp: Dsp {
                gain,
                ..Default::default()
            },
        },
        asr: None,
    }
}
async fn until(host: &Host, predicate: impl Fn(&Host) -> bool) -> Result<()> {
    timeout(Duration::from_secs(2), async {
        while !predicate(host) {
            sleep(Duration::from_millis(5)).await;
        }
    })
    .await?;
    Ok(())
}

#[tokio::test]
async fn microphone_captures_before_slow_asr_ready_and_keeps_the_first_blocks() -> Result<()> {
    use vocal_more_core::runtime::{ExternalAsr, NetworkInput};
    let temp = tempfile::tempdir()?;
    let (native, _gate) = fixture(temp.path())?;
    let mut host = Host::new(
        RecordingStore::open(temp.path().join("store")).await?,
        Some(native),
        None,
    );
    let (sent, received) = tokio::sync::oneshot::channel();
    let asr = ExternalAsr {
        model: "slow-fixture".into(),
        audio_limit_bytes: None,
        run: Box::new(move |mut input, cancel, reporter| {
            Box::pin(async move {
                tokio::select! {
                    _ = cancel.cancelled() => anyhow::bail!("cancelled"),
                    _ = sleep(Duration::from_millis(600)) => {},
                }
                reporter.ready();
                let mut bytes = 0;
                while let Some(value) = input.recv().await {
                    match value {
                        NetworkInput::Pcm(pcm) => {
                            assert!(
                                pcm.chunks_exact(2)
                                    .all(|p| i16::from_le_bytes([p[0], p[1]]) == 42)
                            );
                            bytes += pcm.len();
                        }
                        NetworkInput::Finish => break,
                    }
                }
                let _ = sent.send(bytes);
                Ok("fixture".into())
            })
        }),
    };
    let started = std::time::Instant::now();
    let generation = host
        .start_external(request(1.0).source, asr)
        .await?
        .generation;
    until(&host, |h| h.status().pcm_bytes > 0).await?;
    let first_pcm = started.elapsed();
    println!("600 ms handshake fixture: first PCM = {first_pcm:?}");
    assert!(
        first_pcm < Duration::from_millis(300),
        "microphone waited for ASR: {first_pcm:?}"
    );
    assert!(!host.status().asr_ready);
    until(&host, |h| h.status().pcm_bytes >= 6400).await?;
    // Finish while the connection is still starting. Its bounded queue must
    // retain all early audio and deliver Finish after the final PCM block.
    host.finish(generation)?;
    let status = wait_terminal(&host, Duration::from_secs(2)).await?;
    assert_eq!(status.phase, Phase::Completed, "{status:?}");
    assert_eq!(received.await? as u64, status.pcm_bytes);
    host.shutdown().await?;
    Ok(())
}

#[tokio::test]
async fn configured_device_and_larger_native_blocks_reach_bounded_core() -> Result<()> {
    let temp = tempfile::tempdir()?;
    let (native, _gate) = fixture(temp.path())?;
    assert_eq!(native.list_devices()?[0]["name"], "fixture mic");
    assert_eq!(native.microphone_authorization()?, 3);
    let mut host = Host::new(
        RecordingStore::open(temp.path().join("store")).await?,
        Some(native.clone()),
        None,
    );
    let status = host
        .start(StartRequest {
            source: Source::ConfiguredNative {
                dsp: Dsp {
                    gain: 1.0,
                    ..Default::default()
                },
                device: vocal_more_core::audio::DeviceOptions {
                    input_device: Some("fixture mic".into()),
                    capture_channels: 3,
                    ..Default::default()
                },
            },
            asr: None,
        })
        .await?;
    until(&host, |h| h.status().pcm_bytes >= 5120).await?;
    host.finish(status.generation)?;
    let status = wait_terminal(&host, Duration::from_secs(2)).await?;
    assert_eq!(status.phase, Phase::Completed, "{status:?}");
    assert!(status.pcm_bytes >= 5120);
    until(&host, |h| !h.status().native_busy).await?;
    let measured = native.diagnostics();
    assert_eq!(measured["phase"], "inactive");
    assert_eq!(measured["last_session"]["source_sample_rate_hz"], 48000.0);
    assert_eq!(measured["last_session"]["agc_enabled_observed"], false);
    assert_eq!(measured["last_session"]["gain_control_verified"], true);
    assert_eq!(
        measured["last_session"]["startup_timing_ms"]["first_pcm"],
        2.0
    );
    Ok(())
}

#[tokio::test]
async fn cancelled_start_is_quarantined_until_owner_destroys_handle() -> Result<()> {
    let temp = tempfile::tempdir()?;
    let (native, gate) = fixture(temp.path())?;
    let mut host = Host::new(
        RecordingStore::open(temp.path().join("store")).await?,
        Some(native),
        None,
    );
    let generation = host.start(request(2.0)).await?.generation;
    until(&host, |_| gate.entered()).await?;
    host.cancel(generation)?;
    assert_eq!(
        wait_terminal(&host, Duration::from_secs(2)).await?.phase,
        Phase::Cancelled
    );
    assert!(host.status().native_busy);
    assert!(host.start(request(4.0)).await.is_err());
    gate.release();
    until(&host, |h| !h.status().native_busy).await?;
    let generation = host.start(request(4.0)).await?.generation;
    until(&host, |h| h.status().pcm_bytes > 0).await?;
    host.finish(generation)?;
    assert_eq!(
        wait_terminal(&host, Duration::from_secs(2)).await?.phase,
        Phase::Completed
    );
    host.shutdown().await?;
    Ok(())
}

#[tokio::test]
async fn slow_stop_and_missing_pcm_have_deadlines_and_keep_host_responsive() -> Result<()> {
    let temp = tempfile::tempdir()?;
    let (native, gate) = fixture(temp.path())?;
    let mut host = Host::new(
        RecordingStore::open(temp.path().join("store")).await?,
        Some(native),
        None,
    );
    let generation = host.start(request(3.0)).await?.generation;
    until(&host, |h| h.status().pcm_bytes > 0).await?;
    host.finish(generation)?;
    let status = wait_terminal(&host, Duration::from_secs(2)).await?;
    assert_eq!(status.phase, Phase::Failed);
    assert!(status.native_busy);
    assert!(status.error.unwrap().contains("drain deadline"));
    gate.release();
    until(&host, |h| !h.status().native_busy).await?;
    host.start(request(5.0)).await?;
    let status = wait_terminal(&host, Duration::from_secs(5)).await?;
    assert_eq!(status.phase, Phase::Failed);
    assert!(status.error.unwrap().contains("no PCM"));
    host.shutdown().await?;
    Ok(())
}

#[tokio::test]
async fn failed_cleanup_blocks_new_capture_without_destroying_an_unproven_handle() -> Result<()> {
    let temp = tempfile::tempdir()?;
    let (native, _gate) = fixture(temp.path())?;
    let mut host = Host::new(
        RecordingStore::open(temp.path().join("store")).await?,
        Some(native),
        None,
    );
    let generation = host.start(request(6.0)).await?.generation;
    until(&host, |h| h.status().pcm_bytes > 0).await?;
    host.finish(generation)?;
    let status = wait_terminal(&host, Duration::from_secs(2)).await?;
    assert_eq!(status.phase, Phase::Failed);
    assert!(status.native_busy && status.native_quarantined);
    assert!(
        host.start(request(4.0))
            .await
            .unwrap_err()
            .to_string()
            .contains("restart host")
    );
    let generation = host.start(StartRequest::default()).await?.generation;
    host.finish(generation)?;
    assert_eq!(
        wait_terminal(&host, Duration::from_secs(2)).await?.phase,
        Phase::Completed
    );
    host.shutdown().await?;
    Ok(())
}
