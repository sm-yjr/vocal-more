from __future__ import annotations

import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock


def _evidence(**overrides):
    from vocal_more.domain.dictionary_learning_models import DictionaryLearningEvidence

    values = {
        "raw_text": "今天用阿里云白练测试。",
        "pasted_text": "今天用阿里云白练测试。",
        "original_text": "",
        "baseline_text": "今天用阿里云白练测试。",
        "edited_text": "今天用阿里云百炼测试。",
        "app_bundle_id": "com.apple.Notes",
        "app_name": "Notes",
        "mode_name": "walkie_talkie",
        "recording_id": "recording-1",
    }
    values.update(overrides)
    return DictionaryLearningEvidence(**values)


def _decision(**overrides):
    from vocal_more.domain.dictionary_learning_models import DictionaryLearningDecision

    values = {
        "decision": "add",
        "term": "阿里云百炼",
        "aliases": ["阿里云白练"],
        "confidence": 0.98,
        "reason_code": "proper_noun_correction",
    }
    values.update(overrides)
    return DictionaryLearningDecision(**values)


def test_model_client_uses_user_key_and_fixed_json_mode_parameters():
    from vocal_more.core.dictionary_learning_model import DictionaryLearningModelClient

    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=json.dumps(
                        {
                            "decision": "add",
                            "term": "阿里云百炼",
                            "aliases": ["阿里云白练"],
                            "confidence": 0.98,
                            "reason_code": "proper_noun_correction",
                        },
                        ensure_ascii=False,
                    )
                )
            )
        ]
    )
    create = MagicMock(return_value=response)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    client_factory = MagicMock(return_value=client)

    model_client = DictionaryLearningModelClient(
        api_key="user-key",
        client_factory=client_factory,
    )
    result = model_client.classify(_evidence())

    client_factory.assert_called_once_with(
        api_key="user-key",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    kwargs = create.call_args.kwargs
    assert kwargs["model"] == "qwen3.7-plus"
    assert kwargs["temperature"] == 0
    assert kwargs["max_tokens"] == 256
    assert kwargs["stream"] is False
    assert kwargs["timeout"] == 30.0
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["extra_body"] == {"enable_thinking": False}
    assert "多个独立的词典纠正" in kwargs["messages"][0]["content"]
    assert "只选择置信度最高" in kwargs["messages"][0]["content"]
    assert result == _decision()


def test_model_client_rejects_non_json_and_missing_api_key():
    import pytest

    from vocal_more.core.dictionary_learning_model import (
        DictionaryLearningModelClient,
        DictionaryLearningResponseError,
    )

    with pytest.raises(ValueError, match="API key"):
        DictionaryLearningModelClient(api_key="")

    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=MagicMock(
                    return_value=SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                message=SimpleNamespace(content="not json")
                            )
                        ]
                    )
                )
            )
        )
    )
    model_client = DictionaryLearningModelClient(
        api_key="user-key",
        client_factory=lambda **_: client,
    )

    with pytest.raises(DictionaryLearningResponseError):
        model_client.classify(_evidence())


def test_decision_validation_requires_evidence_and_rejects_content_changes():
    from vocal_more.domain.dictionary_learning_models import validate_decision

    assert validate_decision(_decision(), _evidence()).action == "add"

    invalid_alias = validate_decision(
        _decision(aliases=["不存在的错误形式"]),
        _evidence(),
    )
    assert invalid_alias.action == "ignore"
    assert invalid_alias.reason_code == "alias_not_in_pasted_text"

    number_change = validate_decision(
        _decision(term="四点", aliases=["三点"], confidence=0.99),
        _evidence(
            raw_text="明天下午三点开会。",
            pasted_text="明天下午三点开会。",
            baseline_text="明天下午三点开会。",
            edited_text="明天下午四点开会。",
        ),
    )
    assert number_change.action == "ignore"
    assert number_change.reason_code == "numeric_or_date_change"


def test_decision_validation_routes_medium_confidence_to_review():
    from vocal_more.domain.dictionary_learning_models import validate_decision

    result = validate_decision(_decision(confidence=0.82), _evidence())

    assert result.action == "review"
    assert result.term == "阿里云百炼"
    assert result.aliases == ["阿里云白练"]


