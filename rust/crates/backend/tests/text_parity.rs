// SPDX-License-Identifier: GPL-3.0-only
use serde_json::{Value, json};
use vocal_more_backend::{
    config::Config,
    dictionary::{self, Dictionary, Entry},
    text::{self, PromptKind},
};

#[test]
fn prompt_builders_match_python_across_modes_languages_and_overrides() {
    let cases: Vec<Value> = serde_json::from_str(include_str!("fixtures/prompts.json")).unwrap();
    for (index, case) in cases.iter().enumerate() {
        let config = Config::from_persisted(&case["config"]);
        for (kind, key) in [
            (PromptKind::System, "system"),
            (PromptKind::Inline, "inline"),
            (PromptKind::Native, "native"),
        ] {
            assert_eq!(
                text::build_prompt(
                    &config,
                    kind,
                    case["dictionary"].as_str().unwrap(),
                    case["context"].as_str().unwrap()
                ),
                case[key],
                "case {index} {key}"
            );
        }
    }
}

#[test]
fn final_text_boundaries_match_python_reference() {
    let cases: Vec<Value> =
        serde_json::from_str(include_str!("fixtures/text-output.json")).unwrap();
    let mut config = Config::default();
    config.apply_update("llm.structured", &json!(true)).unwrap();
    for case in cases {
        let input = case["input"].as_str().unwrap();
        let entries: Vec<Entry> = serde_json::from_value(case["entries"].clone()).unwrap();
        assert_eq!(
            dictionary::normalize_text(input, &entries),
            case["normalized"],
            "normalize {input}"
        );
        assert_eq!(
            text::bilingual(input),
            case["bilingual"],
            "bilingual {input}"
        );
        assert_eq!(
            text::sanitize_prompt(input),
            case["prompt"],
            "prompt {input}"
        );
        assert_eq!(
            text::structured(input, &config),
            case["structured"],
            "structured {input}"
        );
    }
}

#[test]
fn dictionary_updates_reload_and_undo_preserve_later_user_edits() -> anyhow::Result<()> {
    let temp = tempfile::tempdir()?;
    let path = temp.path().join("dictionary.yaml");
    let mut dictionary = Dictionary::open(&path)?;
    let mutation = dictionary.add("Rust", &json!("拉斯特, rustlang，拉斯特"))?;
    assert!(mutation.term_created);
    dictionary.add("Rust", &json!(["new_alias"]))?;
    let mut dictionary = Dictionary::open(&path)?;
    assert_eq!(dictionary.entries[0].aliases.len(), 3);
    dictionary.undo(&mutation)?;
    assert_eq!(dictionary.entries[0].aliases, ["new_alias"]);
    assert_eq!(Dictionary::open(&path)?.entries, dictionary.entries);
    assert_eq!(
        dictionary::corpus(&dictionary.entries, &[json!("Rust"), json!("GPUI")]),
        "Rust\n\nGPUI"
    );
    dictionary.remove("Rust")?;
    assert!(Dictionary::open(&path)?.entries.is_empty());
    Ok(())
}
