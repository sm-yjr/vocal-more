use anyhow::Result;
use serde_json::{Value, json};
use vocal_more_backend::{
    dictionary::{Dictionary, Entry},
    learning::{self, Decision},
    learning_store::LearningStore,
};

#[test]
fn correction_splitting_and_eligibility_match_python() -> Result<()> {
    let cases: Value = serde_json::from_str(include_str!("fixtures/learning-validation.json"))?;
    for case in cases.as_array().unwrap() {
        let decision = Decision::parse(case["decision"].clone())?;
        let entries: Vec<Entry> = serde_json::from_value(case["entries"].clone())?;
        assert_eq!(
            serde_json::to_value(learning::validate(&decision, &case["evidence"], &entries))?,
            case["expected"],
            "{case}"
        );
    }
    let cases: Value = serde_json::from_str(include_str!("fixtures/learning-candidates.json"))?;
    for case in cases.as_array().unwrap() {
        let before = case["evidence"]["baseline_text"].as_str().unwrap();
        let after = case["evidence"]["edited_text"].as_str().unwrap();
        let groups = learning::edit_groups(before, after);
        let stats = json!([
            groups.iter().map(|g| g[1] - g[0]).sum::<usize>(),
            groups.iter().map(|g| g[3] - g[2]).sum::<usize>(),
            groups.len()
        ]);
        assert_eq!(stats, case["statistics"], "{before:?} => {after:?}");
        assert_eq!(
            json!(learning::split(&case["evidence"], "fixture")),
            case["expected"],
            "{before:?} => {after:?}"
        );
    }
    Ok(())
}
fn evidence(before: &str, after: &str, recording: &str, observation: &str) -> Value {
    learning::evidence(
        &json!({"raw_text":before,"pasted_text":before,"baseline_text":before,
        "edited_text":after,"recording_id":recording,"observation_id":observation}),
    )
    .unwrap()
}
fn decision(term: &str, alias: &str) -> Decision {
    Decision::parse(json!({"decision":"add","term":term,"aliases":[alias],"term_type":"proper_name","confidence":0.01})).unwrap()
}
fn identity(before: &str, after: &str) -> bool {
    learning::identity(before) == learning::identity(after)
}

#[test]
fn independent_confirmation_persists_and_undo_preserves_manual_edits() -> Result<()> {
    let temp = tempfile::tempdir()?;
    let db = temp.path().join("learning.sqlite3");
    let dict = temp.path().join("dictionary.yaml");
    let mut dictionary = Dictionary::open(&dict)?;
    let mut store = LearningStore::open(&db, 0.0)?;
    let first = evidence("阿里云白练", "阿里云百炼", "recording-1", "observation-1");
    let candidates = learning::split(&first, "observation-1");
    store.enqueue(&candidates, 1.0)?;
    assert!(store.enqueue(&candidates, 1.0)?.is_empty());
    let job = store.claim(1.0)?.unwrap();
    let classified = learning::validate(
        &decision("阿里云百炼", "阿里云白练"),
        &job.evidence,
        &dictionary.entries,
    );
    let authorized = store.authorize(&job, classified.clone(), 1.0, identity)?;
    assert_eq!(authorized.action, "review");
    store.finish(&job.id, "review", &authorized, None, 1.0)?;
    assert_eq!(store.get(&job.id)?.unwrap().evidence["pasted_text"], "");
    drop(store);
    let mut store = LearningStore::open(&db, 2.0)?;
    for (recording, observation, expected) in [
        ("recording-1", "observation-2", "review"),
        ("recording-2", "observation-3", "add"),
    ] {
        store.enqueue(
            &learning::split(
                &evidence("阿里云白练", "阿里云百炼", recording, observation),
                observation,
            ),
            2.0,
        )?;
        let job = store.claim(2.0)?.unwrap();
        let authorized = store.authorize(&job, classified.clone(), 2.0, identity)?;
        assert_eq!(authorized.action, expected);
        if expected == "add" {
            store.apply(&job, &authorized, &mut dictionary, "automatic", 2.0)?;
            dictionary.add("阿里云百炼", &json!(["用户新加的别名"]))?;
            assert!(store.undo(&job.id, &mut dictionary, 3.0)?);
            assert_eq!(dictionary.entries[0].aliases, vec!["用户新加的别名"]);
            assert_eq!(store.get(&job.id)?.unwrap().status, "reverted");
        } else {
            store.finish(&job.id, "review", &authorized, None, 2.0)?;
        }
    }
    store.enqueue(
        &learning::split(
            &evidence("阿里云白练", "阿里云百炼", "recording-3", "observation-4"),
            "observation-4",
        ),
        4.0,
    )?;
    let job = store.claim(4.0)?.unwrap();
    let authorized = store.authorize(&job, classified, 4.0, identity)?;
    assert_eq!(authorized.reason_code, "conflicting_corrections");
    store.finish(&job.id, "review", &authorized, None, 4.0)?;
    assert!(store.approve(&job.id, &mut dictionary, 5.0)?);
    assert!(dictionary.entries[0].aliases.contains(&"阿里云白练".into()));
    assert!(!store.approve(&job.id, &mut dictionary, 5.0)?);
    Ok(())
}

