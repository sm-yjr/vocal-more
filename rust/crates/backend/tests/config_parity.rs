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