def test_sqlite_repository_persists_and_claims_due_jobs(tmp_path):
    from vocal_more.infrastructure.dictionary_learning_repository import (
        DictionaryLearningRepository,
    )

    path = tmp_path / "dictionary-learning.sqlite3"
    repository = DictionaryLearningRepository(database_path=path)
    queued = repository.enqueue(_evidence(), now=100.0)

    reopened = DictionaryLearningRepository(database_path=path)
    claimed = reopened.claim_next(now=100.0)

    assert claimed is not None
    assert claimed.id == queued.id
    assert claimed.status == "processing"
    assert claimed.evidence == _evidence()
    assert claimed.model == "qwen3.7-plus"
    assert claimed.prompt_version == 1

    reopened.schedule_retry(claimed.id, error="rate limited", now=100.0)
    assert reopened.claim_next(now=101.0) is None
    retried = reopened.claim_next(now=102.0)
    assert retried is not None
    assert retried.attempt_count == 2


def test_sqlite_repository_does_not_touch_disk_until_first_operation(tmp_path):
    from vocal_more.infrastructure.dictionary_learning_repository import (
        DictionaryLearningRepository,
    )

    path = tmp_path / "dictionary-learning.sqlite3"
    DictionaryLearningRepository(database_path=path)

    assert not path.exists()


def test_processor_auto_adds_high_confidence_and_can_undo(tmp_path):
    from vocal_more.application.dictionary_learning_service import (
        DictionaryLearningProcessor,
    )
    from vocal_more.application.dictionary_service import DictionaryService
    from vocal_more.infrastructure.dictionary_learning_repository import (
        DictionaryLearningRepository,
    )
    from vocal_more.infrastructure.dictionary_repository import DictionaryRepository

    learning_repository = DictionaryLearningRepository(
        database_path=tmp_path / "learning.sqlite3"
    )
    dictionary = DictionaryService(DictionaryRepository(base_dir=tmp_path))
    job = learning_repository.enqueue(_evidence(), now=100.0)
    model = MagicMock()
    model.classify.return_value = _decision()
    changes: list[dict] = []
    processor = DictionaryLearningProcessor(
        repository=learning_repository,
        dictionary=dictionary,
        model_client=model,
        on_change=changes.append,
    )

    assert processor.process_next(now=100.0) is True
    assert [(entry.term, entry.aliases) for entry in dictionary.entries] == [
        ("阿里云百炼", ["阿里云白练"])
    ]
    completed = learning_repository.get(job.id)
    assert completed is not None
    assert completed.status == "applied"
    assert completed.evidence.raw_text == ""
    assert completed.evidence.edited_text == ""
    assert changes == [
        {
            "id": job.id,
            "status": "applied",
            "term": "阿里云百炼",
            "aliases": ["阿里云白练"],
            "confidence": 0.98,
            "source": "automatic",
            "dictionary_changed": True,
        }
    ]

    assert processor.undo(job.id) is True
    assert dictionary.entries == []
    assert learning_repository.get(job.id).status == "reverted"


def test_processor_marks_duplicate_auto_add_as_unchanged_for_notifications(tmp_path):
    from vocal_more.application.dictionary_learning_service import (
        DictionaryLearningProcessor,
    )
    from vocal_more.application.dictionary_service import DictionaryService
    from vocal_more.infrastructure.dictionary_learning_repository import (
        DictionaryLearningRepository,
    )
    from vocal_more.infrastructure.dictionary_repository import DictionaryRepository

    learning_repository = DictionaryLearningRepository(
        database_path=tmp_path / "learning.sqlite3"
    )
    dictionary = DictionaryService(DictionaryRepository(base_dir=tmp_path))
    dictionary.add_entry("阿里云百炼", ["阿里云白练"])
    job = learning_repository.enqueue(_evidence(), now=100.0)
    model = MagicMock()
    model.classify.return_value = _decision()
    changes: list[dict] = []
    processor = DictionaryLearningProcessor(
        repository=learning_repository,
        dictionary=dictionary,
        model_client=model,
        on_change=changes.append,
    )

    assert processor.process_next(now=100.0) is True

    assert changes == [
        {
            "id": job.id,
            "status": "applied",
            "term": "阿里云百炼",
            "aliases": ["阿里云白练"],
            "confidence": 0.98,
            "source": "automatic",
            "dictionary_changed": False,
        }
    ]


