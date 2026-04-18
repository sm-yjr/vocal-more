"""Tests for the serial dictation command coordinator."""

import threading

import pytest

from vocal_more.application.dictation_command_coordinator import (
    DictationCommandCoordinator,
)


def test_submit_executes_commands_in_order():
    coordinator = DictationCommandCoordinator(thread_name="test-dictation-order")
    received = []
    done = threading.Event()

    coordinator.submit(lambda: received.append("first"))

    def _second():
        received.append("second")
        done.set()

    coordinator.submit(_second)

    assert done.wait(timeout=1.0)
    assert received == ["first", "second"]

    coordinator.close()


def test_call_returns_results_and_propagates_errors():
    coordinator = DictationCommandCoordinator(thread_name="test-dictation-call")

    assert coordinator.call(lambda: 42) == 42

    with pytest.raises(ValueError, match="boom"):
        coordinator.call(lambda: (_ for _ in ()).throw(ValueError("boom")))

    coordinator.close()


def test_close_rejects_new_commands():
    coordinator = DictationCommandCoordinator(thread_name="test-dictation-close")
    coordinator.close()

    with pytest.raises(RuntimeError, match="closed"):
        coordinator.submit(lambda: None)
