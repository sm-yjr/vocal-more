// SPDX-License-Identifier: GPL-3.0-only
//! Frontend-compatible history backed by streamed core recordings.
use crate::persistence::{PreparedWrite, atomic_write_limited, prepare_atomic_write_limited};
use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::HashSet,
    fs,
    io::Read,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio_util::sync::CancellationToken;
use vocal_more_core::recording::{Recording, RecordingStore};

const HISTORY_LIMIT: usize = 30;
const MAX_INDEX_BYTES: u64 = 32 * 1024 * 1024;

#[derive(Clone, Default, PartialEq, Serialize, Deserialize)]
struct Index {
    schema_version: u32,
    recordings: Vec<Value>,
    #[serde(default)]
    deleted: HashSet<String>,
}

pub struct PreparedHistory {
    base: Index,
    next: Index,
    pinned: HashSet<String>,
    write: PreparedWrite,
}

#[derive(Clone)]
pub struct History {
    store: RecordingStore,
    state: Arc<Mutex<Index>>,
    archive: Arc<tokio::sync::Mutex<()>>,
    pinned: Arc<Mutex<HashSet<String>>>,
}

impl History {
    pub async fn open(store: RecordingStore) -> Result<Self> {
        // The store lock excludes live writers. A process killed during prepare
        // can leave an invisible receipt; it must never become committed history.
        for entry in fs::read_dir(store.directory())? {
            let entry = entry?;
            let name = entry.file_name();
            let name = name.to_string_lossy();
            if name.starts_with(".history-prepared-")
                && name.ends_with(".tmp")
                && entry.file_type()?.is_file()
            {
                fs::remove_file(entry.path())?;
            }
        }
        let path = store.directory().join("history.json");
        let state = if path.exists() {
            ensure!(
                fs::metadata(&path)?.len() <= MAX_INDEX_BYTES,
                "history index exceeds size limit"
            );
            let index: Index =
                serde_json::from_slice(&fs::read(&path)?).context("invalid Rust history index")?;
            ensure!(index.schema_version == 1, "unsupported history schema");
            index
        } else {
            Index {
                schema_version: 1,
                ..Default::default()
            }
        };
        let history = Self {
            store,
            state: Arc::new(Mutex::new(state)),
            archive: Arc::new(tokio::sync::Mutex::new(())),
            pinned: Arc::new(Mutex::new(HashSet::new())),
        };
        {
            let mut index = history.state.lock().unwrap();
            let deleted = index.deleted.clone();
            index.recordings.retain(|r| {
                r["id"].as_str().is_some_and(|id| !deleted.contains(id))
                    && history
                        .path_for(r["filename"].as_str().unwrap_or(""))
                        .is_some_and(|p| p.exists())
            });
            for item in &mut index.recordings {
                if item["status"] == "pending" {
                    item["status"] = json!("failed");
                    item["error"] = json!("Previous processing was interrupted");
                }
            }
        }
        // A crash can finish the core WAV before the application index is updated.
        // Recover its audio as failed/retryable; never infer that polishing/paste
        // completed merely because the lower-level recording did.
        for record in history.store.list().await? {
            let id = record.id.to_string();
            let exists = {
                let index = history.state.lock().unwrap();
                index.deleted.contains(&id) || index.recordings.iter().any(|r| r["id"] == id)
            };
            if !exists && record.pcm_bytes > 0 {
                history.register(&record, "realtime_long", "auto")?;
                history.update(
                    &id,
                    "failed",
                    None,
                    Some("Previous processing was interrupted"),
                    None,
                )?;
            }
        }
        history.cleanup_deleted()?;
        history.persist()?;
        Ok(history)
    }

    pub fn store(&self) -> &RecordingStore {
        &self.store
    }
    pub fn list(&self) -> Vec<Value> {
        let mut items = self.state.lock().unwrap().recordings.clone();
        items.reverse();
        items
    }
    pub fn get(&self, id: &str) -> Option<Value> {
        self.state
            .lock()
            .unwrap()
            .recordings
            .iter()
            .find(|r| r["id"] == id)
            .cloned()
    }
    pub fn pin(&self, id: &str) {
        self.pinned.lock().unwrap().insert(id.into());
    }
    pub fn unpin(&self, id: &str) {
        self.pinned.lock().unwrap().remove(id);
    }