def test_processor_keeps_medium_confidence_for_review(tmp_path):
    from vocal_more.application.dictionary_learning_service import (
        DictionaryLearningProcessor,
    )
    from vocal_more.application.dictionary_service import DictionaryService
    from vocal_more.infrastructure.dictionary_learning_repository import (
        DictionaryLearningRepository,
    )
    from vocal_more.infrastructure.dictionary_repository import DictionaryRepository

    learning_repository = DictionaryLearningRepository(
        database_path=tmp_path / "learning.sqlite3"
    )
    dictionary = DictionaryService(DictionaryRepository(base_dir=tmp_path))
    job = learning_repository.enqueue(_evidence(), now=100.0)
    model = MagicMock()
    model.classify.return_value = _decision(confidence=0.82)
    processor = DictionaryLearningProcessor(
        repository=learning_repository,
        dictionary=dictionary,
        model_client=model,
    )

    processor.process_next(now=100.0)

    assert dictionary.entries == []
    reviewed = learning_repository.get(job.id)
    assert reviewed is not None
    assert reviewed.status == "review"

    assert processor.approve(job.id) is True
    assert [(entry.term, entry.aliases) for entry in dictionary.entries] == [
        ("阿里云百炼", ["阿里云白练"])
    ]
    assert learning_repository.get(job.id).status == "applied"


def test_processor_marks_review_approval_as_manual_for_notifications(tmp_path):
    from vocal_more.application.dictionary_learning_service import (
        DictionaryLearningProcessor,
    )
    from vocal_more.application.dictionary_service import DictionaryService
    from vocal_more.infrastructure.dictionary_learning_repository import (
        DictionaryLearningRepository,
    )
    from vocal_more.infrastructure.dictionary_repository import DictionaryRepository

    learning_repository = DictionaryLearningRepository(
        database_path=tmp_path / "learning.sqlite3"
    )
    dictionary = DictionaryService(DictionaryRepository(base_dir=tmp_path))
    job = learning_repository.enqueue(_evidence(), now=100.0)
    model = MagicMock()
    model.classify.return_value = _decision(confidence=0.82)
    changes: list[dict] = []
    processor = DictionaryLearningProcessor(
        repository=learning_repository,
        dictionary=dictionary,
        model_client=model,
        on_change=changes.append,
    )
    processor.process_next(now=100.0)
    changes.clear()

    assert processor.approve(job.id) is True

    assert changes == [
        {
            "id": job.id,
            "status": "applied",
            "term": "阿里云百炼",
            "aliases": ["阿里云白练"],
            "confidence": 0.82,
            "source": "review",
            "dictionary_changed": True,
        }
    ]


def test_processor_can_reject_review_without_mutating_dictionary(tmp_path):
    from vocal_more.application.dictionary_learning_service import (
        DictionaryLearningProcessor,
    )
    from vocal_more.application.dictionary_service import DictionaryService
    from vocal_more.infrastructure.dictionary_learning_repository import (
        DictionaryLearningRepository,
    )
    from vocal_more.infrastructure.dictionary_repository import DictionaryRepository

    learning_repository = DictionaryLearningRepository(
        database_path=tmp_path / "learning.sqlite3"
    )
    dictionary = DictionaryService(DictionaryRepository(base_dir=tmp_path))
    job = learning_repository.enqueue(_evidence(), now=100.0)
    model = MagicMock()
    model.classify.return_value = _decision(confidence=0.82)
    processor = DictionaryLearningProcessor(
        repository=learning_repository,
        dictionary=dictionary,
        model_client=model,
    )
    processor.process_next(now=100.0)

    assert processor.reject(job.id) is True
    assert learning_repository.get(job.id).status == "ignored"
    assert dictionary.entries == []


def test_processor_retries_transient_failures_without_blocking(tmp_path):
    from vocal_more.application.dictionary_learning_service import (
        DictionaryLearningProcessor,
    )
    from vocal_more.application.dictionary_service import DictionaryService
    from vocal_more.core.dictionary_learning_model import DictionaryLearningRequestError
    from vocal_more.infrastructure.dictionary_learning_repository import (
        DictionaryLearningRepository,
    )
    from vocal_more.infrastructure.dictionary_repository import DictionaryRepository

    learning_repository = DictionaryLearningRepository(
        database_path=tmp_path / "learning.sqlite3"
    )
    dictionary = DictionaryService(DictionaryRepository(base_dir=tmp_path))
    job = learning_repository.enqueue(_evidence(), now=100.0)
    model = MagicMock()
    model.classify.side_effect = DictionaryLearningRequestError(
        "rate limited",
        retryable=True,
    )
    processor = DictionaryLearningProcessor(
        repository=learning_repository,
        dictionary=dictionary,
        model_client=model,
    )

    processor.process_next(now=100.0)

    pending = learning_repository.get(job.id)
    assert pending is not None
    assert pending.status == "retry"
    assert pending.next_retry_at == 102.0
    assert dictionary.entries == []


