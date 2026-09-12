// SPDX-License-Identifier: GPL-3.0-only
//! A session owns one streaming WAV writer. No whole-recording PCM allocation.
use anyhow::{Context, Result, bail, ensure};
use fs2::FileExt;
use serde::{Deserialize, Serialize};
use std::{
    path::{Path, PathBuf},
    sync::Arc,
    time::{SystemTime, UNIX_EPOCH},
};
use tokio::{
    fs::{self, File},
    io::{AsyncReadExt, AsyncSeekExt, AsyncWriteExt, BufWriter},
};
use uuid::Uuid;

const HEADER_BYTES: u64 = 44;
const MAX_PCM_BYTES: u64 = u32::MAX as u64 - 36;
const MAX_METADATA_BYTES: usize = 2 * 1024 * 1024;
const STORE_MARKER: &str = ".vocal-more-rust-store.json";

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Recording {
    pub schema_version: u32,
    pub id: Uuid,
    pub generation: u64,
    pub created_unix_ms: u64,
    pub status: String,
    pub pcm_bytes: u64,
    pub sample_rate: u32,
    pub channels: u16,
    pub filename: String,
    pub model: String,
    pub transcript: String,
    pub error: Option<String>,
}

struct StoreInner {
    dir: PathBuf,
    // An OS lock is released on process death, allowing crash recovery.
    _lock: std::fs::File,
}

#[derive(Clone)]
pub struct RecordingStore(Arc<StoreInner>);

impl RecordingStore {
    pub async fn open(dir: impl AsRef<Path>) -> Result<Self> {
        let dir = dir.as_ref();
        fs::create_dir_all(dir)
            .await
            .context("create Rust recording directory")?;
        let dir = fs::canonicalize(dir).await?;
        if !dir.join(STORE_MARKER).exists() {
            let mut entries = fs::read_dir(&dir).await?;
            while let Some(entry) = entries.next_entry().await? {
                ensure!(
                    entry.file_name() == ".vocal-more-rust.lock",
                    "data directory is not empty and is not a Rust store; choose a separate directory"
                );
            }
        }
        let lock = std::fs::OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(dir.join(".vocal-more-rust.lock"))?;
        FileExt::try_lock_exclusive(&lock).context("another Rust host owns the data directory")?;
        if !dir.join(STORE_MARKER).exists() {
            let mut marker = File::options()
                .create_new(true)
                .write(true)
                .open(dir.join(STORE_MARKER))
                .await?;
            marker
                .write_all(b"{\"schema_version\":1,\"owner\":\"vocal-more-rust\"}\n")
                .await?;
            marker.sync_all().await?;
        } else {
            let marker: serde_json::Value =
                serde_json::from_slice(&fs::read(dir.join(STORE_MARKER)).await?)?;
            ensure!(
                marker["schema_version"] == 1 && marker["owner"] == "vocal-more-rust",
                "unsupported Rust store format"
            );
        }
        let store = Self(Arc::new(StoreInner { dir, _lock: lock }));
        store.recover().await?;
        Ok(store)
    }

    pub fn directory(&self) -> &Path {
        &self.0.dir
    }

    pub async fn create(&self, generation: u64, model: &str) -> Result<RecordingWriter> {
        let mut writer = self.reserve(generation, model)?;
        writer.ensure_open().await?;
        Ok(writer)
    }

    /// Reserve identity without putting durable file creation on the control
    /// path. The session opens it on its first append (or terminal commit).
    pub fn reserve(&self, generation: u64, model: &str) -> Result<RecordingWriter> {
        let id = Uuid::new_v4();
        let partial = self.0.dir.join(format!("{id}.wav.part"));
        let record = Recording {
            schema_version: 1,
            id,
            generation,
            created_unix_ms: SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis() as u64,
            status: "recording".into(),
            pcm_bytes: 0,
            sample_rate: crate::SAMPLE_RATE,
            channels: 1,
            filename: format!("{id}.wav.part"),
            model: model.into(),
            transcript: String::new(),
            error: None,
        };
        Ok(RecordingWriter {
            store: self.clone(),
            writer: None,
            sealed: false,
            record,
            partial,
        })
    }

