"""Recording store: save, index, and manage recent audio recordings."""

import base64
import hashlib
import io
import json
import os
import subprocess
import tempfile
import threading
import wave
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..application.background_executor import BackgroundExecutor, TaskHandle
from ..domain.audio_contract import (
    OUTPUT_CHANNELS,
    OUTPUT_SAMPLE_RATE_HZ,
    PCM_SAMPLE_WIDTH_BYTES,
)


SAMPLE_RATE = OUTPUT_SAMPLE_RATE_HZ
CHANNELS = OUTPUT_CHANNELS
SAMPLE_WIDTH = PCM_SAMPLE_WIDTH_BYTES
MAX_RECORDINGS = 30
_MISSING = object()
_RUNNING_MEETING_STATUSES = {"transcribing", "summarizing"}
_TERMINAL_RECORDING_STATUSES = {"success", "failed"}


class AppleLosslessAudioCodec:
    """Lossless WAV/FLAC conversion through macOS AudioToolbox."""

    def __init__(self, executable: str = "/usr/bin/afconvert") -> None:
        self.executable = executable

    def encode_wav_to_flac(self, source: Path, destination: Path) -> None:
        self._run(source, destination, "-f", "flac", "-d", "flac")

    def decode_flac_to_wav(self, source: Path, destination: Path) -> None:
        self._run(
            source,
            destination,
            "-f",
            "WAVE",
            "-d",
            f"LEI16@{SAMPLE_RATE}",
        )

    def _run(self, source: Path, destination: Path, *options: str) -> None:
        result = subprocess.run(
            [self.executable, str(source), str(destination), *options],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not destination.exists():
            details = (result.stderr or result.stdout or "conversion failed").strip()
            raise RuntimeError(f"afconvert failed: {details}")


class RecordingStore:
    """Persist recent recordings as WAV files with a JSON index."""

    def __init__(
        self,
        recordings_dir: Optional[str] = None,
        *,
        audio_codec: Optional[object] = None,
        auto_compact: bool = True,
    ):
        if recordings_dir is None:
            self._dir = Path.home() / ".vocal-more" / "recordings"
        else:
            self._dir = Path(recordings_dir).expanduser()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._dir / "recordings.json"
        self._lock = threading.Lock()
        self._archive_lock = threading.Lock()
        self._audio_codec = audio_codec or AppleLosslessAudioCodec()
        self._auto_compact = bool(auto_compact)
        self._compaction_pending = False
        self._compaction_executor = BackgroundExecutor(
            max_workers=1,
            thread_name_prefix="vocal-more-recording-archive",
        )
        self._recordings: list[dict] = self._load_index()
        self._prune_orphans()
        self._last_id: Optional[str] = None
        self._id_counter = 0
        if self._auto_compact:
            self.schedule_history_compaction()

    def _load_index(self) -> list[dict]:
        if not self._index_path.exists():
            return []
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                return []
            normalized = []
            for entry in data:
                if isinstance(entry, dict):
                    audio_path = self._recording_path_for_filename(entry.get("filename"))
                    if audio_path is None:
                        continue
                    entry = dict(entry)
                    entry["filename"] = audio_path.name
                    entry.setdefault(
                        "storage_format",
                        "flac" if audio_path.suffix.lower() == ".flac" else "wav",
                    )
                    entry.setdefault("original_bytes", None)
                    entry.setdefault("stored_bytes", None)
                    entry.setdefault("transcript", None)
                    entry.setdefault("error", None)
                    entry.setdefault("billing", None)
                    entry.setdefault("meeting", None)
                    normalized.append(entry)
            return normalized
        except (json.JSONDecodeError, OSError):
            backup = self._index_path.with_suffix(".json.bak")
            try:
                self._index_path.rename(backup)
            except OSError:
                pass
            return []

    def _prune_orphans(self) -> None:
        """Remove index entries whose WAV files no longer exist."""
        before = len(self._recordings)
        self._recordings = [
            r for r in self._recordings
            if (audio_path := self._recording_path_for_filename(r.get("filename"))) is not None
            and audio_path.exists()
        ]
        pruned = before - len(self._recordings)
        if pruned:
            print(f"[RecordingStore] Pruned {pruned} orphaned index entries")
            self._save_index()

    def _save_index(self) -> bool:
        temp_path: Optional[Path] = None
        try:
            fd, temp_name = tempfile.mkstemp(
                prefix=".recordings.",
                suffix=".json.tmp",
                dir=str(self._dir),
            )
            temp_path = Path(temp_name)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    self._recordings,
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self._index_path)
            return True
        except OSError as e:
            print(f"[RecordingStore] Failed to write index: {e}")
            return False
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink()
                except FileNotFoundError:
                    pass

    def save(self, pcm_data: bytes, mode: str, asr_model: str, language: str = "zh") -> str:
        """Save PCM data as WAV and add to index. Returns recording ID."""
        now = datetime.now()
        base_id = now.strftime("%Y-%m-%dT%H-%M-%S")
        with self._lock:
            if base_id == self._last_id:
                self._id_counter += 1
                rec_id = f"{base_id}-{self._id_counter}"
            else:
                self._last_id = base_id
                self._id_counter = 0
                rec_id = base_id
        filename = f"{rec_id}.wav"
        wav_path = self._dir / filename

        try:
            with wave.open(str(wav_path), "wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(SAMPLE_WIDTH)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(pcm_data)
        except OSError as e:
            print(f"[RecordingStore] Failed to save WAV: {e}")
            return rec_id

        duration = len(pcm_data) / (SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH)

        entry = {
            "id": rec_id,
            "filename": filename,
            "timestamp": now.isoformat(timespec="seconds"),
            "duration_seconds": round(duration, 1),
            "mode": mode,
            "asr_model": asr_model,
            "language": language,
            "status": "pending",
            "transcript": None,
            "error": None,
            "meeting": None,
            "storage_format": "wav",
            "original_bytes": wav_path.stat().st_size,
            "stored_bytes": wav_path.stat().st_size,
        }

        with self._lock:
            self._recordings.append(entry)
            self._enforce_limit()
            self._save_index()

        print(f"[RecordingStore] Saved {filename} ({duration:.1f}s)")
        return rec_id

    def update(
        self,
        recording_id: str,
        status: str,
        transcript: Optional[str] = _MISSING,
        *,
        error: Optional[str] = _MISSING,
        billing: Optional[dict] = _MISSING,
        meeting: Optional[dict] = _MISSING,
    ) -> bool:
        """Update status and transcript for a recording."""
        updated = False
        with self._lock:
            for rec in self._recordings:
                if rec["id"] == recording_id:
                    updated = True
                    rec["status"] = status
                    if transcript is not _MISSING:
                        rec["transcript"] = transcript
                    if error is not _MISSING:
                        rec["error"] = error
                    elif status == "success":
                        rec["error"] = None
                    if billing is not _MISSING:
                        rec["billing"] = billing
                    if meeting is not _MISSING:
                        rec["meeting"] = meeting
                    break
            if updated:
                self._save_index()
        if (
            updated
            and self._auto_compact
            and status in _TERMINAL_RECORDING_STATUSES
        ):
            self.schedule_history_compaction()
        return updated

    def begin_meeting_generation(self, recording_id: str) -> dict:
        """Atomically mark a recording as generating meeting notes.

        Returns a small status dictionary so callers can avoid launching
        duplicate model requests for the same recording.
        """
        with self._lock:
            for rec in self._recordings:
                if rec["id"] != recording_id:
                    continue

                recording_status = rec.get("status") or "pending"
                meeting = rec.get("meeting")
                meeting_status = (
                    meeting.get("status")
                    if isinstance(meeting, dict)
                    else None
                )
                if meeting_status in _RUNNING_MEETING_STATUSES:
                    return {
                        "started": False,
                        "reason": "already_running",
                        "recording_status": recording_status,
                        "meeting": meeting,
                    }

                rec["error"] = None
                rec["meeting"] = {"status": "transcribing"}
                self._save_index()
                return {
                    "started": True,
                    "reason": None,
                    "recording_status": recording_status,
                    "meeting": rec["meeting"],
                }

        return {
            "started": False,
            "reason": "not_found",
            "recording_status": "pending",
            "meeting": None,
        }

    def list_recordings(self) -> list[dict]:
        """Return all recording metadata, newest first."""
        with self._lock:
            return [dict(recording) for recording in reversed(self._recordings)]

    def get_wav_base64(self, recording_id: str) -> Optional[str]:
        """Read WAV file and return base64-encoded string."""
        wav_bytes = self._read_recording_wav_bytes(recording_id)
        if wav_bytes is None:
            return None
        return base64.b64encode(wav_bytes).decode("ascii")

    def get_pcm_data(self, recording_id: str) -> Optional[bytes]:
        """Read WAV file and return raw PCM bytes."""
        wav_bytes = self._read_recording_wav_bytes(recording_id)
        if wav_bytes is None:
            return None
        try:
            with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                return wf.readframes(wf.getnframes())
        except wave.Error as exc:
            print(f"[RecordingStore] Failed to read WAV data: {exc}")
            return None

    def get_language(self, recording_id: str) -> str:
        """Get the language that was active when the recording was made."""
        with self._lock:
            for rec in self._recordings:
                if rec["id"] == recording_id:
                    return rec.get("language", "zh")
        return "zh"

    def get_recording_path(self, recording_id: str) -> Optional[Path]:
        """Return the WAV or FLAC path referenced by the recording index."""
        return self._find_wav(recording_id)

    def compact_history(
        self,
        *,
        keep_recent: int = 3,
        max_files: Optional[int] = None,
    ) -> dict:
        """Losslessly archive terminal WAV recordings outside the recent window."""
        with self._archive_lock:
            return self._compact_history(
                keep_recent=keep_recent,
                max_files=max_files,
            )

    def _compact_history(
        self,
        *,
        keep_recent: int,
        max_files: Optional[int],
    ) -> dict:
        keep_recent = max(0, int(keep_recent))
        with self._lock:
            recent_ids = (
                {
                    recording["id"]
                    for recording in self._recordings[-keep_recent:]
                }
                if keep_recent
                else set()
            )
            candidates = [
                dict(recording)
                for recording in self._recordings
                if recording.get("id") not in recent_ids
                and recording.get("status") in _TERMINAL_RECORDING_STATUSES
                and str(recording.get("filename", "")).lower().endswith(".wav")
            ]
        if max_files is not None:
            candidates = candidates[:max(0, int(max_files))]

        result = {
            "compressed_count": 0,
            "bytes_saved": 0,
            "error_count": 0,
            "skipped_count": 0,
        }
        for candidate in candidates:
            outcome = self._compact_recording(candidate)
            result[f"{outcome}_count"] += 1
            if outcome == "compressed":
                result["bytes_saved"] += int(candidate.get("_bytes_saved", 0))
        result["storage"] = self.storage_summary()
        return result

    def schedule_history_compaction(
        self,
        *,
        keep_recent: int = 3,
        max_files: Optional[int] = None,
    ) -> Optional[TaskHandle[dict]]:
        """Queue one best-effort archive sweep without blocking dictation."""
        limit_disables_work = max_files is not None and int(max_files) <= 0
        with self._lock:
            if (
                self._compaction_pending
                or limit_disables_work
                or not self._has_compaction_candidates_locked(keep_recent)
            ):
                return None
            self._compaction_pending = True
        try:
            return self._compaction_executor.submit(
                self._run_scheduled_compaction,
                keep_recent,
                max_files,
            )
        except RuntimeError:
            with self._lock:
                self._compaction_pending = False
            return None

    def _has_compaction_candidates_locked(self, keep_recent: int) -> bool:
        """Check for archive work without starting the owned worker."""
        keep_recent = max(0, int(keep_recent))
        recent_ids = (
            {
                recording["id"]
                for recording in self._recordings[-keep_recent:]
            }
            if keep_recent
            else set()
        )
        return any(
            recording.get("id") not in recent_ids
            and recording.get("status") in _TERMINAL_RECORDING_STATUSES
            and str(recording.get("filename", "")).lower().endswith(".wav")
            for recording in self._recordings
        )

    def _run_scheduled_compaction(
        self,
        keep_recent: int,
        max_files: Optional[int],
    ) -> dict:
        try:
            return self.compact_history(
                keep_recent=keep_recent,
                max_files=max_files,
            )
        finally:
            with self._lock:
                self._compaction_pending = False

    def close(self) -> None:
        """Wait for the owned archive worker and reject later scheduling."""
        self._compaction_executor.close(wait=True)
        with self._archive_lock:
            pass

    def storage_summary(self) -> dict:
        """Return aggregate file-size information without reading audio content."""
        with self._lock:
            recordings = [dict(recording) for recording in self._recordings]
        original_bytes = 0
        stored_bytes = 0
        compressed_count = 0
        for recording in recordings:
            audio_path = self._recording_path_for_filename(recording.get("filename"))
            if audio_path is None:
                continue
            try:
                current_size = audio_path.stat().st_size
            except OSError:
                continue
            stored_bytes += current_size
            if audio_path.suffix.lower() == ".flac":
                compressed_count += 1
                original = recording.get("original_bytes")
                original_bytes += (
                    int(original)
                    if isinstance(original, int) and original >= current_size
                    else current_size
                )
            else:
                original_bytes += current_size
        return {
            "recording_count": len(recordings),
            "compressed_count": compressed_count,
            "original_bytes": original_bytes,
            "stored_bytes": stored_bytes,
            "bytes_saved": max(0, original_bytes - stored_bytes),
        }

    def delete(self, recording_id: str) -> bool:
        """Delete a recording's WAV file and index entry."""
        with self._lock:
            entry = None
            for rec in self._recordings:
                if rec["id"] == recording_id:
                    entry = rec
                    break
            if entry is None:
                return False
            self._recordings.remove(entry)
            wav_path = self._recording_path_for_filename(entry.get("filename"))
            if wav_path is not None and wav_path.exists():
                try:
                    wav_path.unlink()
                except OSError:
                    pass
            self._save_index()
        return True

    def _find_wav(self, recording_id: str) -> Optional[Path]:
        with self._lock:
            for rec in self._recordings:
                if rec["id"] == recording_id:
                    return self._recording_path_for_filename(rec.get("filename"))
        return None

    def _read_wav_bytes(self, audio_path: Path) -> bytes:
        if audio_path.suffix.lower() == ".wav":
            return audio_path.read_bytes()
        if audio_path.suffix.lower() != ".flac":
            raise RuntimeError(f"unsupported audio format: {audio_path.suffix}")
        with tempfile.TemporaryDirectory(
            prefix="vocal-more-decode-",
            dir=str(self._dir),
        ) as temp_dir:
            decoded_path = Path(temp_dir) / "decoded.wav"
            self._audio_codec.decode_flac_to_wav(audio_path, decoded_path)
            return decoded_path.read_bytes()

    def _read_recording_wav_bytes(self, recording_id: str) -> Optional[bytes]:
        """Read through one concurrent WAV-to-FLAC index transition."""
        audio_path = self._find_wav(recording_id)
        if audio_path is None:
            return None
        try:
            return self._read_wav_bytes(audio_path)
        except (OSError, RuntimeError, wave.Error) as exc:
            replacement_path = self._find_wav(recording_id)
            if replacement_path is not None and replacement_path != audio_path:
                try:
                    return self._read_wav_bytes(replacement_path)
                except (OSError, RuntimeError, wave.Error) as retry_exc:
                    audio_path = replacement_path
                    exc = retry_exc
            print(f"[RecordingStore] Failed to decode {audio_path.name}: {exc}")
            return None

    @staticmethod
    def _pcm_digest(wav_path: Path) -> tuple[str, tuple]:
        with wave.open(str(wav_path), "rb") as wav_file:
            params = (
                wav_file.getnchannels(),
                wav_file.getsampwidth(),
                wav_file.getframerate(),
                wav_file.getnframes(),
            )
            digest = hashlib.sha256()
            while True:
                chunk = wav_file.readframes(65_536)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest(), params

    def _compact_recording(self, candidate: dict) -> str:
        source_path = self._recording_path_for_filename(candidate.get("filename"))
        final_path = self._recording_path_for_id(candidate.get("id"), suffix=".flac")
        if (
            source_path is None
            or source_path.suffix.lower() != ".wav"
            or final_path is None
            or source_path.stem != candidate.get("id")
        ):
            return "skipped"
        try:
            source_size = source_path.stat().st_size
            source_digest = self._pcm_digest(source_path)
            with tempfile.TemporaryDirectory(
                prefix=".vocal-more-compact-",
                dir=str(self._dir),
            ) as temp_dir:
                encoded_path = Path(temp_dir) / "recording.flac"
                decoded_path = Path(temp_dir) / "verification.wav"
                self._audio_codec.encode_wav_to_flac(source_path, encoded_path)
                encoded_size = encoded_path.stat().st_size
                if encoded_size >= source_size:
                    return "skipped"
                self._audio_codec.decode_flac_to_wav(encoded_path, decoded_path)
                if self._pcm_digest(decoded_path) != source_digest:
                    raise RuntimeError("lossless PCM verification failed")

                with self._lock:
                    current = next(
                        (
                            recording
                            for recording in self._recordings
                            if recording.get("id") == candidate.get("id")
                        ),
                        None,
                    )
                    if (
                        current is None
                        or current.get("filename") != candidate.get("filename")
                        or not source_path.exists()
                    ):
                        return "skipped"
                    os.replace(encoded_path, final_path)
                    previous = dict(current)
                    current["filename"] = final_path.name
                    current["storage_format"] = "flac"
                    current["original_bytes"] = source_size
                    current["stored_bytes"] = encoded_size
                    if not self._save_index():
                        current.clear()
                        current.update(previous)
                        try:
                            final_path.unlink()
                        except OSError:
                            pass
                        return "error"
                    try:
                        source_path.unlink()
                    except OSError as exc:
                        print(
                            f"[RecordingStore] Archived {source_path.name} "
                            f"but could not remove the WAV: {exc}"
                        )
                candidate["_bytes_saved"] = source_size - encoded_size
                print(
                    f"[RecordingStore] Archived {source_path.name} as "
                    f"{final_path.name} ({source_size - encoded_size} bytes saved)"
                )
                return "compressed"
        except (OSError, RuntimeError, wave.Error) as exc:
            print(f"[RecordingStore] Failed to archive {source_path.name}: {exc}")
            return "error"

    def _recording_path_for_id(
        self,
        recording_id: object,
        *,
        suffix: str,
    ) -> Optional[Path]:
        if not isinstance(recording_id, str):
            return None
        raw = recording_id.strip()
        if not raw or raw != recording_id or "\x00" in raw:
            return None
        candidate = Path(raw)
        if candidate.is_absolute() or candidate.name != raw or raw in {".", ".."}:
            return None
        return self._recording_path_for_filename(f"{raw}{suffix}")

    def _recording_path_for_filename(self, filename: object) -> Optional[Path]:
        if not isinstance(filename, str):
            return None
        raw = filename.strip()
        if not raw:
            return None
        candidate = Path(raw)
        # Persisted recording entries should only ever reference local basenames.
        if candidate.is_absolute() or candidate.name != raw or raw in {".", ".."}:
            return None
        return self._dir / raw

    def _enforce_limit(self) -> None:
        """Remove oldest recordings beyond MAX_RECORDINGS. Must hold lock."""
        while len(self._recordings) > MAX_RECORDINGS:
            oldest = self._recordings.pop(0)
            wav_path = self._recording_path_for_filename(oldest.get("filename"))
            if wav_path is not None and wav_path.exists():
                try:
                    wav_path.unlink()
                except OSError:
                    pass
