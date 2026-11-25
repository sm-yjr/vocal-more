// SPDX-License-Identifier: GPL-3.0-only
//! Privacy-bounded cursor context, dynamic technical hotword extraction, and
//! short-lived cross-turn dictation history for ASR accuracy enhancement.

use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::{HashSet, VecDeque},
    sync::LazyLock,
    time::{Duration, Instant},
};

pub const MAX_BEFORE_CURSOR_CHARS: usize = 200;
pub const MAX_AFTER_CURSOR_CHARS: usize = 100;
pub const MAX_SELECTED_CHARS: usize = 200;
pub const MAX_WINDOW_TITLE_CHARS: usize = 120;
pub const MAX_CONTEXT_TURN_CHARS: usize = 400;
pub const MAX_CONTEXT_TURNS: usize = 5;
pub const MAX_RECENT_TURNS: usize = 3;
pub const RECENT_TURN_TTL: Duration = Duration::from_secs(300);

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct CursorContext {
    #[serde(default)]
    pub app_name: String,
    #[serde(default)]
    pub app_bundle_id: String,
    #[serde(default)]
    pub window_title: String,
    #[serde(default)]
    pub before_cursor: String,
    #[serde(default)]
    pub after_cursor: String,
    #[serde(default)]
    pub selected_text: String,
}

impl CursorContext {
    pub fn is_empty(&self) -> bool {
        self.window_title.is_empty()
            && self.before_cursor.is_empty()
            && self.after_cursor.is_empty()
            && self.selected_text.is_empty()
    }
}

#[derive(Clone, Debug)]
struct RecentTurn {
    text: String,
    app_bundle_id: String,
    recorded_at: Instant,
}

/// Keeps the most recent completed dictations for cross-turn terminology consistency.
#[derive(Debug, Default)]
pub struct RecentHistory {
    turns: VecDeque<RecentTurn>,
}

impl RecentHistory {
    pub fn record(&mut self, text: &str, app_bundle_id: &str, now: Instant) {
        self.prune(now);
        let cleaned = collapse_whitespace(text);
        if cleaned.is_empty() {
            return;
        }
        let text = take_tail_chars(&cleaned, 350);
        self.turns.push_back(RecentTurn {
            text,
            app_bundle_id: app_bundle_id.trim().to_string(),
            recorded_at: now,
        });
        while self.turns.len() > MAX_RECENT_TURNS * 2 {
            self.turns.pop_front();
        }
    }

    pub fn recent_for(&self, app_bundle_id: &str, now: Instant) -> Vec<String> {
        let bundle = app_bundle_id.trim();
        let mut matched: Vec<String> = self
            .turns
            .iter()
            .filter(|turn| now.saturating_duration_since(turn.recorded_at) <= RECENT_TURN_TTL)
            .filter(|turn| {
                bundle.is_empty() || turn.app_bundle_id.is_empty() || turn.app_bundle_id == bundle
            })
            .map(|turn| turn.text.clone())
            .collect();
        if matched.len() > MAX_RECENT_TURNS {
            matched.drain(..matched.len() - MAX_RECENT_TURNS);
        }
        matched
    }

    fn prune(&mut self, now: Instant) {
        while let Some(front) = self.turns.front() {
            if now.saturating_duration_since(front.recorded_at) > RECENT_TURN_TTL {
                self.turns.pop_front();
            } else {
                break;
            }
        }
    }
}

