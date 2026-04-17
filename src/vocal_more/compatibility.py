"""Compatibility checks and repairs for persisted user data."""

from __future__ import annotations

from typing import Callable

from .config import Config
from .infrastructure.compatibility_repair import (
    CompatibilityRepairResult,
    backup_yaml_file,
    repair_config_file as _repair_config_file,
    repair_dictionary_file as _repair_dictionary_file,
)


def _backup_yaml_file(path, reason):
    """Backward-compatible alias for config recovery code."""
    return backup_yaml_file(path, reason)


def repair_config_file() -> CompatibilityRepairResult:
    """Normalize the persisted config file in place."""
    return _repair_config_file(Config.get_config_path(), config_cls=Config)


def repair_dictionary_file() -> CompatibilityRepairResult:
    """Normalize the persisted dictionary file in place."""
    return _repair_dictionary_file(Config.get_config_dir() / "dictionary.yaml")


COMPATIBILITY_REPAIR_TOOLS: dict[str, Callable[[], CompatibilityRepairResult]] = {
    "config": repair_config_file,
    "dictionary": repair_dictionary_file,
}


def run_compatibility_check_and_repair(*targets: str) -> list[CompatibilityRepairResult]:
    """Run one or more compatibility repair tools."""
    selected_targets = targets or tuple(COMPATIBILITY_REPAIR_TOOLS.keys())
    results: list[CompatibilityRepairResult] = []
    for target in selected_targets:
        tool = COMPATIBILITY_REPAIR_TOOLS.get(target)
        if tool is None:
            raise ValueError(f"Unknown compatibility repair target: {target}")
        results.append(tool())
    return results
