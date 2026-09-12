// SPDX-License-Identifier: GPL-3.0-only
//! Deterministic correction eligibility, separate from model classification.
use crate::{catalog::CONTRACT, dictionary::Entry, provider::Provider};
use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::{HashMap, HashSet},
    sync::LazyLock,
    time::Duration,
};
use tokio_util::sync::CancellationToken;
use unicode_casefold::UnicodeCaseFold;
use unicode_normalization::UnicodeNormalization;

pub fn fold(value: &str) -> String {
    value.case_fold().collect()
}
pub fn identity(value: &str) -> String {
    value.nfc().case_fold().collect()
}
pub fn same_identity(
    before: &str,
    after: &str,
    native: Option<&vocal_more_core::audio::NativeAudio>,
) -> bool {
    if identity(before) == identity(after) {
        return true;
    }
    if !before
        .chars()
        .chain(after.chars())
        .any(|ch| ('\u{3400}'..='\u{9fff}').contains(&ch))
    {
        return false;
    }
    let Some(native) = native else { return false };
    let romanize = |s: &str| {
        native.transliterate(s).map(|s| {
            identity(&s)
                .chars()
                .filter(|c| !c.is_whitespace())
                .collect::<String>()
        })
    };
    matches!((romanize(before),romanize(after)),(Some(a),Some(b)) if !a.is_empty() && a==b)
}
pub fn string<'a>(v: &'a Value, key: &str) -> &'a str {
    v[key].as_str().unwrap_or("")
}
fn chars(value: &str) -> Vec<char> {
    value.chars().collect()
}
pub(crate) fn slice(value: &str, start: usize, end: usize) -> String {
    value
        .chars()
        .skip(start)
        .take(end.saturating_sub(start))
        .collect()
}
pub(crate) fn length(value: &str) -> usize {
    value.chars().count()
}

pub fn evidence(payload: &Value) -> Result<Value> {
    ensure!(payload.is_object(), "learning evidence must be an object");
    let mut result = json!({});
    for (name, limit) in [
        ("raw_text", 8000),
        ("pasted_text", 8000),
        ("original_text", 8000),
        ("baseline_text", 8000),
        ("edited_text", 8000),
        ("app_bundle_id", 512),
        ("app_name", 512),
        ("mode_name", 128),
        ("observation_id", 128),
        ("candidate_before_text", 8000),
        ("candidate_after_text", 8000),
    ] {
        result[name] = json!(
            payload
                .get(name)
                .map(crate::config::py_string)
                .unwrap_or_default()
                .chars()
                .take(limit)
                .collect::<String>()
        );
    }
    result["recording_id"] = if payload["recording_id"].is_null() {
        Value::Null
    } else {
        json!(
            crate::config::py_string(&payload["recording_id"])
                .chars()
                .take(512)
                .collect::<String>()
        )
    };
    result["candidate_index"] = json!(payload["candidate_index"].as_u64().unwrap_or(0));
    result["candidate_count"] = json!(payload["candidate_count"].as_u64().unwrap_or(1).max(1));
    for name in [
        "candidate_before_change_start",
        "candidate_before_change_end",
        "candidate_after_change_start",
        "candidate_after_change_end",
    ] {
        result[name] = payload[name]
            .as_i64()
            .map(|n| json!(n.max(0)))
            .unwrap_or(Value::Null);
    }
    Ok(result)
}

