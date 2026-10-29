// SPDX-License-Identifier: GPL-3.0-only
#![cfg(target_os = "linux")]
use serde_json::json;
use vocal_more_backend::application::{Application, Options};

#[tokio::test]
async fn imported_apple_capture_settings_report_linux_software_capabilities() {
    let data = tempfile::tempdir().unwrap();
    let app = Application::open(Options::new(data.path().to_owned()))
        .await
        .unwrap();
    app.call(
        "set_config",
        json!({"key":"audio.capture_backend","value":"voice_processing"}),
    )
    .await
    .unwrap();
    app.call(
        "set_config",
        json!({"key":"audio.capture_channels","value":3}),
    )
    .await
    .unwrap();
    app.call(
        "set_config",
        json!({"key":"audio.gain_mode","value":"automatic"}),
    )
    .await
    .unwrap();
    let snapshot = app.call("snapshot", json!({})).await.unwrap();
    let status = &snapshot["audio_input_status"];
    assert_eq!(status["native_backend"], "portaudio");
    assert_eq!(status["capture_channels"], 1);
    assert_eq!(status["gain_control"], "software_fallback");
    assert_eq!(status["echo_cancellation"], "unavailable");
    assert_eq!(status["processing_mode"], "standard");
    assert!(
        !snapshot["environment_checks"]
            .as_array()
            .unwrap()
            .iter()
            .any(|check| matches!(
                check["key"].as_str(),
                Some("accessibility" | "hotkey_listener")
            ))
    );
    app.call("shutdown", json!({})).await.unwrap();
}