def test_processor_recovers_journaled_apply_without_calling_model_again(tmp_path):
    from vocal_more.application.dictionary_learning_service import (
        DictionaryLearningProcessor,
    )
    from vocal_more.application.dictionary_service import DictionaryService
    from vocal_more.domain.dictionary_learning_models import validate_decision
    from vocal_more.domain.dictionary_models import DictionaryMutation
    from vocal_more.infrastructure.dictionary_learning_repository import (
        DictionaryLearningRepository,
    )
    from vocal_more.infrastructure.dictionary_repository import DictionaryRepository

    database_path = tmp_path / "learning.sqlite3"
    learning_repository = DictionaryLearningRepository(database_path=database_path)
    dictionary = DictionaryService(DictionaryRepository(base_dir=tmp_path))
    queued = learning_repository.enqueue(_evidence(), now=100.0)
    claimed = learning_repository.claim_next(now=100.0)
    result = validate_decision(_decision(), claimed.evidence)
    mutation = DictionaryMutation(
        term=result.term,
        term_created=True,
        aliases_added=result.aliases,
    )
    learning_repository.mark_applying(
        queued.id,
        result=result,
        mutation=mutation,
        now=100.0,
    )
    dictionary.add_entry(result.term, result.aliases)

    # Simulate an app exit after YAML was saved but before the final SQLite
    # transition. Reopening resets "applying" to a retryable job.
    reopened = DictionaryLearningRepository(database_path=database_path)
    model = MagicMock()
    processor = DictionaryLearningProcessor(
        repository=reopened,
        dictionary=dictionary,
        model_client=model,
    )

    assert processor.process_next(now=101.0) is True
    model.classify.assert_not_called()
    recovered = reopened.get(queued.id)
    assert recovered.status == "applied"
    assert recovered.term_created is True
    assert recovered.aliases_added == ["阿里云白练"]
    assert processor.undo(queued.id) is True
    assert dictionary.entries == []


class _SnapshotProvider:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)

    def capture_focused(self):
        if not self.snapshots:
            return None
        return self.snapshots.pop(0)


def _snapshot(value: str, **overrides):
    from vocal_more.core.accessibility_text import FocusedTextSnapshot

    values = {
        "target_id": "123:editor",
        "pid": 123,
        "value": value,
        "role": "AXTextArea",
        "subrole": "",
        "app_bundle_id": "com.apple.Notes",
        "app_name": "Notes",
        "is_secure": False,
    }
    values.update(overrides)
    return FocusedTextSnapshot(**values)


def _fake_time():
    state = {"now": 0.0}

    def clock():
        return state["now"]

    def sleep(seconds):
        state["now"] += seconds

    return clock, sleep


def test_edit_observer_captures_same_field_correction():
    from vocal_more.application.dictionary_edit_observer import DictionaryEditObserver

    provider = _SnapshotProvider(
        [
            _snapshot(""),
            _snapshot("今天用阿里云白练测试。"),
            _snapshot("今天用阿里云百炼测试。"),
            _snapshot(
                "另一个输入框",
                target_id="123:other",
            ),
        ]
    )
    clock, sleep = _fake_time()
    observer = DictionaryEditObserver(
        provider=provider,
        observation_seconds=15.0,
        poll_interval=0.25,
        clock=clock,
        sleep=sleep,
    )

    ticket = observer.prepare(
        raw_text="今天用阿里云白练测试。",
        pasted_text="今天用阿里云白练测试。",
        recording_id="recording-1",
        mode_name="walkie_talkie",
    )
    evidence = observer.observe(ticket)

    assert evidence == _evidence()


