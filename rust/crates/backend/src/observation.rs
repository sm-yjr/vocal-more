// SPDX-License-Identifier: GPL-3.0-only
//! The frontend supplies bounded AX snapshots. Timing, target admission,
//! pasted-span projection and edit eligibility remain in the Rust backend.
use crate::learning::{self, length, slice, string};
use serde_json::{Value, json};
use std::sync::LazyLock;

fn normalized(value: &str) -> String {
    value
        .replace("\r\n", "\n")
        .replace('\r', "\n")
        .chars()
        .filter(|c| !['\u{200b}', '\u{200c}', '\u{200d}', '\u{feff}'].contains(c))
        .collect()
}
fn bounded(value: &Value) -> Option<String> {
    if value.is_null() || value["is_secure"] == true {
        return None;
    }
    let text = normalized(string(value, "value"));
    (length(&text) <= 8000).then_some(text)
}
fn same(a: &Value, b: &Value) -> bool {
    !a.is_null() && !b.is_null() && a["pid"] == b["pid"] && a["target_id"] == b["target_id"]
}
fn bounds(before: &str, after: &str) -> (usize, usize, usize) {
    let a: Vec<_> = before.chars().collect();
    let b: Vec<_> = after.chars().collect();
    let prefix = a.iter().zip(&b).take_while(|(a, b)| a == b).count();
    let mut suffix = 0;
    while suffix < a.len() - prefix
        && suffix < b.len() - prefix
        && a[a.len() - suffix - 1] == b[b.len() - suffix - 1]
    {
        suffix += 1;
    }
    (prefix, a.len() - suffix, b.len() - suffix)
}
pub fn project(baseline: &str, edited: &str, start: usize, end: usize) -> String {
    if length(baseline) > 8000 || length(edited) > 8000 {
        return String::new();
    }
    let mut result = String::new();
    for (tag, [a, b, c, d]) in learning::opcodes(baseline, edited) {
        if tag == "insert" {
            if start <= a && a <= end {
                result.push_str(&slice(edited, c, d));
            }
            continue;
        }
        let overlap_start = start.max(a);
        let overlap_end = end.min(b);
        if overlap_start >= overlap_end {
            continue;
        }
        if tag == "equal" {
            result.push_str(&slice(edited, c + overlap_start - a, c + overlap_end - a));
        } else if tag == "replace" {
            result.push_str(&slice(edited, c, d));
        }
    }
    result
}
static SHORT_BEFORE: LazyLock<regex::Regex> =
    LazyLock::new(|| regex::Regex::new(r"^[\w+#./-]+$").unwrap());
static SHORT_AFTER: LazyLock<regex::Regex> =
    LazyLock::new(|| regex::Regex::new(r"^[\w+#./-]+(?: [\w+#./-]+){0,3}$").unwrap());