    pub async fn get(&self, id: Uuid) -> Result<Recording> {
        self.read_metadata(&self.0.dir.join(format!("{id}.json")))
            .await
    }

    pub async fn list(&self) -> Result<Vec<Recording>> {
        let mut result = Vec::new();
        let mut entries = fs::read_dir(&self.0.dir).await?;
        while let Some(entry) = entries.next_entry().await? {
            let path = entry.path();
            if path.extension().is_some_and(|x| x == "json")
                && path
                    .file_stem()
                    .and_then(|x| x.to_str())
                    .is_some_and(|x| Uuid::parse_str(x).is_ok())
            {
                let record = self.read_metadata(&path).await?;
                result.push(record);
            }
        }
        result.sort_unstable_by_key(|r| std::cmp::Reverse(r.created_unix_ms));
        Ok(result)
    }

    async fn read_metadata(&self, path: &Path) -> Result<Recording> {
        ensure!(
            fs::metadata(path).await?.len() <= MAX_METADATA_BYTES as u64,
            "recording metadata exceeds size limit"
        );
        let record: Recording = serde_json::from_slice(&fs::read(path).await?)?;
        ensure!(record.schema_version == 1, "unsupported recording schema");
        let file_id = path
            .file_stem()
            .and_then(|s| s.to_str())
            .context("invalid recording path")?;
        ensure!(
            file_id == record.id.to_string(),
            "recording ID does not match metadata path"
        );
        Ok(record)
    }

    async fn save_metadata(&self, record: &Recording) -> Result<()> {
        let _timing = crate::diagnostics::Timing::new("core_metadata");
        let encoded = serde_json::to_vec_pretty(record)?;
        ensure!(
            encoded.len() <= MAX_METADATA_BYTES,
            "recording metadata exceeds size limit"
        );
        let path = self.0.dir.join(format!("{}.json", record.id));
        let temporary = path.with_extension("json.tmp");
        let mut file = File::create(&temporary).await?;
        file.write_all(&encoded).await?;
        file.sync_all().await?;
        drop(file);
        fs::rename(&temporary, path).await?;
        self.sync_directory().await
    }

    async fn sync_directory(&self) -> Result<()> {
        #[cfg(unix)]
        File::open(&self.0.dir).await?.sync_all().await?;
        Ok(())
    }

    async fn recover(&self) -> Result<()> {
        let mut entries = fs::read_dir(&self.0.dir).await?;
        while let Some(entry) = entries.next_entry().await? {
            let name = entry.file_name().to_string_lossy().to_string();
            let Some(id) = name
                .strip_suffix(".wav.part")
                .and_then(|s| Uuid::parse_str(s).ok())
            else {
                continue;
            };
            let path = entry.path();
            let mut file = File::options().read(true).write(true).open(&path).await?;
            let length = file.metadata().await?.len();
            ensure!(
                length >= HEADER_BYTES,
                "truncated WAV header in {id}; preserve file for manual recovery"
            );
            let mut header = [0u8; 44];
            file.read_exact(&mut header).await?;
            let expected = wav_header(0)?;
            ensure!(
                header[..4] == expected[..4] && header[8..40] == expected[8..40],
                "invalid WAV header in {id}; preserve file for manual recovery"
            );
            let pcm_bytes = (length - HEADER_BYTES) & !1;
            file.set_len(HEADER_BYTES + pcm_bytes).await?;
            file.seek(std::io::SeekFrom::Start(0)).await?;
            file.write_all(&wav_header(pcm_bytes)?).await?;
            file.sync_all().await?;
            drop(file);
            let metadata = self.0.dir.join(format!("{id}.json"));
            let mut record = if metadata.exists() {
                self.read_metadata(&metadata).await?
            } else {
                Recording {
                    schema_version: 1,
                    id,
                    generation: 0,
                    created_unix_ms: 0,
                    status: "recording".into(),
                    pcm_bytes: 0,
                    sample_rate: crate::SAMPLE_RATE,
                    channels: 1,
                    filename: String::new(),
                    model: String::new(),
                    transcript: String::new(),
                    error: None,
                }
            };
            record.pcm_bytes = pcm_bytes;
            record.filename = format!("{id}.wav");
            record.status = "interrupted".into();
            record.transcript.clear();
            record.error = Some("previous process exited before recording completed".into());
            let finished = self.0.dir.join(&record.filename);
            ensure!(
                !finished.exists(),
                "both final and partial WAV exist for {id}"
            );
            fs::rename(path, finished).await?;
            self.save_metadata(&record).await?;
        }
        // Covers death between WAV rename and final metadata commit.
        for mut record in self.list().await? {
            if record.status == "recording" {
                let final_name = format!("{}.wav", record.id);
                let path = self.0.dir.join(&final_name);
                if path.exists() {
                    record.pcm_bytes = fs::metadata(path).await?.len().saturating_sub(HEADER_BYTES);
                    record.filename = final_name;
                    record.status = "interrupted".into();
                    record.transcript.clear();
                    record.error = Some("audio saved; completion metadata was interrupted".into());
                    self.save_metadata(&record).await?;
                }
            }
        }
        Ok(())
    }
}

