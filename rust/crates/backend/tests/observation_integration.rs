use serde_json::{Value, json};
use vocal_more_backend::observation::{Observation, Progress, plausible, project};
fn snapshot(value: &str) -> Value {
    json!({"pid":42,"target_id":"editor","value":value,"app_bundle_id":"com.example.editor","app_name":"Editor","is_secure":false,"selection_start":0,"selection_length":0})
}
fn start(original: Value, text: &str) -> Observation {
    Observation::prepare(
        "o1".into(),
        original,
        &json!({"text":text,"raw_text":text,"mode":"realtime_long","recording_id":"r1"}),
        0.0,
    )
    .unwrap()
}
fn result(progress: Progress) -> Option<Value> {
    match progress {
        Progress::Finished(value) => value,
        _ => panic!("expected finished observation"),
    }
}

#[test]
fn fast_focus_switch_reconstructs_paste_and_keeps_only_its_changed_span() {
    let mut observation = start(snapshot(""), "github");
    let corrected = snapshot("GitHub");
    let learned = result(observation.poll(&Value::Null, &corrected, 0.1, false)).unwrap();
    assert_eq!(learned["baseline_text"], "github");
    assert_eq!(learned["edited_text"], "GitHub");
    assert_eq!(
        project("before github after", "BEFORE GitHub AFTER", 7, 13),
        "GitHub"
    );
    assert_eq!(
        project("before github after", "BEFORE github AFTER", 7, 13),
        "github"
    );
}
#[test]
fn reverted_edits_secure_fields_and_unsettled_deadlines_do_not_learn() {
    let mut observation = start(snapshot(""), "github");
    assert!(matches!(
        observation.poll(&snapshot("github"), &Value::Null, 0.1, false),
        Progress::Pending
    ));
    observation.poll(&snapshot("GitHub"), &Value::Null, 1.0, false);
    assert!(result(observation.poll(&snapshot("github"), &Value::Null, 1.1, true)).is_none());
    let mut observation = start(snapshot(""), "github");
    observation.poll(&snapshot("github"), &Value::Null, 0.1, false);
    observation.poll(&snapshot("GitHub"), &Value::Null, 14.9, false);
    assert!(result(observation.deadline(15.2)).is_none());
    let mut secure = snapshot("secret");
    secure["is_secure"] = json!(true);
    assert!(
        Observation::prepare("x".into(), secure.clone(), &json!({"text":"secret"}), 0.0).is_none()
    );
    assert!(result(observation.poll(&Value::Null, &secure, 15.0, false)).is_none());
}
#[test]
fn next_paste_commits_previous_edit_without_observing_new_paste() {
    let mut observation = start(snapshot(""), "github");
    observation.poll(&snapshot("github"), &Value::Null, 0.1, false);
    assert_eq!(
        result(observation.poll(&snapshot("GitHub"), &Value::Null, 0.15, true)).unwrap()["edited_text"],
        "GitHub"
    );
    assert!(plausible("露那", "Luna"));
    assert!(!plausible(
        "一段需要保留的听写内容",
        "这已经变成完全不同的长句子"
    ));
    assert!(!plausible("一段需要保留的听写内容", ""));
}

#[test]
fn manual_paste_waits_for_real_baseline_before_learning_unicode_edits() {
    let mut original = snapshot("前缀：后缀");
    original["selection_start"] = json!(3);
    let mut observation = Observation::prepare_manual(
        "manual".into(),
        original.clone(),
        &json!({"text":"github","raw_text":"github","recording_id":"manual-r"}),
        0.,
    )
    .unwrap();
    assert!(matches!(
        observation.poll(&original, &Value::Null, 5., false),
        Progress::Pending
    ));
    assert!(!observation.expired(5.));
    // Unrelated typing does not manufacture a paste baseline.
    assert!(matches!(
        observation.poll(&snapshot("前缀：hello后缀"), &Value::Null, 6., false),
        Progress::Pending
    ));
    assert!(matches!(
        observation.poll(&snapshot("前缀：github后缀"), &Value::Null, 7., false),
        Progress::Pending
    ));
    observation.poll(&snapshot("前缀：GitHub后缀"), &Value::Null, 8., false);
    let evidence =
        result(observation.poll(&Value::Null, &snapshot("前缀：GitHub后缀"), 9., false)).unwrap();
    assert_eq!(evidence["baseline_text"], "github");
    assert_eq!(evidence["edited_text"], "GitHub");
    assert_eq!(evidence["recording_id"], "manual-r");
}

#[test]
fn manual_paste_without_observed_insertion_never_learns() {
    for (focused, retained, time) in [
        (snapshot("GitHub"), Value::Null, 15.),
        (Value::Null, snapshot("GitHub"), 0.1),
    ] {
        let mut observation = Observation::prepare_manual(
            "manual".into(),
            snapshot(""),
            &json!({"text":"github"}),
            0.,
        )
        .unwrap();
        assert!(result(observation.poll(&focused, &retained, time, false)).is_none());
    }
}

#[test]
fn terminal_delivery_never_starts_correction_learning() {
    let target = serde_json::json!({"pid":42,"target_id":"terminal","terminal":true,"value":null});
    let paste = serde_json::json!({"text":"synthetic test only"});
    assert!(
        vocal_more_backend::observation::Observation::prepare(
            "terminal".into(),
            target,
            &paste,
            0.
        )
        .is_none()
    );
}
