// SPDX-License-Identifier: GPL-3.0-only
use anyhow::{Context, Result, ensure};
use serde_json::Value;
use std::{fs, io::Write, path::Path};

pub const MAX_DOCUMENT_BYTES: u64 = 2 * 1024 * 1024;

pub fn read_yaml(path: &Path) -> Result<Option<Value>> {
    if !path.try_exists()? {
        return Ok(None);
    }
    ensure!(
        fs::metadata(path)?.len() <= MAX_DOCUMENT_BYTES,
        "document exceeds 2 MiB"
    );
    let text = fs::read_to_string(path).context("cannot read settings document")?;
    // Never include parser snippets: a malformed document can contain API keys.
    serde_saphyr::from_str(&text)
        .map(Some)
        .map_err(|_| anyhow::anyhow!("invalid YAML document"))
}

pub fn write_yaml(path: &Path, value: &impl serde::Serialize) -> Result<()> {
    let text = serde_saphyr::to_string(value).context("cannot serialize settings document")?;
    atomic_write(path, text.as_bytes())
}

pub fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    atomic_write_limited(path, bytes, MAX_DOCUMENT_BYTES)
}

/// A durable temporary file is invisible until explicitly committed. Dropping
/// a preparation (cancellation/failure) removes only its own temporary file.
pub struct PreparedWrite {
    temporary: tempfile::TempPath,
    destination: std::path::PathBuf,
}
impl PreparedWrite {
    pub fn commit(self) -> Result<()> {
        fs::rename(&self.temporary, &self.destination)?;
        #[cfg(unix)]
        fs::File::open(
            self.destination
                .parent()
                .context("document needs a parent directory")?,
        )?
        .sync_all()?;
        Ok(())
    }
}

pub fn prepare_atomic_write_limited(
    path: &Path,
    bytes: &[u8],
    limit: u64,
) -> Result<PreparedWrite> {
    ensure!(bytes.len() as u64 <= limit, "document exceeds size limit");
    let parent = path.parent().context("document needs a parent directory")?;
    fs::create_dir_all(parent)?;
    let mut file = tempfile::Builder::new()
        .prefix(".history-prepared-")
        .suffix(".tmp")
        .tempfile_in(parent)?;
    file.write_all(bytes)?;
    file.as_file().sync_all()?;
    Ok(PreparedWrite {
        temporary: file.into_temp_path(),
        destination: path.into(),
    })
}

pub fn atomic_write_limited(path: &Path, bytes: &[u8], limit: u64) -> Result<()> {
    prepare_atomic_write_limited(path, bytes, limit)?.commit()
}
