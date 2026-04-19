"""Diagnostics export helpers for support and issue reporting."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
import os
from pathlib import Path
from typing import Iterable, Optional
import zipfile

from .config import Config
from .environment_check import EnvironmentCheckResult

DEBUG_TRACE_LIMIT = 3


def default_debug_dir() -> Path:
    return Config.get_config_dir() / "debug"


def ensure_runtime_debug_dir_env() -> Path:
    """Enable persistent debug traces for the app unless the user overrode it."""
    debug_dir = os.environ.get("VOCAL_MORE_DEBUG_DIR", "").strip()
    if debug_dir:
        path = Path(os.path.expanduser(debug_dir))
    else:
        path = default_debug_dir()
        os.environ["VOCAL_MORE_DEBUG_DIR"] = str(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def redact_api_key(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}***{text[-4:]}"


def _redacted_config_snapshot(config: Config) -> dict:
    snapshot = config.to_dict()
    snapshot["api_key"] = redact_api_key(snapshot.get("api_key"))
    return snapshot


def _latest_trace_paths(debug_dir: Path, limit: int = DEBUG_TRACE_LIMIT) -> list[Path]:
    if not debug_dir.exists():
        return []
    return sorted(debug_dir.glob("*.json"), reverse=True)[:limit]


def _manifest_path_hint(
    path: Path,
    *,
    base_dir: Path,
    external_label: str,
    is_dir: bool = False,
) -> str:
    normalized_path = Path(os.path.expanduser(str(path)))
    normalized_base_dir = Path(os.path.expanduser(str(base_dir)))
    try:
        relative = normalized_path.relative_to(normalized_base_dir)
    except ValueError:
        return external_label

    label = relative.as_posix()
    if is_dir:
        return f"{label.rstrip('/')}/" if label and label != "." else "./"
    return label or "."


def _sanitize_environment_check_details(
    details: str,
    *,
    config_dir: Path,
    config_path: Path,
) -> str:
    text = str(details or "")
    if not text:
        return ""

    replacements = {
        str(config_path): "config.yaml",
        str(Path(os.path.expanduser(str(config_path)))): "config.yaml",
        str(config_dir): "<config-dir>",
        str(Path(os.path.expanduser(str(config_dir)))): "<config-dir>",
    }
    for source, replacement in replacements.items():
        if source:
            text = text.replace(source, replacement)
    return text


def _sanitized_environment_checks(
    environment_checks: Iterable[EnvironmentCheckResult],
    *,
    config_dir: Path,
    config_path: Path,
) -> list[dict]:
    checks = []
    for check in environment_checks:
        payload = asdict(check)
        payload["details"] = _sanitize_environment_check_details(
            payload.get("details", ""),
            config_dir=config_dir,
            config_path=config_path,
        )
        checks.append(payload)
    return checks


def _recording_payload(recording_store) -> tuple[Optional[dict], Optional[Path]]:
    recordings = recording_store.list_recordings()
    if not recordings:
        return None, None
    selected = next((rec for rec in recordings if rec.get("status") == "failed"), recordings[0])
    wav_path = recording_store.get_recording_path(selected["id"])
    return selected, wav_path


def export_support_bundle(
    *,
    config: Config,
    recording_store,
    environment_checks: Iterable[EnvironmentCheckResult],
    app_version: str,
) -> Path:
    """Write a support bundle zip and return its path."""
    support_dir = Config.get_config_dir() / "support"
    support_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = support_dir / (
        f"vocal-more-diagnostics-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    )

    debug_dir_raw = os.environ.get("VOCAL_MORE_DEBUG_DIR", "").strip()
    debug_dir = Path(os.path.expanduser(debug_dir_raw)) if debug_dir_raw else default_debug_dir()
    trace_paths = _latest_trace_paths(debug_dir)
    recording_meta, recording_path = _recording_payload(recording_store)
    config_dir = Config.get_config_dir()
    config_path = Config.get_config_path()
    dictionary_path = config_dir / "dictionary.yaml"

    manifest = {
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "app_version": app_version,
        "config_path": _manifest_path_hint(
            config_path,
            base_dir=config_dir,
            external_label="<external-config-path>",
        ),
        "dictionary_path": _manifest_path_hint(
            dictionary_path,
            base_dir=config_dir,
            external_label="<external-dictionary-path>",
        ),
        "debug_dir": _manifest_path_hint(
            debug_dir,
            base_dir=config_dir,
            external_label="<external-debug-dir>",
            is_dir=True,
        ),
        "trace_files": [path.name for path in trace_paths],
        "recording_id": recording_meta.get("id") if recording_meta else None,
        "environment_checks": _sanitized_environment_checks(
            environment_checks,
            config_dir=config_dir,
            config_path=config_path,
        ),
    }

    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )
        bundle.writestr(
            "config.snapshot.json",
            json.dumps(_redacted_config_snapshot(config), ensure_ascii=False, indent=2),
        )

        if dictionary_path.exists():
            bundle.write(dictionary_path, arcname="dictionary.yaml")
        if recording_meta is not None:
            bundle.writestr(
                "selected_recording.json",
                json.dumps(recording_meta, ensure_ascii=False, indent=2),
            )
        if recording_path is not None and recording_path.exists():
            bundle.write(recording_path, arcname=f"recordings/{recording_path.name}")

        for trace_path in trace_paths:
            bundle.write(trace_path, arcname=f"debug/{trace_path.name}")
            wav_path = trace_path.with_suffix(".wav")
            if wav_path.exists():
                bundle.write(wav_path, arcname=f"debug/{wav_path.name}")

    return bundle_path
