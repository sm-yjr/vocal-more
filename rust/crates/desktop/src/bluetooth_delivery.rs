// SPDX-License-Identifier: GPL-3.0-only
//! Fixed-target envelopes and a conservative restart-safe delivery ledger.
//! Stores IDs/outcomes only, never text or authentication credentials.
use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::PathBuf,
    time::{SystemTime, UNIX_EPOCH},
};
pub const MESSAGE_LIFETIME_MS: u64 = 30_000;
const CLOCK_SKEW_MS: u64 = 5_000;
const CAPACITY: usize = 256;
/// A dictation may output only once to the destination observed when it began.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SessionTarget {
    pub epoch: u64,
    pub generation: u64,
    pub peer: String,
    pub revision: u64,
    pub connection: u64,
}
impl SessionTarget {
    pub fn matches(
        &self,
        epoch: u64,
        generation: u64,
        peer: &str,
        revision: u64,
        connection: u64,
    ) -> bool {
        self.epoch == epoch
            && self.generation == generation
            && !self.peer.is_empty()
            && self.peer == peer
            && self.revision != 0
            && self.revision == revision
            && self.connection != 0
            && self.connection == connection
    }
}
pub fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Envelope {
    version: u8,
    created_ms: u64,
    expires_ms: u64,
    target_revision: u64,
    text: String,
}
impl Envelope {
    pub fn encode(text: &str, target_revision: u64, now: u64) -> Result<String> {
        ensure!(target_revision != 0, "对端没有已确认的输入目标");
        ensure!(
            !text.is_empty() && text.len() <= 30 * 1024 && valid_text(text),
            "蓝牙文字无效或过长"
        );
        Ok(serde_json::to_string(&Self {
            version: 2,
            created_ms: now,
            expires_ms: now.saturating_add(MESSAGE_LIFETIME_MS),
            target_revision,
            text: text.into(),
        })?)
    }
    pub fn decode(raw: &str, target_revision: u64, ready: bool, now: u64) -> Result<Self> {
        let message: Self = serde_json::from_str(raw).context("蓝牙消息格式无效")?;
        ensure!(
            message.version == 2
                && message.target_revision != 0
                && ready
                && message.target_revision == target_revision,
            "输入目标未知或已经改变"
        );
        ensure!(
            message.created_ms <= now.saturating_add(CLOCK_SKEW_MS)
                && message.expires_ms >= now
                && message.expires_ms > message.created_ms
                && message.expires_ms - message.created_ms <= MESSAGE_LIFETIME_MS,
            "蓝牙消息已过期或时钟不一致"
        );
        ensure!(
            !message.text.is_empty()
                && message.text.len() <= 30 * 1024
                && valid_text(&message.text),
            "蓝牙文字无效"
        );
        Ok(message)
    }
    pub fn text(&self) -> &str {
        &self.text
    }
    pub fn revision(&self) -> u64 {
        self.target_revision
    }
    pub fn expires(&self) -> u64 {
        self.expires_ms
    }
}
fn valid_text(text: &str) -> bool {
    !text
        .chars()
        .any(|c| c.is_control() && !matches!(c, '\n' | '\r' | '\t'))
}
#[derive(Clone, Serialize, Deserialize)]
struct Entry {
    peer: String,
    id: u64,
    expires: u64,
    submitted: bool,
}
#[derive(Default)]
pub struct Ledger {
    path: Option<PathBuf>,
    entries: Vec<Entry>,
}
#[derive(Debug, PartialEq, Eq)]
pub enum Admission {
    New,
    Replay(bool),
}
impl Ledger {
    pub fn open(path: PathBuf) -> Result<Self> {
        let entries: Vec<Entry> = if path.exists() {
            let bytes = fs::read(&path)?;
            ensure!(bytes.len() <= 128 * 1024, "蓝牙去重记录过大");
            serde_json::from_slice(&bytes).context("蓝牙去重记录不可用，输入已暂停")?
        } else {
            vec![]
        };
        ensure!(entries.len() <= CAPACITY, "蓝牙去重记录过大");
        Ok(Self {
            path: Some(path),
            entries,
        })
    }
    pub fn begin(&mut self, peer: &str, id: u64, expires: u64, now: u64) -> Result<Admission> {
        ensure!(
            id != 0
                && !peer.is_empty()
                && expires >= now
                && expires <= now.saturating_add(MESSAGE_LIFETIME_MS + CLOCK_SKEW_MS),
            "蓝牙消息已过期或来源不明确"
        );
        if let Some(entry) = self.entries.iter().find(|e| e.peer == peer && e.id == id) {
            return Ok(Admission::Replay(entry.submitted));
        }
        self.entries
            .retain(|e| e.expires.saturating_add(CLOCK_SKEW_MS) >= now);
        ensure!(
            self.entries.len() < CAPACITY,
            "蓝牙去重记录已满，请稍后再试"
        );
        // Reserve before any UI mutation. A crash after insertion but before
        // completion is recovered as unknown/rejected, never inserted again.
        self.entries.push(Entry {
            peer: peer.into(),
            id,
            expires,
            submitted: false,
        });
        if let Err(error) = self.persist() {
            self.entries.pop();
            return Err(error);
        }
        Ok(Admission::New)
    }
    pub fn finish(&mut self, peer: &str, id: u64, submitted: bool) -> Result<()> {
        let index = self
            .entries
            .iter()
            .position(|e| e.peer == peer && e.id == id)
            .context("蓝牙去重预留不存在")?;
        let previous = self.entries[index].submitted;
        self.entries[index].submitted = submitted;
        if let Err(error) = self.persist() {
            // The durable reservation still suppresses reinsertion. Do not
            // report success on a later in-process replay if completion could
            // not be persisted; the actual input outcome remains uncertain.
            self.entries[index].submitted = previous;
            return Err(error);
        }
        Ok(())
    }
    fn persist(&self) -> Result<()> {
        let Some(path) = &self.path else {
            return Ok(());
        };
        let parent = path.parent().context("蓝牙去重目录无效")?;
        fs::create_dir_all(parent)?;
        let temporary = parent.join(format!(
            ".bluetooth-deliveries-{}.tmp",
            uuid::Uuid::new_v4()
        ));
        let result = (|| -> Result<()> {
            let mut file = OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&temporary)?;
            file.write_all(&serde_json::to_vec(&self.entries)?)?;
            file.sync_all()?;
            fs::rename(&temporary, path)?;
            fs::File::open(parent)?.sync_all()?;
            Ok(())
        })();
        if result.is_err() {
            let _ = fs::remove_file(temporary);
        }
        result
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn dictation_destination_rejects_focus_connection_peer_and_session_changes() {
        let target = SessionTarget {
            epoch: 2,
            generation: 7,
            peer: "fixed-peer".into(),
            revision: 9,
            connection: 3,
        };
        assert!(target.matches(2, 7, "fixed-peer", 9, 3));
        for (epoch, generation, peer, revision, connection) in [
            (3, 7, "fixed-peer", 9, 3),
            (2, 8, "fixed-peer", 9, 3),
            (2, 7, "other-peer", 9, 3),
            (2, 7, "fixed-peer", 10, 3),
            (2, 7, "fixed-peer", 9, 4),
            (2, 7, "fixed-peer", 0, 3),
        ] {
            assert!(!target.matches(epoch, generation, peer, revision, connection));
        }
    }
    #[test]
    fn completion_write_failure_keeps_unknown_reservation_without_success_replay() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("ledger.json");
        let saved = temp.path().join("reserved.json");
        let mut ledger = Ledger::open(path.clone()).unwrap();
        assert_eq!(
            ledger.begin("synthetic-peer", 1, 31_000, 1_000).unwrap(),
            Admission::New
        );
        fs::rename(&path, &saved).unwrap();
        fs::create_dir(&path).unwrap();
        assert!(ledger.finish("synthetic-peer", 1, true).is_err());
        assert_eq!(
            ledger.begin("synthetic-peer", 1, 31_000, 2_000).unwrap(),
            Admission::Replay(false)
        );
        fs::remove_dir(&path).unwrap();
        fs::rename(&saved, &path).unwrap();
        let mut restarted = Ledger::open(path).unwrap();
        assert_eq!(
            restarted.begin("synthetic-peer", 1, 31_000, 2_000).unwrap(),
            Admission::Replay(false)
        );
    }
    #[test]
    fn completed_and_crash_interrupted_ids_survive_app_restart_without_reinsertion() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("ledger.json");
        let mut ledger = Ledger::open(path.clone()).unwrap();
        assert_eq!(
            ledger.begin("synthetic-peer", 1, 31_000, 1_000).unwrap(),
            Admission::New
        );
        ledger.finish("synthetic-peer", 1, true).unwrap();
        assert_eq!(
            ledger.begin("synthetic-peer", 2, 31_000, 1_000).unwrap(),
            Admission::New
        );
        drop(ledger);
        let mut recovered = Ledger::open(path).unwrap();
        assert_eq!(
            recovered.begin("synthetic-peer", 1, 31_000, 2_000).unwrap(),
            Admission::Replay(true)
        );
        assert_eq!(
            recovered.begin("synthetic-peer", 2, 31_000, 2_000).unwrap(),
            Admission::Replay(false)
        );
        assert_eq!(
            recovered.begin("another-peer", 1, 31_000, 2_000).unwrap(),
            Admission::New
        );
    }
    #[test]
    fn unknown_changed_unready_expired_and_future_targets_cannot_receive() {
        let raw = Envelope::encode("fixed fixture", 9, 10_000).unwrap();
        assert!(Envelope::decode(&raw, 9, true, 10_001).is_ok());
        for (revision, ready, time) in [
            (0, true, 10_001),
            (8, true, 10_001),
            (9, false, 10_001),
            (9, true, 40_001),
            (9, true, 4_999),
        ] {
            assert!(Envelope::decode(&raw, revision, ready, time).is_err());
        }
        assert!(Envelope::encode("x\0y", 9, 10_000).is_err());
        assert!(Envelope::encode("fixed fixture", 0, 10_000).is_err());
    }
    #[test]
    fn full_or_unwritable_ledger_rejects_before_insertion_and_expired_ids_stay_expired() {
        let mut ledger = Ledger::default();
        for id in 1..=CAPACITY as u64 {
            ledger.begin("synthetic-peer", id, 31_000, 1_000).unwrap();
        }
        assert!(ledger.begin("synthetic-peer", 999, 31_000, 1_000).is_err());
        assert!(ledger.begin("synthetic-peer", 1, 31_000, 40_000).is_err());
        assert_eq!(
            ledger.begin("synthetic-peer", 999, 70_000, 40_000).unwrap(),
            Admission::New
        );
        let temp = tempfile::tempdir().unwrap();
        let parent = temp.path().join("not-directory");
        fs::write(&parent, b"fixed").unwrap();
        let mut blocked = Ledger {
            path: Some(parent.join("ledger.json")),
            entries: vec![],
        };
        assert!(blocked.begin("synthetic-peer", 1, 31_000, 1_000).is_err());
        assert!(blocked.entries.is_empty());
    }
}
