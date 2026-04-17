"""YAML-backed repository for persisted dictionary entries."""

from __future__ import annotations

from pathlib import Path

import yaml

from ..domain.dictionary_models import DictEntry, normalize_dictionary_data, normalize_entries, serialize_entries
from ..paths import default_app_paths
from ..yaml_compat import safe_load_compat
from .compatibility_repair import repair_dictionary_file


class DictionaryRepository:
    """Load and save dictionary entries from YAML."""

    def __init__(
        self,
        *,
        base_dir: Path | None = None,
        dictionary_path: Path | None = None,
    ) -> None:
        self.paths = default_app_paths(base_dir)
        self.dictionary_path = (
            Path(dictionary_path) if dictionary_path is not None else self.paths.dictionary_path
        )

    def load(self) -> list[DictEntry]:
        if not self.dictionary_path.exists():
            return []

        repair_dictionary_file(self.dictionary_path)
        with open(self.dictionary_path, encoding="utf-8") as f:
            data = safe_load_compat(f) or {}
        return normalize_entries(data.get("entries", []))

    def save(self, entries: list[DictEntry]) -> None:
        self.dictionary_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.dictionary_path, "w", encoding="utf-8") as f:
            yaml.dump(
                serialize_entries(entries),
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
