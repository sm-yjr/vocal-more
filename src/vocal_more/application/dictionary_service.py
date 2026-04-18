"""Application service for dictionary CRUD and normalization."""

from __future__ import annotations

from ..domain.dictionary_models import (
    DictEntry,
    build_asr_corpus_text,
    format_entries_for_prompt,
    normalize_aliases,
    normalize_term,
    normalize_text_entries,
)


class DictionaryService:
    """Own dictionary behavior while delegating persistence to a repository."""

    def __init__(self, repository) -> None:
        self._repository = repository
        self.entries: list[DictEntry] = self._repository.load()

    @classmethod
    def from_dir(cls, base_dir):
        from ..infrastructure.dictionary_repository import DictionaryRepository

        return cls(DictionaryRepository(base_dir=base_dir))

    def load(self) -> list[DictEntry]:
        self.entries = self._repository.load()
        return self.entries

    def save(self) -> None:
        self._repository.save(self.entries)

    def replace_entries(self, entries: list[DictEntry]) -> None:
        self.entries = list(entries)
        self.save()

    def add_entry(self, term: str, aliases: object = None) -> None:
        term = normalize_term(term)
        if not term:
            return
        normalized_aliases = normalize_aliases(aliases or [])
        for entry in self.entries:
            if entry.term == term:
                if normalized_aliases:
                    entry.aliases = [
                        *entry.aliases,
                        *[
                            alias
                            for alias in normalized_aliases
                            if alias not in entry.aliases and alias != term
                        ],
                    ]
                self.save()
                return
        self.entries.append(DictEntry(term=term, aliases=normalized_aliases))
        self.save()

    def remove_entry(self, term: str) -> None:
        self.entries = [entry for entry in self.entries if entry.term != term]
        self.save()

    def format_for_prompt(self) -> str:
        return format_entries_for_prompt(self.entries)

    def build_asr_corpus_text(self, extra_terms: list[str]) -> str:
        return build_asr_corpus_text(self.entries, extra_terms)

    def normalize_terms(self, text: str) -> str:
        return normalize_text_entries(text, self.entries)