def test_edit_observer_coalesces_multiple_edits_into_only_the_final_text():
    from vocal_more.application.dictionary_edit_observer import DictionaryEditObserver

    provider = _SnapshotProvider(
        [
            _snapshot(""),
            _snapshot("今天用阿里云白练测试。"),
            _snapshot("今天用阿里云百练测试。"),
            _snapshot("今天用阿里云百炼测试。"),
            _snapshot("另一个输入框", target_id="123:other"),
        ]
    )
    clock, sleep = _fake_time()
    observer = DictionaryEditObserver(
        provider=provider,
        observation_seconds=15.0,
        poll_interval=0.25,
        clock=clock,
        sleep=sleep,
    )

    ticket = observer.prepare(
        raw_text="今天用阿里云白练测试。",
        pasted_text="今天用阿里云白练测试。",
        recording_id="recording-1",
        mode_name="walkie_talkie",
    )
    evidence = observer.observe(ticket)

    assert evidence == _evidence()


def test_edit_observer_ignores_correction_that_is_reverted_to_pasted_baseline():
    from vocal_more.application.dictionary_edit_observer import DictionaryEditObserver

    provider = _SnapshotProvider(
        [
            _snapshot(""),
            _snapshot("今天用阿里云白练测试。"),
            _snapshot("今天用阿里云百炼测试。"),
            _snapshot("今天用阿里云白练测试。"),
            _snapshot("另一个输入框", target_id="123:other"),
        ]
    )
    clock, sleep = _fake_time()
    observer = DictionaryEditObserver(
        provider=provider,
        observation_seconds=15.0,
        poll_interval=0.25,
        clock=clock,
        sleep=sleep,
    )

    ticket = observer.prepare(
        raw_text="今天用阿里云白练测试。",
        pasted_text="今天用阿里云白练测试。",
        recording_id="recording-1",
        mode_name="walkie_talkie",
    )

    assert observer.observe(ticket) is None


def test_edit_observer_ignores_edits_outside_the_current_pasted_segment():
    from vocal_more.application.dictionary_edit_observer import DictionaryEditObserver

    original = "旧段落使用阿里云白练。"
    pasted = "今天用阿里云白练测试。"
    provider = _SnapshotProvider(
        [
            _snapshot(original),
            _snapshot(original + pasted),
            _snapshot("旧段落使用阿里云百炼。" + pasted),
            _snapshot("另一个输入框", target_id="123:other"),
        ]
    )
    clock, sleep = _fake_time()
    observer = DictionaryEditObserver(
        provider=provider,
        poll_interval=0.25,
        clock=clock,
        sleep=sleep,
    )
    ticket = observer.prepare(
        raw_text=pasted,
        pasted_text=pasted,
        recording_id=None,
        mode_name="walkie_talkie",
    )

    assert observer.observe(ticket) is None


def test_edit_observer_scopes_mixed_edits_to_the_current_pasted_segment():
    from vocal_more.application.dictionary_edit_observer import DictionaryEditObserver

    original = "旧段落使用阿里云白练。"
    pasted = "今天用阿里云白练测试。"
    provider = _SnapshotProvider(
        [
            _snapshot(original),
            _snapshot(original + pasted),
            _snapshot("旧段落使用阿里云百炼。今天用阿里云百炼测试。"),
            _snapshot("另一个输入框", target_id="123:other"),
        ]
    )
    clock, sleep = _fake_time()
    observer = DictionaryEditObserver(
        provider=provider,
        poll_interval=0.25,
        clock=clock,
        sleep=sleep,
    )
    ticket = observer.prepare(
        raw_text=pasted,
        pasted_text=pasted,
        recording_id=None,
        mode_name="walkie_talkie",
    )

    evidence = observer.observe(ticket)

    assert evidence is not None
    assert evidence.original_text == ""
    assert evidence.baseline_text == "今天用阿里云白练测试。"
    assert evidence.edited_text == "今天用阿里云百炼测试。"


def test_edit_observer_discards_unsettled_edit_at_observation_deadline():
    from vocal_more.application.dictionary_edit_observer import DictionaryEditObserver

    state = {"now": 0.0, "captures": 0}

    class _TimedProvider:
        def capture_focused(self):
            state["captures"] += 1
            if state["captures"] == 1:
                return _snapshot("")
            if state["captures"] == 2:
                return _snapshot("今天用阿里云白练测试。")
            if state["now"] >= 14.75:
                return _snapshot("今天用阿里云百炼测试。")
            return _snapshot("今天用阿里云白练测试。")

    def clock():
        return state["now"]

    def sleep(seconds):
        state["now"] += seconds

    observer = DictionaryEditObserver(
        provider=_TimedProvider(),
        observation_seconds=15.0,
        poll_interval=0.25,
        settle_seconds=0.5,
        clock=clock,
        sleep=sleep,
    )
    ticket = observer.prepare(
        raw_text="今天用阿里云白练测试。",
        pasted_text="今天用阿里云白练测试。",
        recording_id=None,
        mode_name="walkie_talkie",
    )

    assert observer.observe(ticket) is None


