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
