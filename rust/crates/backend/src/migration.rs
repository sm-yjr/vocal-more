// SPDX-License-Identifier: GPL-3.0-only
//! Copy Python data once into a separately owned directory. A SQLite online
//! backup includes live WAL transactions without checkpointing the source.
use crate::{config::ConfigRepository, dictionary::Dictionary, history::History, persistence};
use anyhow::{Context, Result, ensure};
use std::{fs, path::Path, time::Duration};
use vocal_more_core::recording::RecordingStore;

pub async fn import_python(source: &Path, destination: &Path) -> Result<bool> {
    let parent = destination
        .parent()
        .context("data directory needs a parent")?;
    fs::create_dir_all(parent)?;
    let lock = fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(parent.join(".rust-backend-import.lock"))?;
    fs2::FileExt::try_lock_exclusive(&lock).context("another data import is running")?;
    if destination.join("config.yaml").exists() || destination.join("migration.json").exists() {
        return Ok(false);
    }
    if !source.is_dir() {
        return Ok(false);
    }
    let source = fs::canonicalize(source)?;
    if destination.exists() {
        ensure!(
            fs::read_dir(destination)?.next().is_none(),
            "import destination must be empty"
        );
        fs::remove_dir(destination)?;
    }
    let staging = tempfile::Builder::new()
        .prefix(".rust-import-")
        .tempdir_in(parent)?;
    for name in ["config.yaml", "dictionary.yaml"] {
        let path = source.join(name);
        if path.is_file() {
            ensure!(
                !fs::symlink_metadata(&path)?.file_type().is_symlink(),
                "legacy documents must be regular files"
            );
            ensure!(
                fs::metadata(&path)?.len() <= persistence::MAX_DOCUMENT_BYTES,
                "legacy document exceeds size limit"
            );
            persistence::atomic_write(&staging.path().join(name), &fs::read(path)?)?;
        }
    }
    // Validate before publishing any part of the migrated application state.
    ConfigRepository::open(&staging.path().join("config.yaml"))?;
    Dictionary::open(&staging.path().join("dictionary.yaml"))?;
    let database = source.join("dictionary-learning.sqlite3");
    if database.is_file() {
        ensure!(
            !fs::symlink_metadata(&database)?.file_type().is_symlink(),
            "legacy database must be a regular file"
        );
        let original = rusqlite::Connection::open_with_flags(
            database,
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY,
        )?;
        original.busy_timeout(Duration::from_secs(5))?;
        let target = staging.path().join("dictionary-learning.sqlite3");
        let mut copy = rusqlite::Connection::open(&target)?;
        let backup = rusqlite::backup::Backup::new(&original, &mut copy)?;
        let deadline = std::time::Instant::now() + Duration::from_secs(10);
        loop {
            match backup.step(256)? {
                rusqlite::backup::StepResult::Done => break,
                _ => {
                    ensure!(
                        std::time::Instant::now() < deadline,
                        "legacy database backup timed out"
                    );
                    tokio::time::sleep(Duration::from_millis(10)).await;
                }
            }
        }
        drop(backup);
        drop(copy);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(target, fs::Permissions::from_mode(0o600))?;
        }
    }
    let recordings = source.join("recordings");
    let count = if recordings.is_dir() {
        let history =
            History::open(RecordingStore::open(staging.path().join("recordings")).await?).await?;
        history.import_legacy(&recordings)?
    } else {
        0
    };
    persistence::atomic_write(
        &staging.path().join("migration.json"),
        &serde_json::to_vec(&serde_json::json!({"schema":1,"source":"python","recordings":count}))?,
    )?;
    fs::rename(staging.path(), destination)?;
    #[cfg(unix)]
    fs::File::open(parent)?.sync_all()?;
    Ok(true)
}
