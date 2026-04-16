"""Compatibility checks and repairs for persisted user data."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml


@dataclass
class CompatibilityRepairResult:
    """Summary of a compatibility repair pass."""

    target: str
    path: Path
    changed: bool = False
    details: list[str] = field(default_factory=list)
    error: str = ""


def _repair_yaml_file(
    *,
    target: str,
    path: Path,
    normalize_data: Callable[[Any], dict[str, Any]],
    write_data: Callable[[dict[str, Any]], None],
) -> CompatibilityRepairResult:
    result = CompatibilityRepairResult(target=target, path=path)
    if not path.exists():
        return result

    try:
        with open(path, encoding="utf-8") as f:
            raw_data = yaml.safe_load(f) or {}
    except Exception as exc:
        result.error = str(exc)
        print(f"[Compatibility] Failed to load {target} file {path}: {exc}")
        return result

    normalized_data = normalize_data(raw_data)
    if raw_data == normalized_data:
        return result

    try:
        write_data(normalized_data)
    except Exception as exc:
        result.error = str(exc)
        print(f"[Compatibility] Failed to rewrite {target} file {path}: {exc}")
        return result

    result.changed = True
    result.details.append("normalized")
    print(f"[Compatibility] Repaired {target} file {path}")
    return result


def repair_config_file() -> CompatibilityRepairResult:
    """Normalize the persisted config file in place."""
    from .config import Config

    return _repair_yaml_file(
        target="config",
        path=Config.get_config_path(),
        normalize_data=lambda raw_data: Config._from_dict(raw_data).to_dict(),
        write_data=Config._write_config_data,
    )


def repair_dictionary_file() -> CompatibilityRepairResult:
    """Normalize the persisted dictionary file in place."""
    from .config import Config
    from .dictionary import _iter_alias_values, _merge_aliases, _normalize_term

    def normalize_dictionary(raw_data: Any) -> dict[str, Any]:
        entries: list[dict[str, Any]] = []
        if isinstance(raw_data, dict):
            raw_entries = raw_data.get("entries", [])
        else:
            raw_entries = []

        if isinstance(raw_entries, list):
            for raw_entry in raw_entries:
                if not isinstance(raw_entry, dict):
                    continue
                term = _normalize_term(raw_entry.get("term"))
                if not term:
                    continue
                aliases = _merge_aliases(
                    [],
                    list(_iter_alias_values(raw_entry.get("aliases", []))),
                    term,
                )
                entry: dict[str, Any] = {"term": term}
                if aliases:
                    entry["aliases"] = aliases
                entries.append(entry)

        return {"entries": entries}

    def write_dictionary(data: dict[str, Any]) -> None:
        path = Config.get_config_dir() / "dictionary.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(
                data,
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )

    return _repair_yaml_file(
        target="dictionary",
        path=Config.get_config_dir() / "dictionary.yaml",
        normalize_data=normalize_dictionary,
        write_data=write_dictionary,
    )


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
