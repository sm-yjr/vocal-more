// SPDX-License-Identifier: GPL-3.0-only
use serde_json::{Value, json};
use vocal_more_backend::config::{Config, ConfigRepository};

fn set(value: &mut Value, key: &str, updated: &Value) {
    let parts: Vec<_> = key.split('.').collect();
    let mut node = value;
    for part in &parts[..parts.len() - 1] {
        node = &mut node[*part];
    }
    node[parts[parts.len() - 1]] = updated.clone();
}

#[test]
fn configuration_updates_match_python_reference() {
    let cases: Vec<Value> =
        serde_json::from_str(include_str!("fixtures/config-updates.json")).unwrap();
    for case in cases {
        let mut config = Config::default();
        let result = config.apply_update(case["key"].as_str().unwrap(), &case["input"]);
        if case["error"] == true {
            assert!(result.is_err(), "expected error: {case}");
            continue;
        }
        result.unwrap_or_else(|e| panic!("{case}: {e}"));
        let mut expected = Config::default().0;
        for (key, value) in case["changes"].as_object().unwrap() {
            set(&mut expected, key, value);
        }
        assert_eq!(config.0, expected, "case {case}");
    }
}

#[test]
fn old_configuration_migration_matches_python_reference() {
    let cases: Vec<Value> =
        serde_json::from_str(include_str!("fixtures/config-migrations.json")).unwrap();
    for case in cases {
        assert_eq!(
            Config::from_persisted(&case["input"]).0,
            case["expected"],
            "case {case}"
        );
    }
}

#[test]
fn persisted_config_reload_and_failed_form_are_transactional() -> anyhow::Result<()> {
    let temp = tempfile::tempdir()?;
    let path = temp.path().join("config.yaml");
    let mut repo = ConfigRepository::open(&path)?;
    repo.update("api_key", &json!("synthetic-secret"))?;
    repo.update("audio.gain", &json!(4))?;
    let before = std::fs::read(&path)?;
    assert!(
        repo.update_form(&json!({"audio":{"gain":8,"nonexistent":true}}))
            .is_err()
    );
    assert_eq!(before, std::fs::read(&path)?);
    let reload = ConfigRepository::open(&path)?;
    assert_eq!(reload.config.get("audio.gain").as_f64(), Some(4.0));
    assert_eq!(reload.config.public()["api_key"], "");
    assert_eq!(reload.config.get("api_key"), "synthetic-secret");
    Ok(())
}

#[test]
fn update_channel_accepts_stable_and_beta() -> anyhow::Result<()> {
    let mut config = Config::default();
    assert!(config.get("update_channel").is_null());

    config.apply_update("update_channel", &json!("beta"))?;
    assert_eq!(config.get("update_channel"), "beta");

    config.apply_update("update_channel", &json!("stable"))?;
    assert_eq!(config.get("update_channel"), "stable");

    // Retired alpha preference: dropped so the build's own channel applies.
    config.apply_update("update_channel", &json!("nightly"))?;
    assert!(config.get("update_channel").is_null());
    Ok(())
}

#[test]
fn proxy_url_is_validated_and_normalized() -> anyhow::Result<()> {
    let mut config = Config::default();
    config.apply_update("network.proxy_url", &json!("socks5://LOCALHOST:1080/"))?;
    assert_eq!(config.get("network.proxy_url"), "socks5://localhost:1080");

    for invalid in [
        "https://localhost:7890",
        "http://localhost",
        "http://user:pass@localhost:7890",
    ] {
        assert!(
            config
                .apply_update("network.proxy_url", &json!(invalid))
                .is_err()
        );
    }
    Ok(())
}

#[test]
fn ipv6_proxy_remains_usable_after_persistence_and_reload() -> anyhow::Result<()> {
    let temp = tempfile::tempdir()?;
    let path = temp.path().join("config.yaml");
    let mut repo = ConfigRepository::open(&path)?;
    for proxy in ["socks5://[::1]:1080", "http://[2001:db8::1]:7890"] {
        repo.update("network.proxy_url", &json!(proxy))?;
        assert_eq!(repo.config.get("network.proxy_url"), proxy);
        reqwest::Proxy::all(repo.config.get("network.proxy_url").as_str().unwrap())?;
        let reloaded = ConfigRepository::open(&path)?;
        assert_eq!(reloaded.config.get("network.proxy_url"), proxy);
    }
    Ok(())
}