fn collapse_whitespace(input: &str) -> String {
    input.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn take_head_chars(text: &str, limit: usize) -> String {
    text.chars().take(limit).collect()
}

fn take_tail_chars(text: &str, limit: usize) -> String {
    let chars: Vec<char> = text.chars().collect();
    if chars.len() <= limit {
        text.to_string()
    } else {
        chars[chars.len() - limit..].iter().collect()
    }
}

/// Parse a pre-dictation Accessibility snapshot into a bounded `CursorContext`.
/// Fails closed on secure fields or excluded bundle IDs.
pub fn parse_cursor_context(
    snapshot: &Value,
    excluded_bundle_ids: &[String],
) -> Option<CursorContext> {
    if !snapshot.is_object() || snapshot["is_secure"] == true {
        return None;
    }
    let app_bundle_id = snapshot["app_bundle_id"]
        .as_str()
        .unwrap_or("")
        .trim()
        .to_string();
    if !app_bundle_id.is_empty()
        && excluded_bundle_ids
            .iter()
            .any(|excluded| excluded == &app_bundle_id)
    {
        return None;
    }
    let app_name = take_head_chars(snapshot["app_name"].as_str().unwrap_or("").trim(), 80);
    let window_title = take_head_chars(
        &collapse_whitespace(snapshot["window_title"].as_str().unwrap_or("")),
        MAX_WINDOW_TITLE_CHARS,
    );

    let (before_cursor, after_cursor, selected_text) = if snapshot.get("before_cursor").is_some()
        || snapshot.get("after_cursor").is_some()
        || snapshot.get("selected_text").is_some()
    {
        (
            take_tail_chars(
                &collapse_whitespace(snapshot["before_cursor"].as_str().unwrap_or("")),
                MAX_BEFORE_CURSOR_CHARS,
            ),
            take_head_chars(
                &collapse_whitespace(snapshot["after_cursor"].as_str().unwrap_or("")),
                MAX_AFTER_CURSOR_CHARS,
            ),
            take_head_chars(
                &collapse_whitespace(snapshot["selected_text"].as_str().unwrap_or("")),
                MAX_SELECTED_CHARS,
            ),
        )
    } else if let Some(full) = snapshot["value"].as_str() {
        let chars: Vec<char> = full.chars().collect();
        let start = snapshot["selection_start"]
            .as_u64()
            .map(|v| (v as usize).min(chars.len()))
            .unwrap_or(chars.len());
        let len = snapshot["selection_length"]
            .as_u64()
            .map(|v| (v as usize).min(chars.len().saturating_sub(start)))
            .unwrap_or(0);
        let end = start + len;
        let before_start = start.saturating_sub(MAX_BEFORE_CURSOR_CHARS);
        let after_end = (end + MAX_AFTER_CURSOR_CHARS).min(chars.len());
        let before: String = chars[before_start..start].iter().collect();
        let selected: String = chars[start..end].iter().collect();
        let after: String = chars[end..after_end].iter().collect();
        (
            take_tail_chars(&collapse_whitespace(&before), MAX_BEFORE_CURSOR_CHARS),
            take_head_chars(&collapse_whitespace(&after), MAX_AFTER_CURSOR_CHARS),
            take_head_chars(&collapse_whitespace(&selected), MAX_SELECTED_CHARS),
        )
    } else {
        (String::new(), String::new(), String::new())
    };

    let ctx = CursorContext {
        app_name,
        app_bundle_id,
        window_title,
        before_cursor,
        after_cursor,
        selected_text,
    };
    (!ctx.is_empty()).then_some(ctx)
}

/// Validates DashScope/Bailian hotword length rules:
/// - Contains non-ASCII: total scalar chars <= 15
/// - ASCII only: <= 7 whitespace-separated segments, total <= 64 chars
pub fn is_valid_hotword(term: &str) -> bool {
    let trimmed = term.trim();
    if trimmed.is_empty() {
        return false;
    }
    if trimmed.is_ascii() {
        trimmed.len() <= 64 && trimmed.split_whitespace().count() <= 7
    } else {
        trimmed.chars().count() <= 15
    }
}

static BACKTICK_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"`([A-Za-z0-9_.\-/]{2,40})`").unwrap());
static TECH_TOKEN_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\b([A-Za-z][A-Za-z0-9]*(?:[._-][A-Za-z0-9]+)+|[A-Z]{2,12}\d*|[a-z]+[A-Z][A-Za-z0-9]*|[A-Z][a-z]+[A-Z][A-Za-z0-9]*)\b")
        .unwrap()
});

fn is_common_code_noise(token: &str) -> bool {
    matches!(
        token.to_ascii_lowercase().as_str(),
        "true"
            | "false"
            | "null"
            | "none"
            | "todo"
            | "fixme"
            | "http"
            | "https"
            | "utf-8"
            | "json"
            | "yaml"
            | "html"
            | "self"
            | "this"
            | "that"
            | "from"
            | "with"
            | "into"
            | "return"
            | "const"
            | "let"
            | "var"
            | "function"
            | "class"
            | "struct"
            | "impl"
            | "pub"
            | "async"
            | "await"
            | "import"
            | "export"
    )
}

