"""Recording store: save, index, and manage recent audio recordings."""

import base64
import json
import threading
import wave
from datetime import datetime
from pathlib import Path
from typing import Optional


SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2
MAX_RECORDINGS = 10
RETRY_ASR_MODEL = "qwen3.5-omni-plus"
_MISSING = object()


class RecordingStore:
    """Persist recent recordings as WAV files with a JSON index."""

    def __init__(self, recordings_dir: Optional[str] = None):
        if recordings_dir is None:
            self._dir = Path.home() / ".vocal-more" / "recordings"
        else:
            self._dir = Path(recordings_dir).expanduser()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._dir / "recordings.json"
        self._lock = threading.Lock()
        self._recordings: list[dict] = self._load_index()
        self._prune_orphans()
        self._last_id: Optional[str] = None
        self._id_counter = 0

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
                    entry.setdefault("transcript", None)
                    entry.setdefault("error", None)
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
            if (self._dir / r["filename"]).exists()
        ]
        pruned = before - len(self._recordings)
        if pruned:
            print(f"[RecordingStore] Pruned {pruned} orphaned index entries")
            self._save_index()

    def _save_index(self) -> None:
        try:
            self._index_path.write_text(
                json.dumps(self._recordings, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            print(f"[RecordingStore] Failed to write index: {e}")

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
    ) -> None:
        """Update status and transcript for a recording."""
        with self._lock:
            for rec in self._recordings:
                if rec["id"] == recording_id:
                    rec["status"] = status
                    if transcript is not _MISSING:
                        rec["transcript"] = transcript
                    if error is not _MISSING:
                        rec["error"] = error
                    elif status == "success":
                        rec["error"] = None
                    break
            self._save_index()

    def list_recordings(self) -> list[dict]:
        """Return all recording metadata, newest first."""
        with self._lock:
            return list(reversed(self._recordings))

    def get_wav_base64(self, recording_id: str) -> Optional[str]:
        """Read WAV file and return base64-encoded string."""
        wav_path = self._find_wav(recording_id)
        if wav_path is None or not wav_path.exists():
            return None
        return base64.b64encode(wav_path.read_bytes()).decode("ascii")

    def get_pcm_data(self, recording_id: str) -> Optional[bytes]:
        """Read WAV file and return raw PCM bytes."""
        wav_path = self._find_wav(recording_id)
        if wav_path is None or not wav_path.exists():
            return None
        with wave.open(str(wav_path), "rb") as wf:
            return wf.readframes(wf.getnframes())

    def get_language(self, recording_id: str) -> str:
        """Get the language that was active when the recording was made."""
        with self._lock:
            for rec in self._recordings:
                if rec["id"] == recording_id:
                    return rec.get("language", "zh")
        return "zh"

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
            wav_path = self._dir / entry["filename"]
            if wav_path.exists():
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
                    return self._dir / rec["filename"]
        return None

    def _enforce_limit(self) -> None:
        """Remove oldest recordings beyond MAX_RECORDINGS. Must hold lock."""
        while len(self._recordings) > MAX_RECORDINGS:
            oldest = self._recordings.pop(0)
            wav_path = self._dir / oldest["filename"]
            if wav_path.exists():
                try:
                    wav_path.unlink()
                except OSError:
                    pass