#[test]
fn skipped_onboarding_survives_reload() -> anyhow::Result<()> {
    let temp = tempfile::tempdir()?;
    let path = temp.path().join("config.yaml");
    let mut repo = ConfigRepository::open(&path)?;
    repo.update("ui.onboarding_skipped", &json!(true))?;
    let reload = ConfigRepository::open(&path)?;
    assert_eq!(reload.config.get("ui.onboarding_skipped"), &json!(true));
    assert_eq!(reload.config.get("ui.onboarding_completed"), &json!(false));
    Ok(())
}

#[test]
fn capsule_position_and_custom_coords_persist_safely() -> anyhow::Result<()> {
    let temp = tempfile::tempdir()?;
    let path = temp.path().join("config.yaml");
    let mut repo = ConfigRepository::open(&path)?;
    repo.update("ui.capsule_position", &json!("top"))?;
    repo.update("ui.capsule_custom_x", &json!(1.5))?;
    repo.update("ui.capsule_custom_y", &json!(-0.2))?;
    let reload = ConfigRepository::open(&path)?;
    assert_eq!(reload.config.get("ui.capsule_position"), &json!("top"));
    assert_eq!(reload.config.get("ui.capsule_custom_x"), &json!(1.0));
    assert_eq!(reload.config.get("ui.capsule_custom_y"), &json!(0.0));
    repo.update("ui.capsule_position", &json!("invalid-mode"))?;
    assert_eq!(repo.config.get("ui.capsule_position"), &json!("bottom"));
    repo.update("ui.capsule_custom_x", &Value::Null)?;
    assert!(repo.config.get("ui.capsule_custom_x").is_null());
    Ok(())
}

#[test]
fn optional_ui_preferences_survive_reload() -> anyhow::Result<()> {
    let temp = tempfile::tempdir()?;
    let path = temp.path().join("config.yaml");
    let mut repo = ConfigRepository::open(&path)?;
    let preferences = [
        ("ui.color_scheme", json!("dark")),
        ("ui.interface_material", json!("classic")),
        ("ui.reduce_motion", json!(true)),
        ("ui.app_icon", json!("wave")),
        ("ui.icon_visibility", json!("dock_only")),
    ];
    for (key, value) in &preferences {
        assert!(repo.config.get(key).is_null(), "{key}");
        repo.update(key, value)?;
    }

    let reloaded = ConfigRepository::open(&path)?;
    for (key, value) in &preferences {
        assert_eq!(reloaded.config.get(key), value, "{key}");
    }
    Ok(())
}

#[test]
fn invalid_ui_choices_fall_back_to_defaults() -> anyhow::Result<()> {
    let mut config = Config::default();
    for (key, explicit, default) in [
        ("ui.color_scheme", "light", "auto"),
        ("ui.interface_material", "classic", "glass"),
        ("ui.app_icon", "night", "default"),
        ("ui.icon_visibility", "menu_and_dock", "menu_only"),
    ] {
        config.apply_update(key, &json!(explicit))?;
        assert_eq!(config.get(key), explicit, "{key}");
        config.apply_update(key, &json!("invalid"))?;
        assert_eq!(config.get(key), default, "{key}");
    }
    Ok(())
}

#[test]
fn reduce_motion_uses_config_boolean_coercion() -> anyhow::Result<()> {
    let mut config = Config::default();
    for (input, expected) in [
        (Value::Null, false),
        (json!(" On "), true),
        (json!("invalid"), true),
        (json!(0), false),
        (json!(true), true),
        (json!(false), false),
    ] {
        config.apply_update("ui.reduce_motion", &input)?;
        assert_eq!(config.get("ui.reduce_motion"), &json!(expected), "{input}");
    }
    Ok(())
}
