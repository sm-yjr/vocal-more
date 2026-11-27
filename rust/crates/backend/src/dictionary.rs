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

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub struct RemovedEntry {
    pub term: String,
    #[serde(default)]
    pub aliases: Vec<String>,
    pub index: usize,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub struct BatchAddSummary {
    pub added_terms: usize,
    pub updated_terms: usize,
    pub added_aliases: usize,
    pub skipped: usize,
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
    pub fn remove(&mut self, term: &str) -> Result<Vec<RemovedEntry>> {
        let mut removed = Vec::new();
        let mut kept = Vec::with_capacity(self.entries.len());
        for (index, entry) in self.entries.iter().enumerate() {
            if entry.term == term {
                removed.push(RemovedEntry {
                    term: entry.term.clone(),
                    aliases: entry.aliases.clone(),
                    index,
                });
            } else {
                kept.push(entry.clone());
            }
        }
        self.replace(kept)?;
        Ok(removed)
    }
    pub fn restore(&mut self, items: &[RemovedEntry]) -> Result<usize> {
        let mut entries = self.entries.clone();
        let pre_existing: std::collections::HashSet<String> =
            entries.iter().map(|entry| entry.term.clone()).collect();
        let mut sorted = items.to_vec();
        sorted.sort_by_key(|item| item.index);
        let mut restored = 0usize;
        for item in sorted {
            let term = item.term.trim();
            if term.is_empty() {
                continue;
            }
            let normalized: Vec<String> = aliases(&json!(item.aliases))
                .into_iter()
                .filter(|alias| alias != term)
                .collect();
            if pre_existing.contains(term)
                && let Some(existing) = entries.iter_mut().find(|entry| entry.term == term)
            {
                for alias in normalized {
                    if !existing.aliases.contains(&alias) {
                        existing.aliases.push(alias);
                    }
                }
            } else {
                let insert_at = item.index.min(entries.len());
                entries.insert(
                    insert_at,
                    Entry {
                        term: term.into(),
                        aliases: normalized,
                    },
                );
            }
            restored += 1;
        }
        if restored > 0 {
            self.replace(entries)?;
        }
        Ok(restored)
    }
    pub fn update(
        &mut self,
        old_term: &str,
        new_term: &str,
        raw_aliases: &Value,
        expected_aliases: Option<&[String]>,
    ) -> Result<()> {
        self.update_journaled(old_term, new_term, raw_aliases, expected_aliases, || Ok(()))
    }
    pub fn update_journaled(
        &mut self,
        old_term: &str,
        new_term: &str,
        raw_aliases: &Value,
        expected_aliases: Option<&[String]>,
        before_apply: impl FnOnce() -> Result<()>,
    ) -> Result<()> {
        let new_term = new_term.trim();
        anyhow::ensure!(!new_term.is_empty(), "term is required");
        let mut entries = self.entries.clone();
        let Some(index) = entries.iter().position(|entry| entry.term == old_term) else {
            anyhow::bail!("term not found");
        };
        if let Some(expected) = expected_aliases {
            anyhow::ensure!(
                entries[index].aliases == expected,
                "term was modified concurrently; refresh and try again"
            );
        }
        if new_term != old_term
            && entries
                .iter()
                .enumerate()
                .any(|(i, entry)| i != index && entry.term == new_term)
        {
            anyhow::bail!("term already exists");
        }
        let normalized = aliases(raw_aliases)
            .into_iter()
            .filter(|alias| alias != new_term)
            .collect();
        entries[index] = Entry {
            term: new_term.into(),
            aliases: normalized,
        };
        before_apply()?;
        self.replace(entries)
    }
    pub fn batch_add(&mut self, items: &[(String, Value)]) -> Result<BatchAddSummary> {
        anyhow::ensure!(
            items.len() <= 500,
            "too many dictionary entries in one batch"
        );
        let mut entries = self.entries.clone();
        let mut summary = BatchAddSummary {
            added_terms: 0,
            updated_terms: 0,
            added_aliases: 0,
            skipped: 0,
        };
        for (raw_term, raw_aliases) in items {
            let term = raw_term.trim();
            if term.is_empty() {
                summary.skipped += 1;
                continue;
            }
            let normalized: Vec<String> = aliases(raw_aliases)
                .into_iter()
                .filter(|alias| alias != term)
                .collect();
            if let Some(entry) = entries.iter_mut().find(|entry| entry.term == term) {
                let added: Vec<String> = normalized
                    .into_iter()
                    .filter(|alias| !entry.aliases.contains(alias))
                    .collect();
                if added.is_empty() {
                    summary.skipped += 1;
                } else {
                    summary.updated_terms += 1;
                    summary.added_aliases += added.len();
                    entry.aliases.extend(added);
                }
            } else {
                summary.added_terms += 1;
                summary.added_aliases += normalized.len();
                entries.push(Entry {
                    term: term.into(),
                    aliases: normalized,
                });
            }
        }
        if summary.added_terms > 0 || summary.updated_terms > 0 {
            self.replace(entries)?;
        }
        Ok(summary)
    }
    pub fn reload(&mut self) -> Result<usize> {
        let Some(raw) = persistence::read_yaml(&self.path)? else {
            anyhow::bail!("dictionary file not found");
        };
        let Some(root) = raw.as_object() else {
            anyhow::bail!("dictionary root must be a YAML mapping");
        };
        let Some(items) = root.get("entries").and_then(Value::as_array) else {
            anyhow::bail!("dictionary file must contain an entries list");
        };
        let mut parsed = Vec::with_capacity(items.len());
        for (index, item) in items.iter().enumerate() {
            let Some(object) = item.as_object() else {
                anyhow::bail!("dictionary entry #{} must be a mapping", index + 1);
            };
            let raw_term = object.get("term").unwrap_or(&Value::Null);
            anyhow::ensure!(
                raw_term.is_string(),
                "dictionary entry #{} term must be a string",
                index + 1
            );
            let term = raw_term.as_str().unwrap_or_default().trim().to_owned();
            anyhow::ensure!(
                !term.is_empty(),
                "dictionary entry #{} is missing a non-empty term",
                index + 1
            );
            let raw_aliases = object.get("aliases").unwrap_or(&Value::Null);
            match raw_aliases {
                Value::Null | Value::String(_) => {}
                Value::Array(list) => {
                    anyhow::ensure!(
                        list.iter().all(Value::is_string),
                        "dictionary entry #{} aliases must be strings",
                        index + 1
                    );
                }
                _ => anyhow::bail!("dictionary entry #{} has invalid aliases", index + 1),
            }
            let normalized = aliases(raw_aliases)
                .into_iter()
                .filter(|alias| alias != &term)
                .collect();
            parsed.push(Entry {
                term,
                aliases: normalized,
            });
        }
        self.entries = parsed;
        Ok(self.entries.len())
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
