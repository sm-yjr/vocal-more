// SPDX-License-Identifier: GPL-3.0-only
//! Compatible SQLite queue. Call on the owned blocking application lane;
//! network classification occurs outside its lock and outside transactions.
use crate::{
    dictionary::{Dictionary, Mutation},
    learning::{self, Decision},
};
use anyhow::{Context, Result, ensure};
use rusqlite::{Connection, OptionalExtension, Row, params};
use serde::Serialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{path::Path, time::Duration};
use uuid::Uuid;

const REDACTED: &str =
    r#"{"raw_text":"","pasted_text":"","original_text":"","baseline_text":"","edited_text":""}"#;

#[derive(Clone, Debug, Serialize)]
pub struct Job {
    pub id: String,
    pub evidence: Value,
    pub status: String,
    pub created_at: f64,
    pub updated_at: f64,
    pub attempt_count: u32,
    pub next_retry_at: f64,
    pub error: String,
    pub result: Option<Decision>,
    pub term_created: bool,
    pub aliases_added: Vec<String>,
    pub observation_id: String,
    pub candidate_index: u32,
    pub candidate_count: u32,
    pub notification_emitted: bool,
    pub model: String,
    pub prompt_version: u32,
    pub apply_origin: String,
}
fn row(r: &Row<'_>) -> rusqlite::Result<Job> {
    let json_error =
        |e| rusqlite::Error::FromSqlConversionFailure(0, rusqlite::types::Type::Text, Box::new(e));
    let evidence: String = r.get("evidence_json")?;
    let decision: Option<String> = r.get("result_json")?;
    let aliases: String = r.get("aliases_added_json")?;
    Ok(Job {
        id: r.get("id")?,
        evidence: serde_json::from_str(&evidence).map_err(json_error)?,
        status: r.get("status")?,
        created_at: r.get("created_at")?,
        updated_at: r.get("updated_at")?,
        attempt_count: r.get("attempt_count")?,
        next_retry_at: r.get("next_retry_at")?,
        error: r.get("error")?,
        result: decision
            .map(|s| serde_json::from_str(&s))
            .transpose()
            .map_err(json_error)?,
        term_created: r.get("term_created")?,
        aliases_added: serde_json::from_str(&aliases).map_err(json_error)?,
        observation_id: r.get("observation_id")?,
        candidate_index: r.get("candidate_index")?,
        candidate_count: r.get("candidate_count")?,
        notification_emitted: r.get("notification_emitted")?,
        model: r.get("model")?,
        prompt_version: r.get("prompt_version")?,
        apply_origin: r.get("apply_origin")?,
    })
}
pub struct LearningStore {
    connection: Connection,
}
impl LearningStore {
    pub fn open(path: &Path, now: f64) -> Result<Self> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let connection = Connection::open(path)?;
        connection.busy_timeout(Duration::from_secs(5))?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))?;
        }
        connection.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS dictionary_learning_jobs (
                id TEXT PRIMARY KEY,evidence_json TEXT NOT NULL,status TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,next_retry_at REAL NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '',
                result_json TEXT,term_created INTEGER NOT NULL DEFAULT 0,aliases_added_json TEXT NOT NULL DEFAULT '[]',
                observation_id TEXT NOT NULL DEFAULT '',candidate_index INTEGER NOT NULL DEFAULT 0,candidate_count INTEGER NOT NULL DEFAULT 1,
                notification_emitted INTEGER NOT NULL DEFAULT 0,model TEXT NOT NULL DEFAULT 'qwen3.7-plus',prompt_version INTEGER NOT NULL DEFAULT 4,
                apply_origin TEXT NOT NULL DEFAULT 'automatic');
            CREATE TABLE IF NOT EXISTS dictionary_learning_confirmations (alias TEXT NOT NULL,term TEXT NOT NULL,source_id TEXT NOT NULL,created_at REAL NOT NULL,PRIMARY KEY(alias,term,source_id));
            CREATE TABLE IF NOT EXISTS dictionary_learning_suppressed (alias TEXT NOT NULL,term TEXT NOT NULL,PRIMARY KEY(alias,term));")?;
        let columns = connection
            .prepare("PRAGMA table_info(dictionary_learning_jobs)")?
            .query_map([], |r| r.get::<_, String>(1))?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        for (name, definition) in [
            ("model", "TEXT NOT NULL DEFAULT 'qwen3.7-plus'"),
            ("prompt_version", "INTEGER NOT NULL DEFAULT 1"),
            ("observation_id", "TEXT NOT NULL DEFAULT ''"),
            ("candidate_index", "INTEGER NOT NULL DEFAULT 0"),
            ("candidate_count", "INTEGER NOT NULL DEFAULT 1"),
            ("notification_emitted", "INTEGER NOT NULL DEFAULT 0"),
            ("apply_origin", "TEXT NOT NULL DEFAULT 'automatic'"),
        ] {
            if !columns.iter().any(|c| c == name) {
                connection.execute(
                    &format!("ALTER TABLE dictionary_learning_jobs ADD COLUMN {name} {definition}"),
                    [],
                )?;
            }
        }
        connection.execute_batch("CREATE INDEX IF NOT EXISTS idx_dictionary_learning_due ON dictionary_learning_jobs(status,next_retry_at,created_at);
            CREATE INDEX IF NOT EXISTS idx_dictionary_learning_observation ON dictionary_learning_jobs(observation_id,candidate_index);")?;
        connection.execute("UPDATE dictionary_learning_jobs SET status='retry',updated_at=?1,error='interrupted' WHERE status IN ('processing','applying')",[now])?;
        Ok(Self { connection })
    }
    pub fn get(&self, id: &str) -> Result<Option<Job>> {
        Ok(self
            .connection
            .query_row(
                "SELECT * FROM dictionary_learning_jobs WHERE id=?1",
                [id],
                row,
            )
            .optional()?)
    }
    pub fn list(&self, limit: usize) -> Result<Vec<Job>> {
        Ok(self
            .connection
            .prepare(
                "SELECT * FROM dictionary_learning_jobs ORDER BY created_at DESC,id DESC LIMIT ?1",
            )?
            .query_map([limit.clamp(1, 1000)], row)?
            .collect::<rusqlite::Result<Vec<_>>>()?)
    }
    pub fn enqueue(&mut self, candidates: &[Value], now: f64) -> Result<Vec<Job>> {
        if candidates.is_empty() {
            return Ok(vec![]);
        }
        ensure!(candidates.len() <= 5, "too many correction candidates");
        let observation = learning::string(&candidates[0], "observation_id");
        ensure!(
            !observation.is_empty()
                && candidates
                    .iter()
                    .all(|c| learning::string(c, "observation_id") == observation),
            "candidates must share an observation"
        );
        let tx = self.connection.transaction()?;
        let existing: u64 = tx.query_row(
            "SELECT COUNT(*) FROM dictionary_learning_jobs WHERE observation_id=?1",
            [observation],
            |r| r.get(0),
        )?;
        // Re-delivered OS observation/IPC notification is idempotent.
        if existing > 0 {
            return Ok(vec![]);
        }
        let mut ids = vec![];
        for (index, evidence) in candidates.iter().enumerate() {
            let id = Uuid::new_v4().to_string();
            tx.execute("INSERT INTO dictionary_learning_jobs(id,evidence_json,status,created_at,updated_at,observation_id,candidate_index,candidate_count,prompt_version)
                VALUES(?1,?2,'pending',?3,?3,?4,?5,?6,4)",params![id,serde_json::to_string(evidence)?,now,observation,index,candidates.len()])?;
            ids.push(id);
        }
        tx.commit()?;
        ids.iter()
            .map(|id| self.get(id)?.context("enqueued correction missing"))
            .collect()
    }
    pub fn claim(&mut self, now: f64) -> Result<Option<Job>> {
        let tx = self
            .connection
            .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let id:Option<String>=tx.query_row("SELECT id FROM dictionary_learning_jobs WHERE status IN ('pending','retry') AND next_retry_at<=?1 ORDER BY created_at,candidate_index,id LIMIT 1",[now],|r|r.get(0)).optional()?;
        if let Some(id) = &id {
            tx.execute("UPDATE dictionary_learning_jobs SET status='processing',updated_at=?1,attempt_count=attempt_count+1,error='' WHERE id=?2",params![now,id])?;
        }
        tx.commit()?;
        id.map(|id| self.get(&id)).transpose().map(Option::flatten)
    }
    pub fn next_due(&self) -> Result<Option<f64>> {
        Ok(self.connection.query_row("SELECT MIN(next_retry_at) FROM dictionary_learning_jobs WHERE status IN ('pending','retry')",[],|r|r.get(0))?)
    }
    pub fn failure(&self, job: &Job, error: &str, retryable: bool, now: f64) -> Result<()> {
        let retry = retryable && job.attempt_count < 5;
        let next = now + 2_f64.powi(job.attempt_count.clamp(1, 16) as i32);
        self.connection.execute("UPDATE dictionary_learning_jobs SET status=?1,updated_at=?2,next_retry_at=?3,error=?4,evidence_json=CASE WHEN ?5 THEN evidence_json ELSE ?6 END WHERE id=?7",
            params![if retry {"retry"}else{"failed"},now,next,error.chars().take(2000).collect::<String>(),retry,REDACTED,job.id])?;
        Ok(())
    }
    pub fn finish(
        &self,
        id: &str,
        status: &str,
        result: &Decision,
        mutation: Option<&Mutation>,
        now: f64,
    ) -> Result<()> {
        ensure!(
            ["applied", "review", "ignored"].contains(&status),
            "invalid completed learning status"
        );
        self.connection.execute("UPDATE dictionary_learning_jobs SET status=?1,updated_at=?2,result_json=?3,term_created=?4,aliases_added_json=?5,error='',evidence_json=?6 WHERE id=?7",
            params![status,now,serde_json::to_string(result)?,mutation.is_some_and(|m|m.term_created),serde_json::to_string(&mutation.map(|m|m.aliases_added.clone()).unwrap_or_default())?,REDACTED,id])?;
        Ok(())
    }
    pub fn journal(
        &self,
        id: &str,
        result: &Decision,
        mutation: &Mutation,
        origin: &str,
        now: f64,
    ) -> Result<()> {
        self.connection.execute("UPDATE dictionary_learning_jobs SET status='applying',updated_at=?1,result_json=?2,term_created=?3,aliases_added_json=?4,error='',apply_origin=?5 WHERE id=?6",
            params![now,serde_json::to_string(result)?,mutation.term_created,serde_json::to_string(&mutation.aliases_added)?,origin,id])?;
        Ok(())
    }
    pub fn authorize(
        &mut self,
        job: &Job,
        decision: Decision,
        now: f64,
        same_identity: impl Fn(&str, &str) -> bool,
    ) -> Result<Decision> {
        if decision.action == "ignore" {
            return Ok(decision);
        }
        let recording = learning::string(&job.evidence, "recording_id");
        let source = if recording.is_empty() {
            format!("observation:{}", job.observation_id)
        } else {
            format!("recording:{recording}")
        };
        let source = format!("{:x}", Sha256::digest(source.as_bytes()));
        let term = learning::fold(&decision.term);
        let mut count = u64::MAX;
        let mut conflict = false;
        let tx = self.connection.transaction()?;
        for alias in &decision.aliases {
            let alias = learning::fold(alias);
            tx.execute("INSERT OR IGNORE INTO dictionary_learning_confirmations(alias,term,source_id,created_at) VALUES(?1,?2,?3,?4)",params![alias,term,source,now])?;
            count = count.min(tx.query_row(
                "SELECT COUNT(*) FROM dictionary_learning_confirmations WHERE alias=?1 AND term=?2",
                params![alias, term],
                |r| r.get(0),
            )?);
            conflict|=tx.query_row("SELECT EXISTS(SELECT 1 FROM dictionary_learning_suppressed WHERE alias=?1 AND term=?2) OR EXISTS(SELECT 1 FROM dictionary_learning_confirmations WHERE alias=?1 AND term!=?2)",params![alias,term],|r|r.get::<_,bool>(0))?;
        }
        tx.commit()?;
        let fast = decision.action == "add"
            && decision
                .aliases
                .iter()
                .all(|a| same_identity(a, &decision.term));
        let (action, reason) = if conflict {
            ("review", "conflicting_corrections")
        } else if fast {
            ("add", "same_term_correction")
        } else if count >= 2 {
            ("add", "repeated_correction")
        } else {
            ("review", "awaiting_independent_correction")
        };
        Ok(Decision {
            action: action.into(),
            reason_code: reason.into(),
            ..decision
        })
    }
    pub fn suppress(&mut self, decision: &Decision) -> Result<()> {
        let tx = self.connection.transaction()?;
        for alias in &decision.aliases {
            tx.execute(
                "INSERT OR IGNORE INTO dictionary_learning_suppressed(alias,term) VALUES(?1,?2)",
                params![learning::fold(alias), learning::fold(&decision.term)],
            )?;
        }
        tx.commit()?;
        Ok(())
    }
    pub fn apply(
        &self,
        job: &Job,
        decision: &Decision,
        dictionary: &mut Dictionary,
        origin: &str,
        now: f64,
    ) -> Result<()> {
        if job.result.is_some() && job.status == "processing" {
            // A recovered apply already journaled the original mutation. An
            // idempotent add must not replace it with an empty undo record.
            dictionary.add(&decision.term, &json!(decision.aliases))?;
            let mutation = Mutation {
                term: decision.term.clone(),
                term_created: job.term_created,
                aliases_added: job.aliases_added.clone(),
            };
            self.finish(&job.id, "applied", decision, Some(&mutation), now)?;
        } else {
            let mutation =
                dictionary.add_journaled(&decision.term, &json!(decision.aliases), |m| {
                    self.journal(&job.id, decision, m, origin, now)
                })?;
            self.finish(&job.id, "applied", decision, Some(&mutation), now)?;
        }
        Ok(())
    }
    pub fn approve(&self, id: &str, dictionary: &mut Dictionary, now: f64) -> Result<bool> {
        let Some(job) = self
            .get(id)?
            .filter(|j| j.status == "review" && j.result.is_some())
        else {
            return Ok(false);
        };
        self.apply(
            &job,
            job.result.as_ref().unwrap(),
            dictionary,
            "review",
            now,
        )?;
        Ok(true)
    }
    pub fn reject(&mut self, id: &str, now: f64) -> Result<bool> {
        let Some(job) = self
            .get(id)?
            .filter(|j| j.status == "review" && j.result.is_some())
        else {
            return Ok(false);
        };
        let result = job.result.as_ref().unwrap();
        self.suppress(result)?;
        self.finish(id, "ignored", result, None, now)?;
        Ok(true)
    }
    pub fn undo(&mut self, id: &str, dictionary: &mut Dictionary, now: f64) -> Result<bool> {
        let Some(job) = self.get(id)?.filter(|j| {
            ["applied", "reverting"].contains(&j.status.as_str()) && j.result.is_some()
        }) else {
            return Ok(false);
        };
        let result = job.result.as_ref().unwrap();
        self.suppress(result)?;
        self.connection.execute(
            "UPDATE dictionary_learning_jobs SET status='reverting',updated_at=?1 WHERE id=?2",
            params![now, id],
        )?;
        dictionary.undo(&Mutation {
            term: result.term.clone(),
            term_created: job.term_created,
            aliases_added: job.aliases_added.clone(),
        })?;
        self.connection.execute(
            "UPDATE dictionary_learning_jobs SET status='reverted',updated_at=?1 WHERE id=?2",
            params![now, id],
        )?;
        Ok(true)
    }
    pub fn recover_undos(&mut self, dictionary: &mut Dictionary, now: f64) -> Result<()> {
        let ids = self
            .connection
            .prepare("SELECT id FROM dictionary_learning_jobs WHERE status='reverting'")?
            .query_map([], |r| r.get::<_, String>(0))?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        for id in ids {
            self.undo(&id, dictionary, now)?;
        }
        Ok(())
    }
    pub fn claim_notification(&mut self, observation: &str) -> Result<Option<Vec<String>>> {
        if observation.is_empty() {
            return Ok(None);
        }
        let tx = self
            .connection
            .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let rows=tx.prepare("SELECT * FROM dictionary_learning_jobs WHERE observation_id=?1 ORDER BY candidate_index,created_at,id")?
            .query_map([observation],row)?.collect::<rusqlite::Result<Vec<_>>>()?;
        if rows.is_empty()
            || rows.iter().any(|j| {
                j.notification_emitted
                    || !["applied", "review", "ignored", "failed", "reverted"]
                        .contains(&j.status.as_str())
            })
            || rows.len()
                != rows
                    .iter()
                    .map(|j| j.candidate_count as usize)
                    .max()
                    .unwrap_or(0)
        {
            return Ok(None);
        }
        tx.execute(
            "UPDATE dictionary_learning_jobs SET notification_emitted=1 WHERE observation_id=?1",
            [observation],
        )?;
        tx.commit()?;
        let mut terms = Vec::new();
        for job in rows {
            if job.status == "applied"
                && (job.term_created || !job.aliases_added.is_empty())
                && let Some(decision) = job.result
                && !terms.contains(&decision.term)
            {
                terms.push(decision.term);
            }
        }
        Ok(Some(terms))
    }
}