    fn path_for(&self, filename: &str) -> Option<PathBuf> {
        if !safe_basename(filename) || !(filename.ends_with(".wav") || filename.ends_with(".flac"))
        {
            return None;
        }
        let path = self.store.directory().join(filename);
        if fs::symlink_metadata(&path).is_ok_and(|m| m.file_type().is_symlink()) {
            return None;
        }
        Some(path)
    }
    pub fn path(&self, id: &str) -> Option<PathBuf> {
        let item = self.get(id)?;
        self.path_for(item["filename"].as_str()?)
            .filter(|p| p.is_file())
    }
    fn save_index(&self, index: &Index) -> Result<()> {
        let _timing = vocal_more_core::diagnostics::Timing::new("history_index");
        atomic_write_limited(
            &self.store.directory().join("history.json"),
            &serde_json::to_vec_pretty(index)?,
            MAX_INDEX_BYTES,
        )
    }
    fn persist(&self) -> Result<()> {
        self.save_index(&self.state.lock().unwrap())
    }

    pub fn register(&self, record: &Recording, mode: &str, language: &str) -> Result<()> {
        self.register_result(record, mode, language, None)
    }

    pub fn register_completed(
        &self,
        record: &Recording,
        mode: &str,
        language: &str,
        transcript: &str,
        billing: Value,
    ) -> Result<()> {
        self.register_result(record, mode, language, Some((transcript, billing)))
    }

    fn register_result(
        &self,
        record: &Recording,
        mode: &str,
        language: &str,
        result: Option<(&str, Value)>,
    ) -> Result<()> {
        let mut index = self.state.lock().unwrap();
        let Some(next) = Self::registration(
            &index,
            &self.pinned.lock().unwrap(),
            record,
            mode,
            language,
            result,
        ) else {
            return Ok(());
        };
        self.save_index(&next)?;
        *index = next;
        drop(index);
        self.cleanup_deleted()
    }

    fn registration(
        index: &Index,
        pinned: &HashSet<String>,
        record: &Recording,
        mode: &str,
        language: &str,
        result: Option<(&str, Value)>,
    ) -> Option<Index> {
        let id = record.id.to_string();
        let timestamp = chrono::DateTime::from_timestamp_millis(record.created_unix_ms as i64)
            .unwrap_or_default()
            .with_timezone(&chrono::Local)
            .format("%Y-%m-%dT%H:%M:%S")
            .to_string();
        if index.deleted.contains(&id) {
            return None;
        }
        let mut next = index.clone();
        let record_size = record.pcm_bytes + 44;
        if let Some(item) = next.recordings.iter_mut().find(|item| item["id"] == id) {
            item["duration_seconds"] = json!((record.pcm_bytes as f64 / 3200.0).round() / 10.0);
            item["original_bytes"] = json!(record_size);
            item["stored_bytes"] = json!(record_size);
        } else {
            next.recordings.push(json!({"id":id,"filename":format!("{id}.wav"),"timestamp":timestamp,"duration_seconds":(record.pcm_bytes as f64/3200.0).round()/10.0,
                "mode":mode,"asr_model":record.model,"language":language,"status":"pending","transcript":null,"error":null,"billing":null,"storage_format":"wav","original_bytes":record_size,"stored_bytes":record_size}));
        }
        if let Some((transcript, billing)) = result {
            let item = next
                .recordings
                .iter_mut()
                .find(|item| item["id"] == id)
                .unwrap();
            item["status"] = json!("success");
            item["transcript"] = json!(transcript);
            item["billing"] = billing;
            item["error"] = Value::Null;
        }
        // Retention and insertion share one durable transaction. Never evict
        // a pending workflow or a recording pinned for a retry.
        while next.recordings.len() > HISTORY_LIMIT {
            let oldest = next.recordings.iter().position(|r| {
                r["status"] != "pending" && r["id"].as_str().is_some_and(|id| !pinned.contains(id))
            });
            let Some(oldest) = oldest else {
                break;
            };
            if let Some(id) = next.recordings.remove(oldest)["id"].as_str() {
                next.deleted.insert(id.into());
            }
        }
        Some(next)
    }

    /// Flush an invisible final index while the core persists its own receipt.
    pub fn prepare_completed(
        &self,
        record: &Recording,
        mode: &str,
        language: &str,
        transcript: &str,
        billing: Value,
    ) -> Result<Option<PreparedHistory>> {
        let base = self.state.lock().unwrap().clone();
        let pinned = self.pinned.lock().unwrap().clone();
        let Some(next) = Self::registration(
            &base,
            &pinned,
            record,
            mode,
            language,
            Some((transcript, billing)),
        ) else {
            return Ok(None);
        };
        let write = prepare_atomic_write_limited(
            &self.store.directory().join("history.json"),
            &serde_json::to_vec_pretty(&next)?,
            MAX_INDEX_BYTES,
        )?;
        Ok(Some(PreparedHistory {
            base,
            next,
            pinned,
            write,
        }))
    }