#[test]
fn interrupted_journal_keeps_original_undo_and_retry_deadlines() -> Result<()> {
    let temp = tempfile::tempdir()?;
    let db = temp.path().join("learning.sqlite3");
    let mut dictionary = Dictionary::open(&temp.path().join("dictionary.yaml"))?;
    let mut store = LearningStore::open(&db, 0.0)?;
    store.enqueue(
        &learning::split(&evidence("github", "GitHub", "r1", "o1"), "o1"),
        0.0,
    )?;
    let job = store.claim(0.0)?.unwrap();
    let d = store.authorize(&job, decision("GitHub", "github"), 0.0, identity)?;
    assert_eq!(d.reason_code, "same_term_correction");
    dictionary.add_journaled("GitHub", &json!(["github"]), |mutation| {
        store.journal(&job.id, &d, mutation, "automatic", 0.0)
    })?;
    drop(store);
    let mut store = LearningStore::open(&db, 1.0)?;
    let recovered = store.claim(1.0)?.unwrap();
    assert!(recovered.term_created);
    store.apply(&recovered, &d, &mut dictionary, "automatic", 1.0)?;
    assert_eq!(store.claim_notification("o1")?, Some(vec!["GitHub".into()]));
    assert_eq!(store.claim_notification("o1")?, None);
    assert!(store.undo(&job.id, &mut dictionary, 2.0)?);
    assert!(dictionary.entries.is_empty());
    store.enqueue(
        &learning::split(&evidence("github", "GitHub", "r2", "o2"), "o2"),
        3.0,
    )?;
    let mut now = 3.0;
    for attempt in 1..=5 {
        let job = store.claim(now)?.unwrap();
        assert_eq!(job.attempt_count, attempt);
        store.failure(&job, "provider HTTP 429", true, now)?;
        if attempt < 5 {
            assert!(store.claim(now + 1.0)?.is_none());
            now = store.next_due()?.unwrap();
        } else {
            assert_eq!(store.get(&job.id)?.unwrap().status, "failed");
            assert_eq!(store.get(&job.id)?.unwrap().evidence["raw_text"], "");
        }
    }
    Ok(())
}