/// Ratcliff/Obershelp matching blocks, with difflib's earliest-start tie break
/// and autojunk disabled. All offsets count Unicode scalars, as Python does.
pub(crate) fn opcodes(before: &str, after: &str) -> Vec<(&'static str, [usize; 4])> {
    let a = chars(before);
    let b = chars(after);
    let mut index: HashMap<char, Vec<usize>> = HashMap::new();
    for (j, ch) in b.iter().enumerate() {
        index.entry(*ch).or_default().push(j);
    }
    let mut queue = vec![(0, a.len(), 0, b.len())];
    let mut matches = Vec::new();
    while let Some((alo, ahi, blo, bhi)) = queue.pop() {
        let (mut best_a, mut best_b, mut best_size) = (alo, blo, 0);
        let mut previous = HashMap::new();
        for (i, ch) in a.iter().enumerate().take(ahi).skip(alo) {
            let mut current = HashMap::new();
            for &j in index
                .get(ch)
                .into_iter()
                .flatten()
                .filter(|j| **j >= blo && **j < bhi)
            {
                let size = if j > 0 {
                    *previous.get(&(j - 1)).unwrap_or(&0) + 1
                } else {
                    1
                };
                current.insert(j, size);
                if size > best_size {
                    (best_a, best_b, best_size) = (i + 1 - size, j + 1 - size, size);
                }
            }
            previous = current;
        }
        if best_size == 0 {
            continue;
        }
        if alo < best_a && blo < best_b {
            queue.push((alo, best_a, blo, best_b));
        }
        if best_a + best_size < ahi && best_b + best_size < bhi {
            queue.push((best_a + best_size, ahi, best_b + best_size, bhi));
        }
        matches.push((best_a, best_b, best_size));
    }
    matches.sort_unstable();
    matches.push((a.len(), b.len(), 0));
    let mut result = Vec::new();
    let (mut ai, mut bi) = (0, 0);
    for (aj, bj, size) in matches {
        if ai < aj || bi < bj {
            result.push((
                if ai < aj && bi < bj {
                    "replace"
                } else if ai < aj {
                    "delete"
                } else {
                    "insert"
                },
                [ai, aj, bi, bj],
            ));
        }
        if size > 0 {
            result.push(("equal", [aj, aj + size, bj, bj + size]));
        }
        ai = aj + size;
        bi = bj + size;
    }
    result
}
pub fn edit_groups(before: &str, after: &str) -> Vec<[usize; 4]> {
    let mut groups: Vec<[usize; 4]> = Vec::new();
    for (tag, [a, b, c, d]) in opcodes(before, after) {
        if tag == "equal" {
            continue;
        }
        if let Some(previous) = groups.last_mut()
            && a - previous[1] <= 3
            && c - previous[3] <= 3
        {
            previous[1] = b;
            previous[3] = d;
        } else {
            groups.push([a, b, c, d]);
        }
    }
    groups
}
pub fn split(evidence: &Value, observation_id: &str) -> Vec<Value> {
    let before = string(evidence, "baseline_text");
    let after = string(evidence, "edited_text");
    if before.is_empty() || after.is_empty() || before == after {
        return vec![];
    }
    let mut seen = HashSet::new();
    let groups: Vec<_> = edit_groups(before, after)
        .into_iter()
        .filter(|[a, b, c, d]| {
            let removed = slice(before, *a, *b);
            let added = slice(after, *c, *d);
            removed
                .chars()
                .chain(added.chars())
                .any(char::is_alphanumeric)
                && seen.insert((fold(&removed), fold(&added)))
        })
        .collect();
    if groups.len() > 5 {
        return vec![];
    }
    groups
        .iter()
        .enumerate()
        .map(|(index, [a, b, c, d])| {
            let mut candidate = evidence.clone();
            let before_start = a.saturating_sub(16);
            let after_start = c.saturating_sub(16);
            candidate["observation_id"] = json!(observation_id);
            candidate["candidate_index"] = json!(index);
            candidate["candidate_count"] = json!(groups.len());
            candidate["candidate_before_text"] =
                json!(slice(before, before_start, (*b + 16).min(length(before))));
            candidate["candidate_after_text"] =
                json!(slice(after, after_start, (*d + 16).min(length(after))));
            candidate["candidate_before_change_start"] = json!(a - before_start);
            candidate["candidate_before_change_end"] = json!(b - before_start);
            candidate["candidate_after_change_start"] = json!(c - after_start);
            candidate["candidate_after_change_end"] = json!(d - after_start);
            candidate
        })
        .collect()
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct Decision {
    #[serde(alias = "decision")]
    pub action: String,
    #[serde(default)]
    pub term: String,
    #[serde(default)]
    pub aliases: Vec<String>,
    #[serde(default)]
    pub confidence: f64,
    #[serde(default)]
    pub reason_code: String,
    #[serde(default = "other")]
    pub term_type: String,
}
fn other() -> String {
    "other".into()
}
impl Decision {
    pub fn parse(payload: Value) -> Result<Self> {
        let value: Self =
            serde_json::from_value(payload).context("invalid dictionary-learning JSON")?;
        ensure!(
            ["add", "ignore", "review"].contains(&value.action.as_str()),
            "invalid decision"
        );
        ensure!(
            ["proper_name", "technical_term", "abbreviation", "other"]
                .contains(&value.term_type.as_str()),
            "invalid term type"
        );
        ensure!(
            value.confidence.is_finite() && (0.0..=1.0).contains(&value.confidence),
            "invalid confidence"
        );
        Ok(Self {
            reason_code: value.reason_code.chars().take(128).collect(),
            ..value
        })
    }
    pub fn ignore(reason: &str) -> Self {
        Self {
            action: "ignore".into(),
            term: String::new(),
            aliases: vec![],
            confidence: 0.0,
            reason_code: reason.into(),
            term_type: other(),
        }
    }
}
fn occurrences(value: &str, text: &str) -> Vec<usize> {
    let a = chars(text);
    let b = chars(value);
    if b.is_empty() || b.len() > a.len() {
        return vec![];
    }
    a.windows(b.len())
        .enumerate()
        .filter_map(|(i, s)| (s == b).then_some(i))
        .collect()
}
fn overlap(value: &str, scope: &str, start: Option<u64>, end: Option<u64>) -> bool {
    let places = occurrences(value, scope);
    let (Some(start), Some(end)) = (start, end) else {
        return !places.is_empty();
    };
    let (start, end) = (start as usize, end as usize);
    places.iter().any(|&i| {
        if start == end {
            i <= start && start <= i + length(value)
        } else {
            i < end && i + length(value) > start
        }
    })
}
fn explains(term: &str, alias: &str, evidence: &Value) -> bool {
    let before = if string(evidence, "candidate_before_text").is_empty() {
        string(evidence, "baseline_text")
    } else {
        string(evidence, "candidate_before_text")
    };
    let after = if string(evidence, "candidate_after_text").is_empty() {
        string(evidence, "edited_text")
    } else {
        string(evidence, "candidate_after_text")
    };
    let offsets = [
        "candidate_before_change_start",
        "candidate_before_change_end",
        "candidate_after_change_start",
        "candidate_after_change_end",
    ]
    .map(|k| evidence[k].as_u64());
    let [Some(bs), Some(be), Some(as_), Some(ae)] = offsets else {
        return occurrences(alias, before).iter().any(|&i| {
            format!(
                "{}{}{}",
                slice(before, 0, i),
                term,
                slice(before, i + length(alias), length(before))
            ) == after
        });
    };
    let (bs, be, as_, ae) = (bs as usize, be as usize, as_ as usize, ae as usize);
    if bs > be || be > length(before) || as_ > ae || ae > length(after) {
        return false;
    }
    occurrences(alias, before).iter().any(|&i| {
        i <= bs
            && i + length(alias) >= be
            && occurrences(term, after).iter().any(|&j| {
                j <= as_
                    && j + length(term) >= ae
                    && slice(before, i, bs) == slice(after, j, as_)
                    && slice(before, be, i + length(alias)) == slice(after, ae, j + length(term))
            })
    })
}
fn lexical(value: &str) -> bool {
    length(value) <= 60
        && value.split_whitespace().count() <= 5
        && !value.contains([
            '\n', '\r', '。', '，', ',', '；', ';', '！', '？', '!', '?', '：', ':',
        ])
        && value
            .chars()
            .filter(|ch| ('\u{4e00}'..='\u{9fff}').contains(ch))
            .count()
            <= 16
}
static NUMERIC: LazyLock<regex::Regex> = LazyLock::new(|| {
    regex::Regex::new(r"^[零〇一二两三四五六七八九十百千万亿\d年月日号点时分秒周星期礼拜上下午夜凌晨早晚:：./\-\s]+$").unwrap()
});
static DIGITS: LazyLock<regex::Regex> = LazyLock::new(|| regex::Regex::new(r"\d").unwrap());
pub fn validate(decision: &Decision, evidence: &Value, entries: &[Entry]) -> Decision {
    if decision.action == "ignore" {
        return Decision::ignore(if decision.reason_code.is_empty() {
            "model_ignored"
        } else {
            &decision.reason_code
        });
    }
    let term = decision.term.trim();
    let mut seen = HashSet::new();
    let aliases: Vec<String> = decision
        .aliases
        .iter()
        .map(|s| s.trim())
        .filter(|s| !s.is_empty() && *s != term && seen.insert(fold(s)))
        .map(str::to_owned)
        .collect();
    let reject = |reason| Decision::ignore(reason);
    if term.is_empty() {
        return reject("empty_term");
    }
    if length(term) > 60 {
        return reject("term_too_long");
    }
    if aliases.is_empty() {
        return reject("empty_aliases");
    }
    if !term.chars().any(char::is_alphanumeric)
        || aliases
            .iter()
            .any(|a| !a.chars().any(char::is_alphanumeric))
    {
        return reject("non_lexical_mapping");
    }
    if term.contains(['\n', '\r', '。', '！', '？', '!', '?']) {
        return reject("sentence_sized_term");
    }
    let after = string(evidence, "candidate_after_text");
    let before = string(evidence, "candidate_before_text");
    if !after.is_empty()
        && !overlap(
            term,
            after,
            evidence["candidate_after_change_start"].as_u64(),
            evidence["candidate_after_change_end"].as_u64(),
        )
    {
        return reject("term_not_in_candidate");
    }
    if !string(evidence, "edited_text").contains(term) {
        return reject("term_not_in_edited_text");
    }
    if !before.is_empty()
        && aliases.iter().any(|a| {
            !overlap(
                a,
                before,
                evidence["candidate_before_change_start"].as_u64(),
                evidence["candidate_before_change_end"].as_u64(),
            )
        })
    {
        return reject("alias_not_in_candidate");
    }
    if aliases.iter().any(|a| {
        !string(evidence, "pasted_text").contains(a) && !string(evidence, "raw_text").contains(a)
    }) {
        return reject("alias_not_in_pasted_text");
    }
    if NUMERIC.is_match(term) && aliases.iter().all(|a| NUMERIC.is_match(a)) {
        return reject("numeric_or_date_change");
    }
    let digits = |s: &str| DIGITS.find_iter(s).map(|m| m.as_str()).collect::<String>();
    if aliases.iter().any(|a| digits(a) != digits(term)) {
        return reject("numeric_identifier_change");
    }
    if !lexical(term) || aliases.iter().any(|a| !lexical(a)) {
        return reject("non_lexical_phrase");
    }
    if aliases.iter().any(|a| length(a) < 2) {
        return reject("alias_too_broad");
    }
    if aliases.iter().any(|a| !explains(term, a, evidence)) {
        return reject("mapping_does_not_explain_edit");
    }
    if !["proper_name", "technical_term", "abbreviation"].contains(&decision.term_type.as_str()) {
        return reject("not_a_reusable_term");
    }
    let mut mapping = HashMap::new();
    for entry in entries {
        let canonical = fold(&entry.term);
        mapping
            .entry(canonical.clone())
            .or_insert(canonical.clone());
        for alias in &entry.aliases {
            mapping.insert(fold(alias), canonical.clone());
        }
    }
    let canonical = fold(term);
    if std::iter::once(term)
        .chain(aliases.iter().map(String::as_str))
        .any(|s| {
            mapping
                .get(&fold(s))
                .is_some_and(|target| target != &canonical)
        })
    {
        return reject("dictionary_conflict");
    }
    Decision {
        term: term.into(),
        aliases,
        reason_code: if decision.reason_code.is_empty() {
            "model_classification".into()
        } else {
            decision.reason_code.clone()
        },
        ..decision.clone()
    }
}
impl Provider {
    pub async fn classify_correction(
        &self,
        evidence: &Value,
        cancel: &CancellationToken,
    ) -> Result<Decision> {
        let request = json!({"model":"qwen3.7-plus","messages":[{"role":"system","content":CONTRACT["learning_prompt"]},
            {"role":"user","content":serde_json::to_string(evidence)?}],"temperature":0,"max_tokens":256,"stream":false,
            "response_format":{"type":"json_object"},"enable_thinking":false});
        let response = tokio::time::timeout(
            Duration::from_secs(30),
            self.request_json(
                "/compatible-mode/v1/chat/completions",
                request,
                false,
                true,
                cancel,
                None,
            ),
        )
        .await
        .context("dictionary learning request timed out")??;
        Decision::parse(
            serde_json::from_str(&response.text).context("invalid dictionary-learning JSON")?,
        )
    }
}