    /// Reject stale snapshots if archive/retry/delete changed the index during
    /// core commit. The caller then rebuilds from the current index.
    pub fn commit_prepared(&self, prepared: PreparedHistory) -> Result<bool> {
        let mut index = self.state.lock().unwrap();
        if *index != prepared.base || *self.pinned.lock().unwrap() != prepared.pinned {
            return Ok(false);
        }
        prepared.write.commit()?;
        *index = prepared.next;
        drop(index);
        self.cleanup_deleted()?;
        Ok(true)
    }

    pub fn update(
        &self,
        id: &str,
        status: &str,
        transcript: Option<&str>,
        error: Option<&str>,
        billing: Option<Value>,
    ) -> Result<bool> {
        ensure!(
            matches!(status, "pending" | "success" | "failed"),
            "invalid history status"
        );
        let mut index = self.state.lock().unwrap();
        let mut next = index.clone();
        let Some(item) = next.recordings.iter_mut().find(|r| r["id"] == id) else {
            return Ok(false);
        };
        item["status"] = json!(status);
        if let Some(text) = transcript {
            item["transcript"] = json!(text);
        }
        if status == "success" || error.is_some() {
            item["error"] = json!(error);
        }
        if let Some(billing) = billing {
            item["billing"] = billing;
        }
        self.save_index(&next)?;
        *index = next;
        Ok(true)
    }

    pub fn delete(&self, id: &str) -> Result<bool> {
        ensure!(safe_basename(id), "invalid recording ID");
        let mut index = self.state.lock().unwrap();
        if !index.recordings.iter().any(|r| r["id"] == id) {
            return Ok(false);
        }
        let mut next = index.clone();
        next.recordings.retain(|r| r["id"] != id);
        next.deleted.insert(id.into());
        self.save_index(&next)?;
        *index = next;
        drop(index);
        self.cleanup_deleted()?;
        Ok(true)
    }
    fn cleanup_deleted(&self) -> Result<()> {
        let mut index = self.state.lock().unwrap();
        if index.deleted.is_empty() {
            return Ok(());
        }
        let mut next = index.clone();
        for id in &index.deleted {
            if !safe_basename(id) {
                continue;
            }
            let mut removed = true;
            for suffix in [".wav", ".flac", ".json"] {
                let path = self.store.directory().join(format!("{id}{suffix}"));
                if let Err(e) = fs::remove_file(path)
                    && e.kind() != std::io::ErrorKind::NotFound
                {
                    removed = false;
                }
            }
            if removed {
                next.deleted.remove(id);
            }
        }
        // Keep durable tombstones until the next index transaction. A crash
        // replays deletion safely; no second fsync is needed on the input path.
        *index = next;
        Ok(())
    }
    fn enforce_limit(&self) -> Result<()> {
        loop {
            let oldest = {
                let index = self.state.lock().unwrap();
                if index.recordings.len() <= HISTORY_LIMIT {
                    return Ok(());
                }
                let pinned = self.pinned.lock().unwrap();
                index
                    .recordings
                    .iter()
                    .find(|r| {
                        r["id"].as_str().is_some_and(|id| !pinned.contains(id))
                            && r["status"] != "pending"
                    })
                    .and_then(|r| r["id"].as_str())
                    .map(str::to_owned)
            };
            let Some(id) = oldest else { return Ok(()) };
            self.delete(&id)?;
        }
    }

    pub fn storage_summary(&self) -> Value {
        let items = self.list();
        let mut stored = 0_u64;
        let mut original = 0_u64;
        let mut compressed = 0;
        for item in &items {
            let Some(path) = self.path_for(item["filename"].as_str().unwrap_or("")) else {
                continue;
            };
            let Ok(meta) = fs::metadata(&path) else {
                continue;
            };
            stored += meta.len();
            if path.extension().is_some_and(|s| s == "flac") {
                compressed += 1;
                original += item["original_bytes"]
                    .as_u64()
                    .unwrap_or(meta.len())
                    .max(meta.len());
            } else {
                original += meta.len();
            }
        }
        json!({"recording_count":items.len(),"compressed_count":compressed,"original_bytes":original,"stored_bytes":stored,"bytes_saved":original.saturating_sub(stored)})
    }

    /// Owned decode scope: callers keep this alive while playback/retry uses its
    /// path. Closing an operation drops only its temporary decode, not history.
    pub async fn wav(&self, id: &str, cancel: &CancellationToken) -> Result<WaveLease> {
        let path = self.path(id).context("recording file not found")?;
        if path.extension().is_some_and(|s| s == "wav") {
            return Ok(WaveLease {
                path,
                _temporary: None,
            });
        }
        let temp = tempfile::tempdir_in(self.store.directory())?;
        let output = temp.path().join("decoded.wav");
        convert(&path, &output, false, cancel).await?;
        Ok(WaveLease {
            path: output,
            _temporary: Some(temp),
        })
    }

