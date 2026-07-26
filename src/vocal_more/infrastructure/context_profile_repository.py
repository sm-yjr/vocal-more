"""Atomic persistence for aggregate context-category usage counts."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading

from ..domain.app_context import AppContext, CONTEXT_CATEGORIES


class ContextProfileRepository:
    """Persist category counters while never writing app or text identity."""

    SCHEMA_VERSION = 1

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    @staticmethod
    def _empty_counts() -> dict[str, int]:
        return {category: 0 for category in CONTEXT_CATEGORIES}

    def _load_counts(self) -> dict[str, int]:
        counts = self._empty_counts()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return counts
        raw_counts = payload.get("category_counts") if isinstance(payload, dict) else None
        if not isinstance(raw_counts, dict):
            return counts
        for category in CONTEXT_CATEGORIES:
            value = raw_counts.get(category, 0)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                counts[category] = value
        return counts

    def _save_counts(self, counts: dict[str, int]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "category_counts": {
                category: int(max(0, counts.get(category, 0)))
                for category in CONTEXT_CATEGORIES
            },
        }
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    payload,
                    handle,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    def increment(self, context: AppContext) -> None:
        """Increment only the coarse category; app identity is intentionally ignored."""
        if context.category not in CONTEXT_CATEGORIES:
            return
        with self._lock:
            counts = self._load_counts()
            counts[context.category] += 1
            self._save_counts(counts)

    def summary(self) -> dict:
        with self._lock:
            counts = self._load_counts()
        return {"counts": counts, "total": sum(counts.values())}

    def reset(self) -> None:
        with self._lock:
            self._save_counts(self._empty_counts())


__all__ = ["ContextProfileRepository"]
