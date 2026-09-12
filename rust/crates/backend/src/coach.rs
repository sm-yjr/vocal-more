// SPDX-License-Identifier: GPL-3.0-only
use crate::catalog::CONTRACT;
use serde_json::{Value, json};
use std::{collections::HashMap, sync::LazyLock};

static PATTERNS: LazyLock<HashMap<&'static str, regex::Regex>> = LazyLock::new(|| {
    [
        "_ACTION_RE",
        "_CONTEXT_RE",
        "_OUTPUT_RE",
        "_BOUNDARY_RE",
        "_TECHNICAL_RE",
        "_CONTEXT_NEEDED_RE",
    ]
    .into_iter()
    .map(|name| {
        (
            name,
            regex::RegexBuilder::new(CONTRACT["prompt_coach"]["patterns"][name].as_str().unwrap())
                .case_insensitive(true)
                .build()
                .unwrap(),
        )
    })
    .collect()
});
pub fn assess(text: &str, language: &str) -> Value {
    let matched = |name: &str| PATTERNS[name].is_match(text.trim());
    let mut present = vec![];
    for (facet, pattern) in [
        ("goal", "_ACTION_RE"),
        ("context", "_CONTEXT_RE"),
        ("output", "_OUTPUT_RE"),
        ("boundaries", "_BOUNDARY_RE"),
    ] {
        if matched(pattern) {
            present.push(facet);
        }
    }
    let suggested =
        if text.chars().filter(|c| c.is_alphanumeric()).count() < 6 || !present.contains(&"goal") {
            Some("goal")
        } else if !present.contains(&"output") {
            Some("output")
        } else if !present.contains(&"boundaries") && matched("_TECHNICAL_RE") {
            Some("boundaries")
        } else if !present.contains(&"context") && matched("_CONTEXT_NEEDED_RE") {
            Some("context")
        } else {
            None
        };
    let locale = if language.to_lowercase().starts_with("zh") {
        "zh"
    } else {
        "en"
    };
    json!({"present":present,"suggested":suggested,"ready":suggested.is_none(),"hint":CONTRACT["prompt_coach"]["hints"][locale][suggested.unwrap_or("ready")]})
}
pub fn waveform(rms: f64, ceiling: f64) -> f64 {
    if !rms.is_finite() || rms <= 0.0 {
        return 0.0;
    }
    ((20.0 * rms.log10() + 60.0) / (ceiling.clamp(-30.0, 0.0) + 60.0)).clamp(0.0, 1.0)
}