pub struct RecordingWriter {
    store: RecordingStore,
    writer: Option<BufWriter<File>>,
    sealed: bool,
    record: Recording,
    partial: PathBuf,
}

impl RecordingWriter {
    async fn ensure_open(&mut self) -> Result<()> {
        if self.writer.is_none() {
            let mut file = File::options()
                .create_new(true)
                .write(true)
                .read(true)
                .open(&self.partial)
                .await?;
            file.write_all(&wav_header(0)?).await?;
            file.sync_all().await?;
            self.store.save_metadata(&self.record).await?;
            self.writer = Some(BufWriter::with_capacity(64 * 1024, file));
        }
        Ok(())
    }
    pub fn id(&self) -> Uuid {
        self.record.id
    }
    pub fn pcm_bytes(&self) -> u64 {
        self.record.pcm_bytes
    }

    pub(crate) fn sealed_record(&self) -> Option<Recording> {
        self.sealed.then(|| self.record.clone())
    }

    pub async fn append(&mut self, pcm: &[u8]) -> Result<()> {
        ensure!(!self.sealed, "recording audio is already sealed");
        ensure!(
            pcm.len().is_multiple_of(2),
            "PCM16 input must contain complete samples"
        );
        ensure!(
            self.record.pcm_bytes + pcm.len() as u64 <= MAX_PCM_BYTES,
            "WAV size limit reached"
        );
        self.ensure_open().await?;
        self.writer.as_mut().unwrap().write_all(pcm).await?;
        self.record.pcm_bytes += pcm.len() as u64;
        Ok(())
    }

    /// Seal accepted audio while the provider finalizes its response. Final
    /// metadata is still committed before publishing a terminal session state.
    pub async fn seal_audio(&mut self) -> Result<()> {
        if self.sealed {
            return Ok(());
        }
        let _timing = crate::diagnostics::Timing::new("core_seal");
        self.ensure_open().await?;
        let mut writer = self.writer.take().unwrap();
        writer.flush().await?;
        let mut file = writer.into_inner();
        // A cancelled write_all may have persisted only a prefix. Derive the
        // final header from bytes actually present, retaining complete samples.
        self.record.pcm_bytes = (file.metadata().await?.len().saturating_sub(HEADER_BYTES)) & !1;
        file.set_len(HEADER_BYTES + self.record.pcm_bytes).await?;
        file.seek(std::io::SeekFrom::Start(0)).await?;
        file.write_all(&wav_header(self.record.pcm_bytes)?).await?;
        file.sync_all().await?;
        drop(file);
        self.record.filename = format!("{}.wav", self.record.id);
        fs::rename(&self.partial, self.store.0.dir.join(&self.record.filename)).await?;
        self.sealed = true;
        Ok(())
    }

    pub async fn finish(
        mut self,
        status: &str,
        transcript: String,
        error: Option<String>,
    ) -> Result<Recording> {
        ensure!(
            matches!(status, "completed" | "cancelled" | "failed"),
            "invalid terminal recording status"
        );
        self.seal_audio().await?;
        self.record.status = status.into();
        self.record.transcript = if status == "completed" {
            transcript
        } else {
            String::new()
        };
        self.record.error = error;
        self.store.save_metadata(&self.record).await?;
        Ok(self.record)
    }
}

