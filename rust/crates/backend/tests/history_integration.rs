// SPDX-License-Identifier: GPL-3.0-only
use anyhow::Result;
use serde_json::json;
use std::fs;
use tokio_util::sync::CancellationToken;
use vocal_more_backend::{history::History, wave::wav_bytes};
use vocal_more_core::recording::RecordingStore;

async fn record(history: &History, generation: u64, pcm: &[u8]) -> Result<String> {
    let mut writer = history
        .store()
        .create(generation, "qwen3.5-omni-plus-realtime")
        .await?;
    writer.append(pcm).await?;
    let record = writer.finish("completed", "raw".into(), None).await?;
    history.register(&record, "realtime_long", "en")?;
    let id = record.id.to_string();
    history.update(&id, "success", Some("整理结果"), None, None)?;
    Ok(id)
}

#[tokio::test]
async fn history_retention_restart_delete_and_late_retry_do_not_resurrect_audio() -> Result<()> {
    let dir = tempfile::tempdir()?;
    let history = History::open(RecordingStore::open(dir.path()).await?).await?;
    let mut ids = Vec::new();
    for generation in 0..32 {
        ids.push(record(&history, generation, &[1, 0, 2, 0]).await?);
    }
    assert_eq!(history.list().len(), 30);
    assert!(history.get(&ids[0]).is_none());
    assert!(!dir.path().join(format!("{}.wav", ids[0])).exists());
    assert_eq!(history.list()[0]["id"], ids[31]);
    let deleted = &ids[5];
    history.delete(deleted)?;
    assert!(!history.update(deleted, "success", Some("late retry"), None, None)?);
    assert!(history.path("../outside").is_none());
    drop(history);
    let history = History::open(RecordingStore::open(dir.path()).await?).await?;
    assert_eq!(history.list().len(), 29);
    assert!(history.get(deleted).is_none());
    assert_eq!(history.storage_summary()["recording_count"], 29);
    Ok(())
}

#[tokio::test]
async fn interrupted_core_commit_is_recovered_as_retryable_without_publishing_raw_text()
-> Result<()> {
    let dir = tempfile::tempdir()?;
    let store = RecordingStore::open(dir.path()).await?;
    let mut writer = store.create(1, "test").await?;
    writer.append(&[1, 0, 2, 0]).await?;
    let completed = writer
        .finish("completed", "must-not-be-final".into(), None)
        .await?;
    drop(store);
    let history = History::open(RecordingStore::open(dir.path()).await?).await?;
    let item = history.get(&completed.id.to_string()).unwrap();
    assert_eq!(item["status"], "failed");
    assert!(item["transcript"].is_null());
    assert!(history.path(&completed.id.to_string()).is_some());
    Ok(())
}

#[tokio::test]
async fn legacy_import_copies_valid_entries_without_changing_originals() -> Result<()> {
    let original = tempfile::tempdir()?;
    let dir = tempfile::tempdir()?;
    let wav = wav_bytes(&[1, 0, 2, 0, 3, 0])?;
    fs::write(original.path().join("2026-09-10T10-00-00.wav"), &wav)?;
    let index = json!([
        {"id":"2026-09-10T10-00-00","filename":"2026-09-10T10-00-00.wav","status":"success","transcript":"原有记录","timestamp":"2026-09-10T10:00:00","language":"zh","duration_seconds":0.1},
        {"id":"escape","filename":"../outside.wav","status":"success"}
    ]).to_string();
    fs::write(original.path().join("recordings.json"), &index)?;
    let history = History::open(RecordingStore::open(dir.path()).await?).await?;
    assert_eq!(history.import_legacy(original.path())?, 1);
    assert_eq!(history.import_legacy(original.path())?, 0);
    let items = history.list();
    assert_eq!(items[0]["transcript"], "原有记录");
    assert_eq!(
        fs::read(history.path(items[0]["id"].as_str().unwrap()).unwrap())?,
        wav
    );
    history.delete("2026-09-10T10-00-00")?;
    assert_eq!(
        fs::read(original.path().join("2026-09-10T10-00-00.wav"))?,
        wav
    );
    assert_eq!(
        fs::read_to_string(original.path().join("recordings.json"))?,
        index
    );
    Ok(())
}