def test_edit_observer_discards_observation_if_target_becomes_secure():
    from vocal_more.application.dictionary_edit_observer import DictionaryEditObserver

    provider = _SnapshotProvider(
        [
            _snapshot(""),
            _snapshot("今天用阿里云白练测试。"),
            _snapshot("今天用阿里云百炼测试。"),
            _snapshot("", is_secure=True),
        ]
    )
    clock, sleep = _fake_time()
    observer = DictionaryEditObserver(
        provider=provider,
        poll_interval=0.25,
        clock=clock,
        sleep=sleep,
    )
    ticket = observer.prepare(
        raw_text="今天用阿里云白练测试。",
        pasted_text="今天用阿里云白练测试。",
        recording_id=None,
        mode_name="walkie_talkie",
    )

    assert observer.observe(ticket) is None


def test_edit_observer_skips_secure_fields_and_excluded_apps():
    from vocal_more.application.dictionary_edit_observer import DictionaryEditObserver

    secure = DictionaryEditObserver(
        provider=_SnapshotProvider([_snapshot("", is_secure=True)])
    )
    assert (
        secure.prepare(
            raw_text="secret",
            pasted_text="secret",
            recording_id=None,
            mode_name="walkie_talkie",
        )
        is None
    )

    excluded = DictionaryEditObserver(
        provider=_SnapshotProvider([_snapshot("")]),
        excluded_bundle_ids={"com.apple.Notes"},
    )
    assert (
        excluded.prepare(
            raw_text="text",
            pasted_text="text",
            recording_id=None,
            mode_name="walkie_talkie",
        )
        is None
    )


def test_macos_provider_never_reads_secure_field_value(monkeypatch):
    import AppKit

    from vocal_more.core.accessibility_text import MacOSFocusedTextProvider

    api = types.ModuleType("ApplicationServices")
    api.kAXFocusedUIElementAttribute = "focused"
    api.kAXRoleAttribute = "role"
    api.kAXSubroleAttribute = "subrole"
    api.kAXValueAttribute = "value"
    api.kAXIdentifierAttribute = "identifier"
    api.AXUIElementCreateSystemWide = lambda: "system"
    requested: list[str] = []

    def copy_attribute(_element, attribute, _output):
        requested.append(attribute)
        values = {
            "focused": "secure-element",
            "role": "AXTextField",
            "subrole": "AXSecureTextField",
            "identifier": "password",
        }
        if attribute == "value":
            raise AssertionError("secure value must not be read")
        return 0, values[attribute]

    api.AXUIElementCopyAttributeValue = copy_attribute
    api.AXUIElementGetPid = lambda _element, _output: (0, 123)
    core_foundation = types.ModuleType("CoreFoundation")
    core_foundation.CFHash = lambda _element: 456

    class _RunningApplication:
        @staticmethod
        def runningApplicationWithProcessIdentifier_(_pid):
            return SimpleNamespace(
                bundleIdentifier=lambda: "com.example.passwords",
                localizedName=lambda: "Passwords",
            )

    monkeypatch.setitem(sys.modules, "ApplicationServices", api)
    monkeypatch.setitem(sys.modules, "CoreFoundation", core_foundation)
    monkeypatch.setattr(
        AppKit,
        "NSRunningApplication",
        _RunningApplication,
        raising=False,
    )

    snapshot = MacOSFocusedTextProvider().capture_focused()

    assert snapshot is not None
    assert snapshot.is_secure is True
    assert snapshot.value == ""
    assert "value" not in requested


def test_edit_observer_rejects_clear_and_large_rewrite():
    from vocal_more.application.dictionary_edit_observer import DictionaryEditObserver

    provider = _SnapshotProvider(
        [
            _snapshot("prefix "),
            _snapshot("prefix 一段需要保留的听写内容"),
            _snapshot("完全不同"),
            None,
        ]
    )
    clock, sleep = _fake_time()
    observer = DictionaryEditObserver(
        provider=provider,
        clock=clock,
        sleep=sleep,
        poll_interval=0.25,
    )

    ticket = observer.prepare(
        raw_text="一段需要保留的听写内容",
        pasted_text="一段需要保留的听写内容",
        recording_id=None,
        mode_name="realtime_long",
    )

    assert observer.observe(ticket) is None


