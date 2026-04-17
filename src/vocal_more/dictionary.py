"""Dictionary management compatibility facade for Vocal-More."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .application.dictionary_service import DictionaryService
from .config import Config, get_config
from .domain.dictionary_models import (
    DictEntry,
    iter_alias_values as _iter_alias_values,
    merge_aliases as _merge_aliases,
    normalize_aliases as _normalize_aliases,
    normalize_term as _normalize_term,
)
from .infrastructure.dictionary_repository import DictionaryRepository


class Dictionary(DictionaryService):
    """Backward-compatible dictionary object backed by the new service/repository split."""

    def __init__(self) -> None:
        super().__init__(
            DictionaryRepository(
                base_dir=Config.get_config_dir(),
                dictionary_path=self.get_path(),
            )
        )

    @staticmethod
    def get_path() -> Path:
        return Config.get_config_dir() / "dictionary.yaml"

    def build_asr_corpus_text(self) -> str:
        config = get_config()
        if not config.asr.use_dictionary_corpus:
            return ""
        return super().build_asr_corpus_text(config.asr.extra_corpus_terms)


_dictionary: Optional[Dictionary] = None


def get_dictionary() -> Dictionary:
    global _dictionary
    if _dictionary is None:
        _dictionary = Dictionary()
    return _dictionary


def reload_dictionary() -> Dictionary:
    global _dictionary
    _dictionary = Dictionary()
    return _dictionary


def build_asr_corpus_text() -> str:
    return get_dictionary().build_asr_corpus_text()


def normalize_terms(text: str) -> str:
    return get_dictionary().normalize_terms(text)


__all__ = [
    "DictEntry",
    "Dictionary",
    "_iter_alias_values",
    "_merge_aliases",
    "_normalize_aliases",
    "_normalize_term",
    "build_asr_corpus_text",
    "get_dictionary",
    "normalize_terms",
    "reload_dictionary",
]