#[cfg(target_os = "macos")]
#[tokio::test]
async fn real_system_flac_round_trip_preserves_pcm_and_history_after_restart() -> Result<()> {
    let dir = tempfile::tempdir()?;
    let history = History::open(RecordingStore::open(dir.path()).await?).await?;
    let pcm = (0..32000)
        .flat_map(|i| ((i % 40) as i16 * 10).to_le_bytes())
        .collect::<Vec<_>>();
    let id = record(&history, 1, &pcm).await?;
    let result = history.compact(0, 1, &CancellationToken::new()).await?;
    assert_eq!(result["compressed_count"], 1, "{result}");
    assert!(result["bytes_saved"].as_u64().unwrap() > 0);
    assert!(!dir.path().join(format!("{id}.wav")).exists());
    assert!(
        history
            .path(&id)
            .unwrap()
            .extension()
            .is_some_and(|s| s == "flac")
    );
    let lease = history.wav(&id, &CancellationToken::new()).await?;
    let decoded_path = lease.path.clone();
    let mut wav = hound::WavReader::open(&lease.path)?;
    let decoded = wav
        .samples::<i16>()
        .collect::<Result<Vec<_>, _>>()?
        .iter()
        .flat_map(|s| s.to_le_bytes())
        .collect::<Vec<_>>();
    assert_eq!(decoded, pcm);
    drop(wav);
    drop(lease);
    assert!(!decoded_path.exists());
    drop(history);
    let history = History::open(RecordingStore::open(dir.path()).await?).await?;
    assert_eq!(history.list().len(), 1);
    assert_eq!(history.get(&id).unwrap()["transcript"], "整理结果");
    assert_eq!(history.storage_summary()["compressed_count"], 1);
    history.delete(&id)?;
    assert!(history.list().is_empty());
    Ok(())
}

#[tokio::test]
async fn staged_history_is_invisible_discardable_and_rejects_stale_indexes() -> Result<()> {
    let dir = tempfile::tempdir()?;
    let history = History::open(RecordingStore::open(dir.path()).await?).await?;
    let mut writer = history.store().create(1, "test").await?;
    writer.append(&[1, 0, 2, 0]).await?;
    let completed = writer.finish("completed", "raw".into(), None).await?;
    let index_path = dir.path().join("history.json");
    let original = fs::read(&index_path)?;
    let staged = history
        .prepare_completed(&completed, "realtime_long", "auto", "raw", json!({}))?
        .unwrap();
    assert!(history.list().is_empty());
    assert_eq!(fs::read(&index_path)?, original);
    drop(staged);
    assert!(fs::read_dir(dir.path())?.all(|entry| {
        !entry
            .unwrap()
            .file_name()
            .to_string_lossy()
            .starts_with(".history-prepared-")
    }));
    let staged = history
        .prepare_completed(&completed, "realtime_long", "auto", "raw", json!({}))?
        .unwrap();
    let other = record(&history, 2, &[3, 0, 4, 0]).await?;
    assert!(!history.commit_prepared(staged)?);
    assert!(history.get(&other).is_some());
    assert!(history.get(&completed.id.to_string()).is_none());
    let staged = history
        .prepare_completed(&completed, "realtime_long", "auto", "raw", json!({}))?
        .unwrap();
    history.pin(&other);
    assert!(!history.commit_prepared(staged)?);
    history.unpin(&other);
    let staged = history
        .prepare_completed(&completed, "realtime_long", "auto", "raw", json!({}))?
        .unwrap();
    assert!(history.commit_prepared(staged)?);
    assert_eq!(
        history.get(&completed.id.to_string()).unwrap()["status"],
        "success"
    );
    drop(history);
    let reopened = History::open(RecordingStore::open(dir.path()).await?).await?;
    assert_eq!(
        reopened.get(&completed.id.to_string()).unwrap()["transcript"],
        "raw"
    );
    assert!(reopened.get(&other).is_some());
    Ok(())
}

#[tokio::test]
async fn abandoned_preparation_is_removed_and_never_replayed_as_success() -> Result<()> {
    let dir = tempfile::tempdir()?;
    let history = History::open(RecordingStore::open(dir.path()).await?).await?;
    let mut writer = history.store().create(1, "test").await?;
    writer.append(&[1, 0, 2, 0]).await?;
    let completed = writer.finish("completed", "raw".into(), None).await?;
    let staged = history
        .prepare_completed(&completed, "realtime_long", "auto", "raw", json!({}))?
        .unwrap();
    // Simulate SIGKILL: no TempPath destructor and no visible index commit.
    std::mem::forget(staged);
    drop(history);
    let reopened = History::open(RecordingStore::open(dir.path()).await?).await?;
    let row = reopened.get(&completed.id.to_string()).unwrap();
    assert_eq!(row["status"], "failed");
    assert!(row["transcript"].is_null());
    assert!(fs::read_dir(dir.path())?.all(|entry| {
        !entry
            .unwrap()
            .file_name()
            .to_string_lossy()
            .starts_with(".history-prepared-")
    }));
    Ok(())
}

#[cfg(target_os = "macos")]
#[tokio::test]
async fn short_recording_survives_system_codec_rejection() -> Result<()> {
    let dir = tempfile::tempdir()?;
    let history = History::open(RecordingStore::open(dir.path()).await?).await?;
    let pcm = [0_u8, 16].repeat(3200);
    let id = record(&history, 1, &pcm).await?;
    // Some macOS versions emit only an invalid FLAC header for short input.
    // Accept a future codec fix too, but never accept a truncated archive.
    history.compact(0, 1, &CancellationToken::new()).await?;
    let lease = history.wav(&id, &CancellationToken::new()).await?;
    let decoded = hound::WavReader::open(&lease.path)?
        .samples::<i16>()
        .collect::<Result<Vec<_>, _>>()?
        .into_iter()
        .flat_map(i16::to_le_bytes)
        .collect::<Vec<_>>();
    assert_eq!(decoded, pcm);
    Ok(())
}
