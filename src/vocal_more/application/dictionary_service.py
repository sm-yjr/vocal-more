"""Application service for dictionary CRUD and normalization."""

from __future__ import annotations

import threading

from ..domain.dictionary_models import (
    DictEntry,
    DictionaryMutation,
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
        self._lock = threading.RLock()
        self.entries: list[DictEntry] = self._repository.load()

    @classmethod
    def from_dir(cls, base_dir):
        from ..infrastructure.dictionary_repository import DictionaryRepository

        return cls(DictionaryRepository(base_dir=base_dir))

    def load(self) -> list[DictEntry]:
        with self._lock:
            self.entries = self._repository.load()
            return self.snapshot_entries()

    def save(self) -> None:
        with self._lock:
            self._repository.save(self.entries)

    def replace_entries(self, entries: list[DictEntry]) -> None:
        with self._lock:
            self.entries = list(entries)
            self.save()

    def add_entry(self, term: str, aliases: object = None) -> None:
        self.add_entry_with_result(term, aliases)

    def add_entry_with_result(
        self,
        term: str,
        aliases: object = None,
        *,
        before_apply=None,
    ) -> DictionaryMutation:
        with self._lock:
            term = normalize_term(term)
            if not term:
                return DictionaryMutation(term="", term_created=False)
            normalized_aliases = normalize_aliases(aliases or [])
            for entry in self.entries:
                if entry.term == term:
                    aliases_added = [
                        alias
                        for alias in normalized_aliases
                        if alias not in entry.aliases and alias != term
                    ]
                    if aliases_added:
                        mutation = DictionaryMutation(
                            term=term,
                            term_created=False,
                            aliases_added=aliases_added,
                        )
                        if before_apply is not None:
                            before_apply(mutation)
                        entry.aliases = [*entry.aliases, *aliases_added]
                    else:
                        mutation = DictionaryMutation(
                            term=term,
                            term_created=False,
                            aliases_added=[],
                        )
                        if before_apply is not None:
                            before_apply(mutation)
                    self.save()
                    return mutation
            mutation = DictionaryMutation(
                term=term,
                term_created=True,
                aliases_added=list(normalized_aliases),
            )
            if before_apply is not None:
                before_apply(mutation)
            self.entries.append(DictEntry(term=term, aliases=normalized_aliases))
            self.save()
            return mutation

    def undo_mutation(self, mutation: DictionaryMutation) -> bool:
        """Remove only aliases introduced by one automatic-learning mutation."""
        with self._lock:
            for entry in list(self.entries):
                if entry.term != mutation.term:
                    continue
                aliases_to_remove = set(mutation.aliases_added)
                entry.aliases = [
                    alias for alias in entry.aliases if alias not in aliases_to_remove
                ]
                if mutation.term_created and not entry.aliases:
                    self.entries.remove(entry)
                self.save()
                return True
            return False

    def remove_entry(self, term: str) -> None:
        with self._lock:
            self.entries = [entry for entry in self.entries if entry.term != term]
            self.save()

    def format_for_prompt(self) -> str:
        return format_entries_for_prompt(self.snapshot_entries())

    def build_asr_corpus_text(self, extra_terms: list[str]) -> str:
        return build_asr_corpus_text(self.snapshot_entries(), extra_terms)

    def normalize_terms(self, text: str) -> str:
        return normalize_text_entries(text, self.snapshot_entries())

    def snapshot_entries(self) -> list[DictEntry]:
        with self._lock:
            return [
                DictEntry(term=entry.term, aliases=list(entry.aliases))
                for entry in self.entries
            ]