def test_edit_observer_keeps_late_correction_in_bounded_long_document_context():
    from vocal_more.application.dictionary_edit_observer import DictionaryEditObserver

    prefix = "已有内容" * 2_500
    provider = _SnapshotProvider(
        [
            _snapshot(prefix),
            _snapshot(prefix + "阿里云白练"),
            _snapshot(prefix + "阿里云百炼"),
            None,
        ]
    )
    clock, sleep = _fake_time()
    observer = DictionaryEditObserver(
        provider=provider,
        clock=clock,
        sleep=sleep,
        poll_interval=0.25,
    )
    ticket = observer.prepare(
        raw_text="阿里云白练",
        pasted_text="阿里云白练",
        recording_id=None,
        mode_name="realtime_long",
    )

    evidence = observer.observe(ticket)

    assert evidence is not None
    assert len(evidence.baseline_text) <= 8_000
    assert len(evidence.edited_text) <= 8_000
    assert "阿里云白练" in evidence.baseline_text
    assert "阿里云百炼" in evidence.edited_text


class _ImmediateExecutor:
    def submit(self, callback, *args, **kwargs):
        callback(*args, **kwargs)
        return SimpleNamespace(done=lambda: True)

    def close(self, **_kwargs):
        return None


def test_coordinator_queues_observed_evidence_only_when_enabled():
    from vocal_more.application.dictionary_learning_runtime import (
        AutomaticDictionaryLearningCoordinator,
    )

    config = SimpleNamespace(
        api_key="user-key",
        dictionary_learning=SimpleNamespace(
            enabled=True,
            excluded_bundle_ids=["com.1password.1password"],
        ),
    )
    observer = MagicMock()
    observer.prepare.return_value = "observer-ticket"
    observer.observe.return_value = _evidence()
    observer_factory = MagicMock(return_value=observer)
    repository = MagicMock()
    queue_worker = MagicMock()
    coordinator = AutomaticDictionaryLearningCoordinator(
        config=config,
        observer_factory=observer_factory,
        repository=repository,
        queue_worker=queue_worker,
        executor=_ImmediateExecutor(),
    )

    ticket = coordinator.prepare_paste(
        raw_text="今天用阿里云白练测试。",
        pasted_text="今天用阿里云白练测试。",
        recording_id="recording-1",
        mode_name="walkie_talkie",
    )
    coordinator.observe_after_paste(ticket)

    observer_factory.assert_called_once_with(
        excluded_bundle_ids={"com.1password.1password"}
    )
    repository.enqueue.assert_called_once_with(_evidence())
    queue_worker.wake.assert_called_once_with()

    config.dictionary_learning.enabled = False
    assert (
        coordinator.prepare_paste(
            raw_text="ignored",
            pasted_text="ignored",
            recording_id=None,
            mode_name="walkie_talkie",
        )
        is None
    )


def test_queue_worker_never_processes_when_disabled_or_key_is_missing():
    from vocal_more.application.dictionary_learning_runtime import (
        DictionaryLearningQueueWorker,
    )

    config = SimpleNamespace(
        api_key="",
        dictionary_learning=SimpleNamespace(enabled=True),
    )
    processor = MagicMock()
    worker = DictionaryLearningQueueWorker(
        config=config,
        processor=processor,
        repository=MagicMock(),
        auto_start=False,
    )

    assert worker.process_available_once() is False
    processor.process_next.assert_not_called()

    config.api_key = "user-key"
    config.dictionary_learning.enabled = False
    assert worker.process_available_once() is False
    processor.process_next.assert_not_called()

    config.dictionary_learning.enabled = True
    processor.process_next.return_value = True
    assert worker.process_available_once() is True
    processor.process_next.assert_called_once_with()


def test_queue_worker_starts_lazily_only_after_feature_is_enabled():
    from vocal_more.application.dictionary_learning_runtime import (
        DictionaryLearningQueueWorker,
    )

    config = SimpleNamespace(
        api_key="user-key",
        dictionary_learning=SimpleNamespace(enabled=False),
    )
    executor = MagicMock()
    worker = DictionaryLearningQueueWorker(
        config=config,
        processor=MagicMock(),
        repository=MagicMock(),
        executor=executor,
    )

    executor.submit.assert_not_called()

    config.dictionary_learning.enabled = True
    worker.wake()

    executor.submit.assert_called_once_with(worker._run)