    pub async fn compact(
        &self,
        keep_recent: usize,
        max_files: usize,
        cancel: &CancellationToken,
    ) -> Result<Value> {
        let _lane = tokio::select! { _ = cancel.cancelled() => anyhow::bail!("history compaction cancelled"), lane = self.archive.lock() => lane };
        let candidates = self
            .list()
            .into_iter()
            .skip(keep_recent)
            .filter(|r| {
                matches!(r["status"].as_str(), Some("success" | "failed"))
                    && r["storage_format"] == "wav"
            })
            .take(max_files)
            .collect::<Vec<_>>();
        let mut result =
            json!({"compressed_count":0,"bytes_saved":0,"error_count":0,"skipped_count":0});
        for candidate in candidates {
            if cancel.is_cancelled() {
                break;
            }
            match self.compact_one(&candidate, cancel).await {
                Ok(Some(saved)) => {
                    result["compressed_count"] =
                        json!(result["compressed_count"].as_u64().unwrap() + 1);
                    result["bytes_saved"] = json!(result["bytes_saved"].as_u64().unwrap() + saved);
                }
                Ok(None) => {
                    result["skipped_count"] = json!(result["skipped_count"].as_u64().unwrap() + 1)
                }
                Err(_) => {
                    result["error_count"] = json!(result["error_count"].as_u64().unwrap() + 1)
                }
            }
        }
        result["storage"] = self.storage_summary();
        Ok(result)
    }
    async fn compact_one(
        &self,
        candidate: &Value,
        cancel: &CancellationToken,
    ) -> Result<Option<u64>> {
        let id = candidate["id"].as_str().context("recording ID missing")?;
        if self.pinned.lock().unwrap().contains(id) {
            return Ok(None);
        }
        let source = self.path(id).context("recording file not found")?;
        if source.extension().is_none_or(|s| s != "wav") {
            return Ok(None);
        }
        let original_size = fs::metadata(&source)?.len();
        let hash_source = source.clone();
        let hash_cancel = cancel.clone();
        let digest =
            tokio::task::spawn_blocking(move || pcm_digest(&hash_source, &hash_cancel)).await??;
        let temporary = tempfile::tempdir_in(self.store.directory())?;
        let encoded = temporary.path().join("encoded.flac");
        let decoded = temporary.path().join("decoded.wav");
        convert(&source, &encoded, true, cancel).await?;
        let encoded_size = fs::metadata(&encoded)?.len();
        if encoded_size >= original_size {
            return Ok(None);
        }
        convert(&encoded, &decoded, false, cancel).await?;
        let hash_cancel = cancel.clone();
        let verified =
            tokio::task::spawn_blocking(move || pcm_digest(&decoded, &hash_cancel)).await??;
        ensure!(verified == digest, "lossless PCM verification failed");
        ensure!(!cancel.is_cancelled(), "history compaction cancelled");
        let mut index = self.state.lock().unwrap();
        let mut next = index.clone();
        let Some(current) = next.recordings.iter_mut().find(|r| r["id"] == id) else {
            return Ok(None);
        };
        if current["filename"] != candidate["filename"]
            || self.pinned.lock().unwrap().contains(id)
            || !source.exists()
        {
            return Ok(None);
        }
        let filename = format!("{id}.flac");
        let destination = self.store.directory().join(&filename);
        fs::rename(&encoded, &destination)?;
        current["filename"] = json!(filename);
        current["storage_format"] = json!("flac");
        current["original_bytes"] = json!(original_size);
        current["stored_bytes"] = json!(encoded_size);
        if let Err(error) = self.save_index(&next) {
            let _ = fs::remove_file(&destination);
            return Err(error);
        }
        *index = next;
        // The core sidecar remains a crash-recovery record. The application
        // index is authoritative for final text and compressed playback paths.
        fs::remove_file(&source)?;
        Ok(Some(original_size - encoded_size))
    }

