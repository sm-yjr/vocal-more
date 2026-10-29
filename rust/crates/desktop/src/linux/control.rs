// SPDX-License-Identifier: GPL-3.0-only
//! Private, per-profile control socket for desktop-managed global shortcuts.
//! Polling stays on the UI thread; no worker or audio session is started by CLI.
use anyhow::{Context, Result, bail, ensure};
use std::{
    fs::{self, File, OpenOptions},
    hash::{DefaultHasher, Hash, Hasher},
    io::ErrorKind,
    os::unix::{
        fs::{MetadataExt, OpenOptionsExt, PermissionsExt},
        net::UnixDatagram,
    },
    path::{Path, PathBuf},
    time::Duration,
};

pub fn method(command: &str) -> Option<&'static str> {
    if command
        .strip_prefix("shortcut:")
        .is_some_and(super::shortcuts::valid_id)
    {
        return Some("platform_shortcut_activated");
    }
    Some(match command {
        "ping" => "platform_ping",
        "toggle" => "toggle_recording",
        "input-enable" => "linux_input_enable",
        "input-disable" => "linux_input_disable",
        "codex-accessibility" => "linux_input_codex",
        "gestures-enable" => "linux_shortcuts_enable_gestures",
        "gestures-disable" => "linux_shortcuts_disable_gestures",
        "press" => "hotkey_pressed",
        "release" => "hotkey_released",
        "stop" => "stop",
        "cancel" => "cancel",
        "settings" => "platform_show_settings",
        "copy" => "platform_copy_last",
        "quit" => "platform_quit",
        _ => return None,
    })
}

pub fn default_data() -> Result<PathBuf> {
    let home = std::env::var_os("HOME").context("HOME is unavailable")?;
    Ok(std::env::var_os("XDG_DATA_HOME")
        .map(PathBuf::from)
        .filter(|p| p.is_absolute())
        .unwrap_or_else(|| PathBuf::from(home).join(".local/share"))
        .join("vocal-more"))
}

fn socket_path(data: &Path) -> Result<PathBuf> {
    // SAFETY: getuid has no preconditions and does not change process identity.
    let uid = unsafe { libc::getuid() };
    let base = std::env::var_os("XDG_RUNTIME_DIR")
        .map(PathBuf::from)
        .filter(|p| p.is_absolute())
        .unwrap_or_else(std::env::temp_dir);
    let directory = base.join(format!("vocal-more-{uid}"));
    match fs::create_dir(&directory) {
        Ok(()) => fs::set_permissions(&directory, fs::Permissions::from_mode(0o700))?,
        Err(error) if error.kind() == ErrorKind::AlreadyExists => {}
        Err(error) => return Err(error.into()),
    }
    let meta = fs::symlink_metadata(&directory)?;
    ensure!(
        meta.is_dir() && meta.uid() == uid && meta.mode() & 0o077 == 0,
        "control directory must be owned by this user with mode 0700"
    );
    let absolute = if data.is_absolute() {
        data.to_owned()
    } else {
        std::env::current_dir()?.join(data)
    };
    let normalized = fs::canonicalize(&absolute).unwrap_or(absolute);
    let mut hash = DefaultHasher::new();
    normalized.hash(&mut hash);
    Ok(directory.join(format!("{:016x}.sock", hash.finish())))
}

pub fn send(data: &Path, command: &str) -> Result<()> {
    ensure!(
        method(command).is_some(),
        "unknown desktop command: {command}"
    );
    let path = socket_path(data)?;
    let client = path.with_extension(format!("client-{}", std::process::id()));
    let socket = UnixDatagram::bind(&client).context("bind control reply socket")?;
    let result = (|| {
        socket.set_read_timeout(Some(Duration::from_secs(2)))?;
        // The UI may be opening a capsule when a release arrives. Preserve the
        // local sender's monotonic event time rather than measuring UI delay.
        let payload = if matches!(command, "press" | "release") {
            format!("{command}@{}", monotonic_ns()?)
        } else {
            command.into()
        };
        socket
            .send_to(payload.as_bytes(), &path)
            .context("Vocal More is not running for this profile")?;
        let mut response = [0; 32];
        let count = socket
            .recv(&mut response)
            .context("desktop did not acknowledge the command")?;
        ensure!(
            &response[..count] == b"accepted",
            "desktop rejected the command"
        );
        Ok(())
    })();
    let _ = fs::remove_file(client);
    result
}