pub fn plausible(before: &str, after: &str) -> bool {
    if before.is_empty()
        || after.is_empty()
        || before == after
        || (length(after) as f64) < (length(before) as f64 * 0.2)
    {
        return false;
    }
    let groups = learning::edit_groups(before, after);
    if groups.is_empty() || groups.len() > 5 {
        return false;
    }
    let removed: usize = groups.iter().map(|g| g[1] - g[0]).sum();
    let added: usize = groups.iter().map(|g| g[3] - g[2]).sum();
    let a = before.trim_matches([' ', '。', '！', '？', '!', '?', '.', ',', '，']);
    let b = after.trim_matches([' ', '。', '！', '？', '!', '?', '.', ',', '，']);
    let short = (2..=8).contains(&length(a))
        && (2..=24).contains(&length(b))
        && SHORT_BEFORE.is_match(a)
        && SHORT_AFTER.is_match(b);
    (short
        || (removed as f64 <= length(before) as f64 * 0.5
            && removed + added <= 4.max(length(before) * 2)))
        && removed.max(added) <= 8000
}
pub enum Progress {
    Pending,
    Finished(Option<Value>),
}
pub struct Observation {
    pub id: String,
    original: Value,
    raw: String,
    pasted: String,
    recording: Value,
    mode: String,
    original_text: String,
    baseline: Option<String>,
    original_replaced: String,
    span: Option<(usize, usize)>,
    latest: String,
    last_change: Option<f64>,
    post_deadline: f64,
    deadline: f64,
}
impl Observation {
    pub fn prepare(id: String, original: Value, paste: &Value, now: f64) -> Option<Self> {
        let original_text = bounded(&original)?;
        let pasted = normalized(string(paste, "text"));
        if pasted.is_empty() || length(&pasted) > 8000 {
            return None;
        }
        let selection = original["selection_length"].as_u64().unwrap_or(0) as usize;
        if length(&original_text) + length(&pasted) - length(&original_text).min(selection) > 8000 {
            return None;
        }
        Some(Self {
            id,
            original,
            raw: string(paste, "raw_text").chars().take(8000).collect(),
            pasted,
            recording: paste["recording_id"].clone(),
            mode: string(paste, "mode").into(),
            original_text,
            baseline: None,
            original_replaced: String::new(),
            span: None,
            latest: String::new(),
            last_change: None,
            post_deadline: now + 1.0,
            deadline: now + 16.0,
        })
    }
    pub fn target_id(&self) -> &str {
        string(&self.original, "target_id")
    }
    pub fn app_bundle_id(&self) -> &str {
        string(&self.original, "app_bundle_id")
    }
    pub fn expired(&self, now: f64) -> bool {
        now >= if self.baseline.is_some() {
            self.deadline
        } else {
            self.post_deadline
        }
    }
    fn expected(&self) -> Option<String> {
        let start = self.original["selection_start"].as_u64()? as usize;
        let count = self.original["selection_length"].as_u64()? as usize;
        if start > length(&self.original_text) {
            return None;
        }
        Some(format!(
            "{}{}{}",
            slice(&self.original_text, 0, start),
            self.pasted,
            slice(
                &self.original_text,
                (start + count).min(length(&self.original_text)),
                length(&self.original_text)
            )
        ))
    }
    fn reconstruct(&self, current: &str) -> Option<String> {
        if let Some(expected) = self.expected() {
            return Some(expected);
        }
        if current.is_empty() || current == self.original_text {
            return None;
        }
        let (start, end, _) = bounds(&self.original_text, current);
        Some(format!(
            "{}{}{}",
            slice(&self.original_text, 0, start),
            self.pasted,
            slice(&self.original_text, end, length(&self.original_text))
        ))
    }
    fn set_baseline(&mut self, baseline: String, current: Option<String>, now: f64) -> bool {
        if length(&baseline) > 8000 {
            return false;
        }
        let (change, end, _) = bounds(&self.original_text, &baseline);
        let positions: Vec<_> = baseline
            .match_indices(&self.pasted)
            .map(|(byte, _)| baseline[..byte].chars().count())
            .collect();
        let Some(start) = positions.into_iter().min_by_key(|p| p.abs_diff(change)) else {
            return false;
        };
        let finish = start + length(&self.pasted);
        self.span = Some((start, finish));
        self.original_replaced = slice(&self.original_text, change, end);
        self.latest = slice(&baseline, start, finish);
        if let Some(current) = current {
            let projected = project(&baseline, &current, start, finish);
            if projected != self.latest {
                self.latest = projected;
                self.last_change = Some(now);
            }
        }
        self.baseline = Some(baseline);
        self.deadline = now + 15.0;
        true
    }
    fn completed(&self, now: f64, committed: bool) -> Progress {
        if !committed && self.last_change.is_some_and(|t| now - t < 0.5) {
            return Progress::Finished(None);
        }
        let Some(baseline) = &self.baseline else {
            return Progress::Finished(None);
        };
        let (start, end) = self.span.unwrap();
        let before = slice(baseline, start, end);
        if !plausible(&before, &self.latest) {
            return Progress::Finished(None);
        }
        Progress::Finished(learning::evidence(&json!({"raw_text":self.raw,"pasted_text":self.pasted,"original_text":self.original_replaced,
            "baseline_text":before,"edited_text":self.latest,"app_bundle_id":self.original["app_bundle_id"],"app_name":self.original["app_name"],
            "mode_name":self.mode,"recording_id":self.recording,"observation_id":self.id})).ok())
    }
    pub fn deadline(&self, now: f64) -> Progress {
        self.completed(now, false)
    }
    pub fn poll(
        &mut self,
        focused: &Value,
        retained: &Value,
        now: f64,
        finishing: bool,
    ) -> Progress {
        let focused_same = same(&self.original, focused);
        let committed = finishing || !focused_same;
        let current = if focused_same { focused } else { retained };
        if same(&self.original, current) && current["is_secure"] == true {
            return Progress::Finished(None);
        }
        if self.baseline.is_none() {
            if !same(&self.original, current) {
                return Progress::Finished(None);
            }
            let Some(value) = bounded(current) else {
                return Progress::Finished(None);
            };
            let reconstruction = if committed {
                self.reconstruct(&value).map(|b| (b, Some(value.clone())))
            } else if let Some(expected) = self.expected()
                && value != self.original_text
            {
                Some((
                    expected.clone(),
                    (value != expected).then_some(value.clone()),
                ))
            } else if value.contains(&self.pasted) {
                Some((value.clone(), None))
            } else {
                self.reconstruct(&value).map(|b| (b, Some(value.clone())))
            };
            if let Some((baseline, observed)) = reconstruction {
                if !self.set_baseline(baseline, observed, now) {
                    return Progress::Finished(None);
                }
                return if committed {
                    self.completed(now, true)
                } else {
                    Progress::Pending
                };
            }
            return if now >= self.post_deadline {
                Progress::Finished(None)
            } else {
                Progress::Pending
            };
        }
        if !committed && now >= self.deadline {
            return self.completed(now, false);
        }
        if same(&self.original, current) {
            let Some(value) = bounded(current) else {
                return Progress::Finished(None);
            };
            if value.is_empty() {
                return self.completed(now, false);
            }
            let (start, end) = self.span.unwrap();
            let projected = project(self.baseline.as_ref().unwrap(), &value, start, end);
            if projected != self.latest {
                self.latest = projected;
                self.last_change = Some(now);
            }
        }
        if committed {
            self.completed(now, true)
        } else {
            Progress::Pending
        }
    }
}