/// Extract technical identifiers, filenames, acronyms, and backtick symbols
/// from cursor context for session-scoped instant hotword boosting.
pub fn extract_dynamic_hotwords(ctx: &CursorContext, limit: usize) -> Vec<String> {
    let mut result = Vec::new();
    let mut seen = HashSet::new();
    let mut push = |raw: &str| {
        if result.len() >= limit {
            return;
        }
        let cleaned = raw
            .trim()
            .trim_matches(|c: char| !c.is_alphanumeric() && c != '_' && c != '-' && c != '.');
        if cleaned.len() < 2
            || cleaned.chars().all(|c| c.is_ascii_digit() || c == '.')
            || is_common_code_noise(cleaned)
            || !is_valid_hotword(cleaned)
        {
            return;
        }
        let key = cleaned.to_ascii_lowercase();
        if seen.insert(key) {
            result.push(cleaned.to_string());
        }
    };

    // Priority order: selected text > window title > before cursor > after cursor
    let sources = [
        ctx.selected_text.as_str(),
        ctx.window_title.as_str(),
        ctx.before_cursor.as_str(),
        ctx.after_cursor.as_str(),
    ];
    for source in sources {
        if source.is_empty() {
            continue;
        }
        for cap in BACKTICK_RE.captures_iter(source) {
            if let Some(m) = cap.get(1) {
                push(m.as_str());
            }
        }
        for cap in TECH_TOKEN_RE.captures_iter(source) {
            if let Some(m) = cap.get(1) {
                push(m.as_str());
            }
        }
    }
    result
}

/// Build Bailian `input.context` messages (max 5 turns, <= 400 chars per turn)
/// combining scene/app context, cursor surrounding text, and recent dictation turns.
pub fn build_recognition_context_turns(
    scene: &str,
    abstract_context: &str,
    cursor: Option<&CursorContext>,
    recent_turns: &[String],
) -> Vec<Value> {
    let mut texts: Vec<String> = Vec::new();

    let base = [scene.trim(), abstract_context.trim()]
        .into_iter()
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join("\n");
    if !base.is_empty() {
        texts.push(take_head_chars(&base, MAX_CONTEXT_TURN_CHARS));
    }

    if let Some(cursor) = cursor.filter(|c| !c.is_empty()) {
        let mut parts = Vec::new();
        if !cursor.window_title.is_empty() {
            parts.push(format!("窗口：{}", cursor.window_title));
        }
        if !cursor.selected_text.is_empty() {
            parts.push(format!("选中：{}", cursor.selected_text));
        }
        if !cursor.before_cursor.is_empty() {
            parts.push(format!("光标前：{}", cursor.before_cursor));
        }
        if !cursor.after_cursor.is_empty() {
            parts.push(format!("光标后：{}", cursor.after_cursor));
        }
        if !parts.is_empty() {
            texts.push(take_head_chars(&parts.join("\n"), MAX_CONTEXT_TURN_CHARS));
        }
    }

    for turn in recent_turns {
        let trimmed = turn.trim();
        if !trimmed.is_empty() {
            texts.push(take_head_chars(trimmed, MAX_CONTEXT_TURN_CHARS));
        }
    }

    if texts.len() > MAX_CONTEXT_TURNS {
        texts.drain(..texts.len() - MAX_CONTEXT_TURNS);
    }

    texts
        .into_iter()
        .map(|text| {
            json!({
                "role": "user",
                "content": [{
                    "type": "input_text",
                    "text": text
                }]
            })
        })
        .collect()
}