    pub fn import_legacy(&self, source: &Path) -> Result<usize> {
        let source = fs::canonicalize(source)?;
        ensure!(
            source != self.store.directory(),
            "legacy import requires a separate directory"
        );
        let index_path = source.join("recordings.json");
        if !index_path.exists() {
            return Ok(0);
        }
        ensure!(
            fs::metadata(&index_path)?.len() <= MAX_INDEX_BYTES,
            "legacy history index exceeds size limit"
        );
        let records: Vec<Value> = serde_json::from_slice(&fs::read(index_path)?)
            .context("invalid legacy history index")?;
        let mut imported = 0;
        for mut record in records
            .into_iter()
            .rev()
            .take(HISTORY_LIMIT)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
        {
            let (Some(id), Some(filename)) = (record["id"].as_str(), record["filename"].as_str())
            else {
                continue;
            };
            if !safe_basename(id) || !safe_basename(filename) || self.get(id).is_some() {
                continue;
            }
            let source_file = source.join(filename);
            if fs::symlink_metadata(&source_file).is_ok_and(|m| m.file_type().is_symlink()) {
                continue;
            }
            let Some(destination) = self.path_for(filename) else {
                continue;
            };
            if !source_file.is_file() || destination.exists() {
                continue;
            }
            fs::copy(&source_file, &destination)?;
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                fs::set_permissions(&destination, fs::Permissions::from_mode(0o600))?;
            }
            let stored_size = fs::metadata(&destination)?.len();
            if record.get("storage_format").is_none() {
                record["storage_format"] = json!(if filename.ends_with(".flac") {
                    "flac"
                } else {
                    "wav"
                });
            }
            for key in ["transcript", "error", "billing"] {
                if record.get(key).is_none() {
                    record[key] = Value::Null;
                }
            }
            if record.get("original_bytes").is_none() {
                record["original_bytes"] = json!(stored_size);
            }
            record["stored_bytes"] = json!(stored_size);
            if record["status"] == "pending" {
                record["status"] = json!("failed");
                record["error"] = json!("Imported incomplete processing; retry transcription");
            }
            let mut index = self.state.lock().unwrap();
            let mut next = index.clone();
            next.recordings.push(record);
            if let Err(error) = self.save_index(&next) {
                let _ = fs::remove_file(destination);
                return Err(error);
            }
            *index = next;
            imported += 1;
        }
        self.enforce_limit()?;
        Ok(imported)
    }
}

fn safe_basename(value: &str) -> bool {
    !value.is_empty()
        && value.trim() == value
        && !value.contains(['/', '\\', '\0'])
        && !matches!(value, "." | "..")
}
pub struct WaveLease {
    pub path: PathBuf,
    _temporary: Option<tempfile::TempDir>,
}

async fn convert(
    source: &Path,
    destination: &Path,
    encode: bool,
    cancel: &CancellationToken,
) -> Result<()> {
    ensure!(
        cfg!(target_os = "macos"),
        "lossless archival requires the macOS audio converter"
    );
    let mut command = tokio::process::Command::new("/usr/bin/afconvert");
    command
        .arg(source)
        .arg(destination)
        .args(if encode {
            ["-f", "flac", "-d", "flac"]
        } else {
            ["-f", "WAVE", "-d", "LEI16@16000"]
        })
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .kill_on_drop(true);
    let mut child = command
        .spawn()
        .context("cannot start lossless audio converter")?;
    tokio::select! {
        _ = cancel.cancelled() => { child.kill().await?; let _ = child.wait().await; anyhow::bail!("audio conversion cancelled"); },
        result = tokio::time::timeout(Duration::from_secs(120),child.wait()) => {
            let status = match result { Ok(status) => status?, Err(_) => { child.kill().await?; let _ = child.wait().await; anyhow::bail!("audio conversion timed out"); } };
            ensure!(status.success() && destination.exists(),"lossless audio conversion failed"); Ok(())
        }
    }
}

fn pcm_digest(path: &Path, cancel: &CancellationToken) -> Result<(Vec<u8>, u32, u16, u16, u32)> {
    let reader = hound::WavReader::open(path)?;
    let spec = reader.spec();
    let duration = reader.duration();
    ensure!(
        spec.sample_format == hound::SampleFormat::Int && spec.bits_per_sample == 16,
        "unsupported PCM verification format"
    );
    let mut remaining = reader.len() as u64 * 2;
    let mut source = reader.into_inner();
    let mut digest = Sha256::new();
    let mut buffer = [0u8; 65536];
    // Hound has located the data chunk and validated PCM16. Hash its exact
    // little-endian bytes without decoding/re-encoding every sample.
    while remaining > 0 {
        ensure!(!cancel.is_cancelled(), "history verification cancelled");
        let count = remaining.min(buffer.len() as u64) as usize;
        source.read_exact(&mut buffer[..count])?;
        digest.update(&buffer[..count]);
        remaining -= count as u64;
    }
    Ok((
        digest.finalize().to_vec(),
        spec.sample_rate,
        spec.channels,
        spec.bits_per_sample,
        duration,
    ))
}
