// SPDX-License-Identifier: GPL-3.0-only
use crate::{config::py_string, persistence};
use anyhow::Result;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::path::{Path, PathBuf};

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub struct Entry {
    pub term: String,
    #[serde(default)]
    pub aliases: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Mutation {
    pub term: String,
    pub term_created: bool,
    pub aliases_added: Vec<String>,
}

pub fn normalize_term(value: &Value) -> String {
    if value.is_null() {
        String::new()
    } else {
        py_string(value).trim().into()
    }
}

pub fn aliases(value: &Value) -> Vec<String> {
    fn collect(value: &Value, result: &mut Vec<String>) {
        match value {
            Value::Null | Value::Object(_) => {}
            Value::Array(items) => {
                for item in items {
                    collect(item, result);
                }
            }
            Value::String(s) => {
                let s = s.trim();
                if s.starts_with('[')
                    && s.ends_with(']')
                    && let Ok(parsed) = serde_json::from_str::<Value>(s)
                {
                    collect(&parsed, result);
                    return;
                }
                for piece in s
                    .split([',', '，', '\n'])
                    .map(str::trim)
                    .filter(|p| !p.is_empty())
                {
                    if !result.iter().any(|a| a == piece) {
                        result.push(piece.into());
                    }
                }
            }
            _ => {
                let s = normalize_term(value);
                if !s.is_empty() && !result.contains(&s) {
                    result.push(s);
                }
            }
        }
    }
    let mut result = Vec::new();
    collect(value, &mut result);
    result
}

pub fn normalize_entries(value: &Value) -> Vec<Entry> {
    let Some(items) = value.as_array() else {
        return vec![];
    };
    items
        .iter()
        .filter_map(|item| {
            let term = normalize_term(&item["term"]);
            if term.is_empty() {
                return None;
            }
            let aliases = aliases(&item["aliases"])
                .into_iter()
                .filter(|a| a != &term)
                .collect();
            Some(Entry { term, aliases })
        })
        .collect()
}

pub fn normalize_text(text: &str, entries: &[Entry]) -> String {
    let mut replacements = entries
        .iter()
        .flat_map(|e| e.aliases.iter().map(move |alias| (alias, &e.term)))
        .collect::<Vec<_>>();
    replacements.sort_by_key(|(alias, _)| std::cmp::Reverse(alias.chars().count()));
    let mut text = text.to_string();
    for (alias, canonical) in replacements {
        if alias.is_empty() || alias == canonical {
            continue;
        }
        let Ok(pattern) = regex::RegexBuilder::new(&regex::escape(alias))
            .case_insensitive(alias.is_ascii())
            .build()
        else {
            continue;
        };
        let first = alias.chars().next().unwrap();
        let last = alias.chars().next_back().unwrap();
        let mut replaced = String::new();
        let mut cursor = 0;
        for m in pattern.find_iter(&text) {
            if first.is_ascii_alphanumeric()
                && text[..m.start()]
                    .chars()
                    .next_back()
                    .is_some_and(|ch| ch.is_ascii_alphanumeric())
            {
                continue;
            }
            if last.is_ascii_alphanumeric()
                && text[m.end()..]
                    .chars()
                    .next()
                    .is_some_and(|ch| ch.is_ascii_alphanumeric())
            {
                continue;
            }
            replaced.push_str(&text[cursor..m.start()]);
            replaced.push_str(canonical);
            cursor = m.end();
        }
        replaced.push_str(&text[cursor..]);
        text = replaced;
    }
    text
}

pub fn format_prompt(entries: &[Entry]) -> String {
    if entries.is_empty() {
        return String::new();
    }
    let lines = entries
        .iter()
        .map(|e| {
            if e.aliases.is_empty() {
                format!("   - {}", e.term)
            } else {
                format!(
                    "   - {}（可能被误识别为：{}）",
                    e.term,
                    e.aliases.join("、")
                )
            }
        })
        .collect::<Vec<_>>();
    format!(
        "5. 专有名词修正：以下是用户定义的专有名词词典，语音转写中可能出现这些词的错误识别，请修正为正确的写法：\n{}",
        lines.join("\n")
    )
}

pub fn corpus(entries: &[Entry], extras: &[Value]) -> String {
    let mut seen = std::collections::HashSet::new();
    let terms: Vec<_> = entries
        .iter()
        .map(|e| e.term.trim())
        .filter(|t| !t.is_empty() && seen.insert(t.to_string()))
        .collect();
    let extras: Vec<_> = extras
        .iter()
        .map(|e| py_string(e).trim().to_string())
        .filter(|t| !t.is_empty() && seen.insert(t.clone()))
        .collect();
    [terms.join("\n"), extras.join("\n")]
        .into_iter()
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join("\n\n")
}

pub struct Dictionary {
    pub entries: Vec<Entry>,
    pub path: PathBuf,
}

impl Dictionary {
    pub fn open(path: &Path) -> Result<Self> {
        let raw = persistence::read_yaml(path)?.unwrap_or_else(|| json!({}));
        Ok(Self {
            entries: normalize_entries(&raw["entries"]),
            path: path.into(),
        })
    }
    fn replace(&mut self, entries: Vec<Entry>) -> Result<()> {
        let stored = entries
            .iter()
            .map(|e| {
                if e.aliases.is_empty() {
                    json!({"term":e.term})
                } else {
                    json!(e)
                }
            })
            .collect::<Vec<_>>();
        persistence::write_yaml(&self.path, &json!({"entries":stored}))?;
        self.entries = entries;
        Ok(())
    }
    pub fn add(&mut self, term: &str, raw_aliases: &Value) -> Result<Mutation> {
        self.add_journaled(term, raw_aliases, |_| Ok(()))
    }
    pub fn add_journaled(
        &mut self,
        term: &str,
        raw_aliases: &Value,
        before_apply: impl FnOnce(&Mutation) -> Result<()>,
    ) -> Result<Mutation> {
        let term = term.trim();
        anyhow::ensure!(!term.is_empty(), "term is required");
        let mut entries = self.entries.clone();
        let mut mutation = Mutation {
            term: term.into(),
            term_created: false,
            aliases_added: vec![],
        };
        let normalized = aliases(raw_aliases);
        if let Some(entry) = entries.iter_mut().find(|e| e.term == term) {
            mutation.aliases_added = normalized
                .into_iter()
                .filter(|a| a != term && !entry.aliases.contains(a))
                .collect();
            entry.aliases.extend(mutation.aliases_added.iter().cloned());
        } else {
            mutation.term_created = true;
            mutation.aliases_added = normalized.clone();
            entries.push(Entry {
                term: term.into(),
                aliases: normalized,
            });
        }
        before_apply(&mutation)?;
        self.replace(entries)?;
        Ok(mutation)
    }
    pub fn remove(&mut self, term: &str) -> Result<()> {
        self.replace(
            self.entries
                .iter()
                .filter(|e| e.term != term)
                .cloned()
                .collect(),
        )
    }
    pub fn undo(&mut self, mutation: &Mutation) -> Result<bool> {
        let mut entries = self.entries.clone();
        let Some(index) = entries.iter().position(|e| e.term == mutation.term) else {
            return Ok(false);
        };
        entries[index]
            .aliases
            .retain(|a| !mutation.aliases_added.contains(a));
        if mutation.term_created && entries[index].aliases.is_empty() {
            entries.remove(index);
        }
        self.replace(entries)?;
        Ok(true)
    }
}