/// Format bounded cursor + recent-turn context for Omni Realtime or second-stage LLM prompts.
pub fn build_prompt_context_appendix(
    cursor: Option<&CursorContext>,
    recent_turns: &[String],
) -> String {
    let mut sections = Vec::new();
    if let Some(cursor) = cursor.filter(|c| !c.is_empty()) {
        let mut lines = Vec::new();
        if !cursor.app_name.is_empty() || !cursor.window_title.is_empty() {
            lines.push(
                format!("- 当前窗口：{} {}", cursor.app_name, cursor.window_title)
                    .trim()
                    .to_string(),
            );
        }
        if !cursor.selected_text.is_empty() {
            lines.push(format!("- 当前选中文本：{}", cursor.selected_text));
        }
        if !cursor.before_cursor.is_empty() {
            lines.push(format!("- 光标前文本：{}", cursor.before_cursor));
        }
        if !cursor.after_cursor.is_empty() {
            lines.push(format!("- 光标后文本：{}", cursor.after_cursor));
        }
        if !lines.is_empty() {
            sections.push(format!(
                "当前输入位置上下文（仅用于判断术语、代码标识符、同音词和标点衔接；严禁复述或把上下文当成本次口述输出）：\n{}",
                lines.join("\n")
            ));
        }
    }
    let valid_recent: Vec<&str> = recent_turns
        .iter()
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .collect();
    if !valid_recent.is_empty() {
        let numbered = valid_recent
            .iter()
            .enumerate()
            .map(|(idx, line)| format!("{}. {line}", idx + 1))
            .collect::<Vec<_>>()
            .join("\n");
        sections.push(format!(
            "最近几轮听写前文（仅用于保持人名和术语拼写一致，严禁重复输出前文）：\n{numbered}"
        ));
    }
    sections.join("\n\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn secure_and_excluded_snapshots_fail_closed() {
        let secure = json!({
            "is_secure": true,
            "app_bundle_id": "com.apple.Notes",
            "value": "secret-password",
            "selection_start": 6,
            "selection_length": 0
        });
        assert!(parse_cursor_context(&secure, &[]).is_none());

        let excluded = json!({
            "is_secure": false,
            "app_bundle_id": "com.1password.1password",
            "value": "vault text",
            "selection_start": 4,
            "selection_length": 0
        });
        assert!(parse_cursor_context(&excluded, &["com.1password.1password".into()]).is_none());
    }

    #[test]
    fn extracts_code_identifiers_and_enforces_hotword_limits() {
        let ctx = CursorContext {
            app_name: "Cursor".into(),
            app_bundle_id: "com.todesktop.230313mzl4w4u92".into(),
            window_title: "provider.rs — vocal-more".into(),
            before_cursor: "检查 `SpeechAnalyzer` 和 AudioRecorder 的 tail_padding_ms 参数".into(),
            after_cursor: "确保 QwenAudio3 和 VAD 正常工作".into(),
            selected_text: "VocalMoreAudio".into(),
        };
        let hotwords = extract_dynamic_hotwords(&ctx, 20);
        assert!(hotwords.contains(&"VocalMoreAudio".to_string()));
        assert!(hotwords.contains(&"provider.rs".to_string()));
        assert!(hotwords.contains(&"vocal-more".to_string()));
        assert!(hotwords.contains(&"SpeechAnalyzer".to_string()));
        assert!(hotwords.contains(&"AudioRecorder".to_string()));
        assert!(hotwords.contains(&"tail_padding_ms".to_string()));
        assert!(hotwords.contains(&"VAD".to_string()));
        assert!(hotwords.iter().all(|w| is_valid_hotword(w)));
        assert!(!is_valid_hotword("一二三四五六七八九十一二三四五六"));
        assert!(!is_valid_hotword("one two three four five six seven eight"));
    }

    #[test]
    fn recent_history_respects_ttl_app_scope_and_turn_limit() {
        let mut history = RecentHistory::default();
        let t0 = Instant::now();
        history.record("第一句讨论 Qwen3.8", "com.app.a", t0);
        history.record("别的软件内容", "com.app.b", t0 + Duration::from_secs(10));
        history.record(
            "第二句讨论 GPUI Kit",
            "com.app.a",
            t0 + Duration::from_secs(20),
        );
        history.record(
            "第三句讨论 Butterworth",
            "com.app.a",
            t0 + Duration::from_secs(30),
        );
        history.record("第四句讨论热词", "com.app.a", t0 + Duration::from_secs(40));

        let turns = history.recent_for("com.app.a", t0 + Duration::from_secs(50));
        assert_eq!(turns.len(), 3);
        assert_eq!(turns[0], "第二句讨论 GPUI Kit");
        assert_eq!(turns[2], "第四句讨论热词");

        let expired = history.recent_for("com.app.a", t0 + Duration::from_secs(400));
        assert!(expired.is_empty());
    }

    #[test]
    fn recognition_context_caps_turns_and_char_limits() {
        let long_scene = "a".repeat(600);
        let ctx = CursorContext {
            window_title: "main.rs".into(),
            before_cursor: "b".repeat(250),
            ..Default::default()
        };
        let recent = vec![
            "turn1".into(),
            "turn2".into(),
            "turn3".into(),
            "turn4".into(),
        ];
        let turns = build_recognition_context_turns(&long_scene, "dev", Some(&ctx), &recent);
        assert_eq!(turns.len(), MAX_CONTEXT_TURNS);
        for turn in &turns {
            let text = turn["content"][0]["text"].as_str().unwrap();
            assert!(text.chars().count() <= MAX_CONTEXT_TURN_CHARS);
        }
    }
}
