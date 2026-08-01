"""Compatibility repair helpers for persisted YAML files."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from ..domain.config_models import AppConfig
from ..domain.dictionary_models import normalize_dictionary_data
from ..yaml_compat import safe_load_compat


@dataclass
class CompatibilityRepairResult:
    """Summary of a compatibility repair pass."""

    target: str
    path: Path
    changed: bool = False
    details: list[str] = field(default_factory=list)
    error: str = ""
    backup_path: str = ""


def backup_yaml_file(path: Path, reason: str) -> Path | None:
    """Preserve the original YAML file before rewriting or fallback."""
    if not path.exists():
        return None

    backup_path = path.with_name(f"{path.name}.{reason}.bak")
    if backup_path.exists():
        return backup_path

    shutil.copy2(path, backup_path)
    return backup_path


def _write_yaml_data(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(
            data,
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )


def _repair_yaml_file(
    *,
    target: str,
    path: Path,
    normalize_data: Callable[[Any], dict[str, Any]],
) -> CompatibilityRepairResult:
    result = CompatibilityRepairResult(target=target, path=path)
    if not path.exists():
        return result

    try:
        with open(path, encoding="utf-8") as f:
            raw_data = safe_load_compat(f) or {}
    except Exception as exc:
        backup_path = backup_yaml_file(path, f"{target}-load-failed")
        if backup_path is not None:
            result.backup_path = str(backup_path)
            result.details.append("backup_created")
        result.error = str(exc)
        return result

    try:
        normalized_data = normalize_data(raw_data)
    except Exception as exc:
        backup_path = backup_yaml_file(path, f"{target}-normalize-failed")
        if backup_path is not None:
            result.backup_path = str(backup_path)
            result.details.append("backup_created")
        result.error = str(exc)
        return result

    if raw_data == normalized_data:
        return result

    backup_path = backup_yaml_file(path, f"{target}-pre-repair")
    if backup_path is not None:
        result.backup_path = str(backup_path)
        result.details.append("backup_created")

    try:
        _write_yaml_data(path, normalized_data)
    except Exception as exc:
        # Loading can still normalize the original data in memory. A read-only
        # config should not prevent the app from starting merely because a new
        # compatibility field could not be persisted yet.
        result.error = str(exc)
        result.details.append("normalized_write_failed")
        return result
    result.changed = True
    result.details.append("normalized")
    return result


def repair_config_file(path: Path, config_cls: type[AppConfig] = AppConfig) -> CompatibilityRepairResult:
    """Normalize the persisted config file in place."""
    return _repair_yaml_file(
        target="config",
        path=path,
        normalize_data=lambda raw_data: config_cls._from_dict(raw_data).to_dict(),
    )


def repair_dictionary_file(path: Path) -> CompatibilityRepairResult:
    """Normalize the persisted dictionary file in place."""
    return _repair_yaml_file(
        target="dictionary",
        path=path,
        normalize_data=normalize_dictionary_data,
    )