fn wav_header(pcm_bytes: u64) -> Result<[u8; 44]> {
    if pcm_bytes > MAX_PCM_BYTES || !pcm_bytes.is_multiple_of(2) {
        bail!("invalid WAV PCM length")
    }
    let mut header = [0u8; 44];
    header[0..4].copy_from_slice(b"RIFF");
    header[4..8].copy_from_slice(&(36 + pcm_bytes as u32).to_le_bytes());
    header[8..16].copy_from_slice(b"WAVEfmt ");
    header[16..20].copy_from_slice(&16u32.to_le_bytes());
    header[20..22].copy_from_slice(&1u16.to_le_bytes());
    header[22..24].copy_from_slice(&1u16.to_le_bytes());
    header[24..28].copy_from_slice(&crate::SAMPLE_RATE.to_le_bytes());
    header[28..32].copy_from_slice(&(crate::SAMPLE_RATE * 2).to_le_bytes());
    header[32..34].copy_from_slice(&2u16.to_le_bytes());
    header[34..36].copy_from_slice(&16u16.to_le_bytes());
    header[36..40].copy_from_slice(b"data");
    header[40..44].copy_from_slice(&(pcm_bytes as u32).to_le_bytes());
    Ok(header)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn streamed_file_is_readable_and_cancel_does_not_publish_text() -> Result<()> {
        let temp = tempfile::tempdir()?;
        let store = RecordingStore::open(temp.path()).await?;
        let mut writer = store.create(1, "test").await?;
        writer.append(&[1, 0, 255, 127]).await?;
        assert!(writer.append(&[1]).await.is_err());
        writer.seal_audio().await?;
        assert!(writer.append(&[1, 0]).await.is_err());
        let record = writer
            .finish("cancelled", "must not escape".into(), None)
            .await?;
        assert!(record.transcript.is_empty());
        let reader = hound::WavReader::open(temp.path().join(record.filename))?;
        assert_eq!(reader.spec().sample_rate, 16_000);
        assert_eq!(
            reader
                .into_samples::<i16>()
                .collect::<Result<Vec<_>, _>>()?,
            [1, 32767]
        );
        Ok(())
    }

    #[tokio::test]
    async fn restores_interrupted_audio_and_excludes_other_hosts() -> Result<()> {
        let temp = tempfile::tempdir()?;
        let store = RecordingStore::open(temp.path()).await?;
        assert!(RecordingStore::open(temp.path()).await.is_err());
        let mut writer = store.create(2, "test").await?;
        writer.append(&[2, 0, 3, 0]).await?;
        writer.writer.as_mut().unwrap().flush().await?;
        drop(writer);
        drop(store);
        let recovered = RecordingStore::open(temp.path()).await?;
        let records = recovered.list().await?;
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].status, "interrupted");
        assert_eq!(records[0].pcm_bytes, 4);
        assert_eq!(
            hound::WavReader::open(temp.path().join(&records[0].filename))?.duration(),
            2
        );
        Ok(())
    }

    #[tokio::test]
    async fn reserved_identity_and_early_audio_seal_remain_recoverable() -> Result<()> {
        let temp = tempfile::tempdir()?;
        let store = RecordingStore::open(temp.path()).await?;
        let mut writer = store.reserve(7, "reserved-model")?;
        let id = writer.id();
        assert!(store.list().await?.is_empty());
        writer.append(&[7, 0, 9, 0]).await?;
        assert_eq!(store.get(id).await?.generation, 7);
        writer.seal_audio().await?;
        assert!(temp.path().join(format!("{id}.wav")).exists());
        drop(writer);
        drop(store);
        let recovered = RecordingStore::open(temp.path()).await?;
        let record = recovered.get(id).await?;
        assert_eq!(record.status, "interrupted");
        assert_eq!(record.model, "reserved-model");
        assert_eq!(record.pcm_bytes, 4);
        assert!(record.transcript.is_empty());
        Ok(())
    }

    #[tokio::test]
    async fn refuses_existing_foreign_data_directory() -> Result<()> {
        let temp = tempfile::tempdir()?;
        fs::write(temp.path().join("config.yaml"), b"existing user config").await?;
        assert!(RecordingStore::open(temp.path()).await.is_err());
        assert!(!temp.path().join(STORE_MARKER).exists());
        Ok(())
    }
}