fn monotonic_ns() -> Result<u128> {
    let mut time = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: clock_gettime writes to this live, correctly sized timespec.
    ensure!(
        unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut time) } == 0,
        "monotonic clock unavailable"
    );
    Ok(time.tv_sec as u128 * 1_000_000_000 + time.tv_nsec as u128)
}
fn decode(payload: &str) -> Option<(&str, std::time::Instant)> {
    let now = std::time::Instant::now();
    if let Some((command, stamp)) = payload.split_once('@') {
        if !matches!(command, "press" | "release") {
            return None;
        }
        let age = monotonic_ns()
            .ok()?
            .checked_sub(stamp.parse::<u128>().ok()?)?;
        if age > 60_000_000_000 {
            return None;
        }
        Some((command, now.checked_sub(Duration::from_nanos(age as u64))?))
    } else {
        method(payload).map(|_| (payload, now))
    }
}

pub fn handle_cli() -> Result<bool> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    let Some(index) = args.iter().position(|a| a == "--command") else {
        return Ok(false);
    };
    let command = args.get(index + 1).context("--command requires a value")?;
    let mut data = default_data()?;
    let mut position = 0;
    while position < args.len() {
        match args[position].as_str() {
            "--command" => position += 2,
            "--data-dir" | "--backend-data-dir" => {
                data = args
                    .get(position + 1)
                    .context("missing --data-dir value")?
                    .into();
                position += 2;
            }
            flag => bail!("unsupported control option: {flag}"),
        }
    }
    send(&data, command)?;
    Ok(true)
}

pub struct Server {
    socket: UnixDatagram,
    path: PathBuf,
    _lock: File,
}
impl Server {
    pub fn bind(data: &Path) -> Result<Self> {
        fs::create_dir_all(data)?;
        let path = socket_path(data)?;
        let lock = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW)
            .open(path.with_extension("lock"))?;
        use std::os::fd::AsRawFd;
        // SAFETY: the owned File keeps this valid descriptor open for the lock lifetime.
        ensure!(
            unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } == 0,
            "Vocal More is already running for this profile"
        );
        if path.exists() {
            fs::remove_file(&path)?;
        }
        let socket = UnixDatagram::bind(&path)?;
        socket.set_nonblocking(true)?;
        Ok(Self {
            socket,
            path,
            _lock: lock,
        })
    }
    pub fn poll(&self, sink: &crate::bridge::CommandSink) {
        // Bound work so a command flood cannot starve rendering or cancellation.
        for _ in 0..32 {
            let mut bytes = [0; 64];
            let Ok((count, peer)) = self.socket.recv_from(&mut bytes) else {
                break;
            };
            let accepted = std::str::from_utf8(&bytes[..count])
                .ok()
                .and_then(decode)
                .is_some_and(|(command, admitted_at)| {
                    let Some(method) = method(command) else {
                        return false;
                    };
                    let params =
                        serde_json::json!({"shortcut_id":command.strip_prefix("shortcut:")});
                    sink.request_checked_admitted(method, params, admitted_at)
                        .is_ok()
                });
            if let Some(path) = peer.as_pathname() {
                let _ = self
                    .socket
                    .send_to(if accepted { b"accepted" } else { b"rejected" }, path);
            }
        }
    }
}
impl Drop for Server {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.path);
        use std::os::fd::AsRawFd;
        // Explicit unlock also releases a descriptor briefly inherited between
        // another thread's fork and exec (the File itself is close-on-exec).
        unsafe {
            libc::flock(self._lock.as_raw_fd(), libc::LOCK_UN);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn commands_are_an_explicit_allowlist() {
        assert_eq!(method("ping"), Some("platform_ping"));
        assert_eq!(method("toggle"), Some("toggle_recording"));
        assert_eq!(method("quit"), Some("platform_quit"));
        assert_eq!(method("set_config"), None);
        assert_eq!(
            method("shortcut:vocal-more-test"),
            Some("platform_shortcut_activated")
        );
        assert_eq!(method("shortcut:../bad"), None);
        assert_eq!(method("toggle\ncancel"), None);
    }
    #[test]
    fn local_key_time_is_bounded_and_other_commands_cannot_carry_timestamps() {
        assert!(decode(&format!("press@{}", monotonic_ns().unwrap())).is_some());
        for value in [
            "press@0",
            "release@999999999999999999999999",
            "cancel@0",
            "press@garbage",
            "toggle\ncancel",
        ] {
            assert!(decode(value).is_none());
        }
        assert_eq!(decode("release").unwrap().0, "release");
    }
    #[test]
    fn profile_has_one_owner_and_releases_lock() {
        let data = tempfile::tempdir().unwrap();
        let first = Server::bind(data.path()).unwrap();
        assert!(Server::bind(data.path()).is_err());
        drop(first);
        assert!(Server::bind(data.path()).is_ok());
    }
}
