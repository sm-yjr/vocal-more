"""Composition root for the automatic dictionary-learning runtime."""

from __future__ import annotations

import platform
import threading

from .application.dictionary_edit_observer import DictionaryEditObserver
from .application.dictionary_learning_runtime import (
    AutomaticDictionaryLearningCoordinator,
    DictionaryLearningQueueWorker,
)
from .application.dictionary_learning_service import DictionaryLearningProcessor
from .config import get_config
from .core.accessibility_text import MacOSFocusedTextProvider
from .core.dictionary_learning_model import DictionaryLearningModelClient
from .dictionary import get_dictionary
from .infrastructure.dictionary_learning_repository import (
    DictionaryLearningRepository,
)


class _CurrentAPIKeyModelClient:
    """Reuse a client until the user changes their configured API key."""

    def __init__(self, config) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._api_key = ""
        self._client = None

    def classify(self, evidence):
        api_key = str(self._config.api_key or "")
        with self._lock:
            if self._client is None or api_key != self._api_key:
                self._client = DictionaryLearningModelClient(api_key=api_key)
                self._api_key = api_key
            client = self._client
        return client.classify(evidence)


class _UnavailableFocusedTextProvider:
    """No-op provider for platforms without an accessibility text adapter."""

    @staticmethod
    def capture_focused():
        return None

    @staticmethod
    def capture_target(_snapshot):
        return None


def _focused_text_provider():
    if platform.system() == "Darwin":
        return MacOSFocusedTextProvider()
    return _UnavailableFocusedTextProvider()


def build_dictionary_learning_runtime(
    *,
    config=None,
) -> AutomaticDictionaryLearningCoordinator:
    config = config or get_config()
    config_dir = getattr(config, "get_config_dir", None)
    repository = DictionaryLearningRepository(
        base_dir=config_dir() if callable(config_dir) else None
    )
    processor = DictionaryLearningProcessor(
        repository=repository,
        dictionary=get_dictionary(),
        model_client=_CurrentAPIKeyModelClient(config),
    )
    queue_worker = DictionaryLearningQueueWorker(
        config=config,
        processor=processor,
        repository=repository,
    )
    return AutomaticDictionaryLearningCoordinator(
        config=config,
        observer_factory=lambda **kwargs: DictionaryEditObserver(
            provider=_focused_text_provider(),
            **kwargs,
        ),
        repository=repository,
        queue_worker=queue_worker,
        processor=processor,
    )


__all__ = ["build_dictionary_learning_runtime"]