#[test]
fn ui_listing_override_approve_rename_supersede_and_safe_history_clear() -> Result<()> {
    let temp = tempfile::tempdir()?;
    let db = temp.path().join("learning.sqlite3");
    let mut dictionary = Dictionary::open(&temp.path().join("dictionary.yaml"))?;
    let mut store = LearningStore::open(&db, 0.0)?;

    // 1. Seed one early review item and 105 later ignored items; review must never be truncated.
    store.enqueue(
        &learning::split(
            &evidence("百练", "百炼", "r-early", "obs-early"),
            "obs-early",
        ),
        1.0,
    )?;
    let early = store.claim(1.0)?.unwrap();
    let early_decision = store.authorize(&early, decision("百炼", "百练"), 1.0, identity)?;
    assert_eq!(early_decision.action, "review");
    store.finish(&early.id, "review", &early_decision, None, 1.0)?;

    for i in 0..105 {
        let obs = format!("obs-noise-{i}");
        store.enqueue(
            &learning::split(&evidence("旧词", "新词", &format!("r-{i}"), &obs), &obs),
            10.0 + i as f64,
        )?;
        let job = store.claim(10.0 + i as f64)?.unwrap();
        store.finish(
            &job.id,
            "ignored",
            &Decision::ignore("not_a_reusable_term"),
            None,
            10.0 + i as f64,
        )?;
    }

    let ui_jobs = store.list_for_ui(100)?;
    assert!(
        ui_jobs
            .iter()
            .any(|job| job.id == early.id && job.status == "review"),
        "early review item was truncated behind 105 ignored rows"
    );

    // 2. Approve with edited term & aliases, then rename term, recreate old name, and verify undo does not delete new unrelated entry.
    assert!(store.approve_with_override(
        &early.id,
        &mut dictionary,
        Some("阿里云百炼"),
        Some(&json!(["百练", "白练"])),
        200.0
    )?);
    assert_eq!(dictionary.entries[0].term, "阿里云百炼");
    assert_eq!(dictionary.entries[0].aliases, vec!["百练", "白练"]);

    dictionary.update(
        "阿里云百炼",
        "百炼平台",
        &json!(["百练"]),
        Some(&["百练".into(), "白练".into()]),
    )?;
    assert_eq!(store.supersede_renamed_term("阿里云百炼", 201.0)?, 1);
    dictionary.add("阿里云百炼", &json!(["手动新建别名"]))?;
    assert!(store.undo(&early.id, &mut dictionary, 202.0)?);
    let recreated = dictionary
        .entries
        .iter()
        .find(|entry| entry.term == "阿里云百炼")
        .expect("recreated entry must not be deleted by superseded undo");
    assert_eq!(recreated.aliases, vec!["手动新建别名"]);

    // 3. Mixed-status sibling observation: ignored sibling must stay until processing sibling completes.
    let sibling_a = json!({
        "raw_text": "foo bar", "pasted_text": "foo bar", "baseline_text": "foo bar",
        "edited_text": "Foo Bar", "recording_id": "r-mix", "observation_id": "obs-mix"
    });
    let sibling_b = json!({
        "raw_text": "foo bar", "pasted_text": "foo bar", "baseline_text": "foo bar",
        "edited_text": "Foo Baz", "recording_id": "r-mix", "observation_id": "obs-mix"
    });
    let mixed = store.enqueue(&[sibling_a, sibling_b], 300.0)?;
    assert_eq!(mixed.len(), 2);
    let first_mix = store.claim(300.0)?.unwrap();
    store.finish(
        &first_mix.id,
        "ignored",
        &Decision::ignore("not_a_reusable_term"),
        None,
        301.0,
    )?;

    let cleared = store.clear_history()?;
    assert_eq!(
        cleared, 106,
        "105 noise rows + 1 reverted row should be cleared"
    );
    assert!(
        store.get(&first_mix.id)?.is_some(),
        "ignored sibling of active observation must not be cleared"
    );

    // Finish second sibling -> now group can claim notification and later be cleared.
    let second_mix = store.claim(302.0)?.unwrap();
    store.finish(
        &second_mix.id,
        "ignored",
        &Decision::ignore("not_a_reusable_term"),
        None,
        303.0,
    )?;
    assert_eq!(store.claim_notification("obs-mix")?, Some(vec![]));
    assert_eq!(store.clear_history()?, 2);
    Ok(())
}
