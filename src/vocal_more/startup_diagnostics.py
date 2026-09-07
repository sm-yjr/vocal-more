"""Bounded, privacy-conscious telemetry for dictation startup failures."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Config

STARTUP_EVENT_FILENAME = "startup-events.jsonl"
STARTUP_EVENT_MAX_BYTES = 512 * 1024
_LOCK = threading.Lock()
_SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "access_token",
    "refresh_token",
    "secret",
    "transcript",
    "text",
    "audio_data",
    "context_instruction",
}


def startup_event_path() -> Path:
    override = os.environ.get("VOCAL_MORE_STARTUP_DIAGNOSTICS_PATH", "").strip()
    if override:
        return Path(os.path.expanduser(override))
    return Config.get_config_dir() / "diagnostics" / STARTUP_EVENT_FILENAME


def startup_event_paths() -> list[Path]:
    if "PYTEST_CURRENT_TEST" in os.environ and not os.environ.get(
        "VOCAL_MORE_STARTUP_DIAGNOSTICS_PATH"
    ):
        return []
    path = startup_event_path()
    return [
        candidate
        for candidate in (path, path.with_suffix(".1.jsonl"))
        if candidate.exists()
    ]


def new_startup_attempt_id() -> str:
    return (
        f"{datetime.now().astimezone().strftime('%Y%m%dT%H%M%S')}"
        f"-{uuid.uuid4().hex[:8]}"
    )


def _safe_string(value: object, *, limit: int = 600) -> str:
    text = str(value)
    home = str(Path.home())
    if home:
        text = text.replace(home, "<home>")
    text = re.sub(
        r"(?i)(api[_-]?key|authorization|bearer)\s*[:= ]\s*\S+", r"\1=<redacted>", text
    )
    return text[:limit]


def sanitize_diagnostic_value(value: Any, *, key: str = "", depth: int = 0) -> Any:
    """Return a bounded JSON-safe value without user content or credentials."""
    if key.lower() in _SENSITIVE_KEYS:
        return "<redacted>"
    if depth > 5:
        return "<truncated>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _safe_string(value)
    if isinstance(value, dict):
        return {
            _safe_string(k, limit=80): sanitize_diagnostic_value(
                v, key=str(k), depth=depth + 1
            )
            for k, v in list(value.items())[:80]
        }
    if isinstance(value, (list, tuple)):
        return [
            sanitize_diagnostic_value(item, key=key, depth=depth + 1)
            for item in value[:80]
        ]
    return _safe_string(value)


def exception_fields(exc: BaseException) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "error_type": type(exc).__name__,
        "error_message": _safe_string(exc),
    }
    for name in (
        "code",
        "stage",
        "recoverable",
        "startup_timed_out",
        "device_change_detected",
    ):
        if hasattr(exc, name):
            fields[name] = sanitize_diagnostic_value(getattr(exc, name), key=name)
    return fields


def record_startup_event(
    event: str,
    *,
    attempt_id: str | None = None,
    **fields: Any,
) -> None:
    """Append one event. Failures here must never affect dictation."""
    if "PYTEST_CURRENT_TEST" in os.environ and not os.environ.get(
        "VOCAL_MORE_STARTUP_DIAGNOSTICS_PATH"
    ):
        return
    payload = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "monotonic_ns": time.monotonic_ns(),
        "pid": os.getpid(),
        "thread": threading.current_thread().name,
        "event": event,
        "attempt_id": attempt_id,
    }
    payload.update(fields)
    line = json.dumps(
        sanitize_diagnostic_value(payload), ensure_ascii=False, separators=(",", ":")
    )
    path = startup_event_path()
    try:
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            if (
                path.exists()
                and path.stat().st_size + len(line.encode("utf-8"))
                > STARTUP_EVENT_MAX_BYTES
            ):
                rotated = path.with_suffix(".1.jsonl")
                os.replace(path, rotated)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
    except Exception:  # noqa: BLE001 - diagnostics must never break dictation
        return
