// SPDX-License-Identifier: GPL-3.0-only
//! Run exclusively with the private GNOME capsule runner.
use super::*;
use crate::bridge::{BackendDriver, UiEvent};
use std::{path::PathBuf, time::Instant};
use vocal_more_backend::application::Options;

#[test]
#[ignore = "requires the isolated GNOME acceptance session"]
fn linux_real_shell_capsule_transport() {
    let output = PathBuf::from(
        std::env::var_os("VOCAL_MORE_GNOME_TEST_OUTPUT").expect("use isolated runner"),
    );
    fn wait(mut check: impl FnMut() -> bool) {
        let end = Instant::now() + Duration::from_secs(15);
        while !check() {
            assert!(Instant::now() < end, "GNOME capsule transport timed out");
            std::thread::sleep(Duration::from_millis(25));
        }
    }
    wait(|| output.join("transport-ready").exists());
    let temporary = tempfile::tempdir().unwrap();
    let (driver, sink, events) =
        BackendDriver::start(Options::new(temporary.path().into())).unwrap();
    let capsule = ShellCapsule::new(sink.clone()).unwrap();
    let model = Model {
        active: true,
        state: "recording".into(),
        text: "实际点击与焦点验证".into(),
        level: 0.72,
        ..Model::default()
    };
    capsule.update(model.clone());
    wait(|| capsule.available());
    std::fs::write(output.join("transport-phase"), "recording").unwrap();
    for action in ["cancel", "stop"] {
        let mut received = false;
        wait(|| {
            while let Ok(event) = events.try_recv() {
                if let UiEvent::Request(request) = event {
                    assert_eq!(request.method, action);
                    if action == "cancel" {
                        assert!(sink.paste_epoch() > 0);
                    }
                    driver.send(request);
                    received = true;
                }
            }
            received
        });
        std::fs::write(
            output.join("transport-phase"),
            if action == "cancel" {
                "processing"
            } else {
                "restart"
            },
        )
        .unwrap();
        if action == "cancel" {
            capsule.update(Model {
                state: "processing".into(),
                text: "处理中".into(),
                ..model.clone()
            });
        }
    }
    wait(|| output.join("transport-disabled").exists());
    wait(|| !capsule.available());
    std::fs::write(output.join("transport-unavailable"), "yes").unwrap();
    wait(|| output.join("transport-enabled").exists());
    wait(|| capsule.available());
    std::fs::write(output.join("transport-phase"), "recovered").unwrap();
    wait(|| output.join("transport-recovery-checked").exists());
    let hide = Instant::now();
    capsule.update(Model::default());
    std::fs::write(output.join("transport-phase"), "hide").unwrap();
    wait(|| output.join("transport-hide-checked").exists());
    let latency = hide.elapsed();
    assert!(
        latency < Duration::from_millis(300),
        "completed capsule lingered: {latency:?}"
    );
    std::fs::write(
        output.join("capsule-hide-latency.json"),
        json!({"hidden_ms":latency.as_millis(),"limit_ms":300}).to_string(),
    )
    .unwrap();
    capsule.close();
    wait(|| capsule.finished());
    driver.close();
    wait(|| driver.finished());
    assert_eq!(driver.shutdown_failures(), Default::default());
    std::fs::write(output.join("transport-phase"), "closed").unwrap();
}
