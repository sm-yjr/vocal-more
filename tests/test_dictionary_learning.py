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


def _candidate_evidences():
    from vocal_more.application.dictionary_learning_candidates import (
        split_dictionary_learning_evidence,
    )

    return split_dictionary_learning_evidence(
        _evidence(
            raw_text="范UI是一定要符合Shadcn UI的。",
            pasted_text="范UI是一定要符合Shadcn UI的。",
            baseline_text="范UI是一定要符合Shadcn UI的。",
            edited_text="FanUI是一定要符合Shadcn/ui的。",
            recording_id="recording-multi",
        ),
        observation_id="observation-multi",
    )


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
    assert "指定候选" in kwargs["messages"][0]["content"]
    assert "不要判断其他位置" in kwargs["messages"][0]["content"]
    assert "只选择置信度最高" not in kwargs["messages"][0]["content"]
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


def test_decision_validation_scopes_mapping_to_current_candidate():
    from vocal_more.domain.dictionary_learning_models import validate_decision

    evidence = _evidence(
        raw_text="范UI是一定要符合Shadcn UI的。",
        pasted_text="范UI是一定要符合Shadcn UI的。",
        baseline_text="范UI是一定要符合Shadcn UI的。",
        edited_text="FanUI是一定要符合Shadcn/ui的。",
        observation_id="observation-1",
        candidate_index=0,
        candidate_count=2,
        candidate_before_text="范UI是一定要符合",
        candidate_after_text="FanUI是一定要符合",
    )

    fanui = validate_decision(
        _decision(
            term="FanUI",
            aliases=["范UI"],
            reason_code="product_name_correction",
        ),
        evidence,
    )
    wrong_sibling = validate_decision(
        _decision(
            term="Shadcn/ui",
            aliases=["Shadcn UI"],
            reason_code="technical_term_correction",
        ),
        evidence,
    )

    assert fanui.action == "add"
    assert wrong_sibling.action == "ignore"
    assert wrong_sibling.reason_code == "term_not_in_candidate"


def test_decision_validation_rejects_alias_from_sibling_candidate():
    from vocal_more.domain.dictionary_learning_models import validate_decision

    evidence = _evidence(
        raw_text="范UI是一定要符合Shadcn UI的。",
        pasted_text="范UI是一定要符合Shadcn UI的。",
        baseline_text="范UI是一定要符合Shadcn UI的。",
        edited_text="FanUI是一定要符合Shadcn/ui的。",
        observation_id="observation-1",
        candidate_index=1,
        candidate_count=2,
        candidate_before_text="符合Shadcn UI的。",
        candidate_after_text="符合Shadcn/ui的。",
    )

    result = validate_decision(
        _decision(
            term="Shadcn/ui",
            aliases=["范UI"],
            reason_code="technical_term_correction",
        ),
        evidence,
    )

    assert result.action == "ignore"
    assert result.reason_code == "alias_not_in_candidate"


def test_decision_validation_requires_mapping_to_overlap_candidate_diff():
    from vocal_more.application.dictionary_learning_candidates import (
        split_dictionary_learning_evidence,
    )
    from vocal_more.domain.dictionary_learning_models import validate_decision

    before = "范UI----Shadcn UI"
    after = "FanUI----Shadcn/ui"
    fanui_evidence, shadcn_evidence = split_dictionary_learning_evidence(
        _evidence(
            raw_text=before,
            pasted_text=before,
            baseline_text=before,
            edited_text=after,
        ),
        observation_id="observation-overlap",
    )

    fanui_from_wrong_candidate = validate_decision(
        _decision(
            term="FanUI",
            aliases=["范UI"],
            reason_code="product_name_correction",
        ),
        shadcn_evidence,
    )
    shadcn_from_wrong_candidate = validate_decision(
        _decision(
            term="Shadcn/ui",
            aliases=["Shadcn UI"],
            reason_code="technical_term_correction",
        ),
        fanui_evidence,
    )

    assert fanui_from_wrong_candidate.action == "ignore"
    assert fanui_from_wrong_candidate.reason_code == "term_not_in_candidate"
    assert shadcn_from_wrong_candidate.action == "ignore"
    assert shadcn_from_wrong_candidate.reason_code == "term_not_in_candidate"


def test_decision_validation_routes_medium_confidence_to_review():
    from vocal_more.domain.dictionary_learning_models import validate_decision

    result = validate_decision(_decision(confidence=0.82), _evidence())

    assert result.action == "review"
    assert result.term == "阿里云百炼"
    assert result.aliases == ["阿里云白练"]


def test_decision_validation_routes_low_confidence_candidate_to_review():
    from vocal_more.domain.dictionary_learning_models import validate_decision

    result = validate_decision(_decision(confidence=0.28), _evidence())

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
    assert claimed.evidence == queued.evidence
    assert claimed.model == "qwen3.7-plus"
    assert claimed.prompt_version == 3

    reopened.schedule_retry(claimed.id, error="rate limited", now=100.0)
    assert reopened.claim_next(now=101.0) is None
    retried = reopened.claim_next(now=102.0)
    assert retried is not None
    assert retried.attempt_count == 2


def test_repository_enqueue_many_is_atomic_and_preserves_candidate_order(tmp_path):
    from vocal_more.infrastructure.dictionary_learning_repository import (
        DictionaryLearningRepository,
    )

    repository = DictionaryLearningRepository(
        database_path=tmp_path / "learning.sqlite3"
    )

    jobs = repository.enqueue_many(_candidate_evidences(), now=100.0)

    assert len(jobs) == 2
    assert [job.observation_id for job in jobs] == [
        "observation-multi",
        "observation-multi",
    ]
    assert [job.candidate_index for job in jobs] == [0, 1]
    assert [job.candidate_count for job in jobs] == [2, 2]
    assert [job.prompt_version for job in jobs] == [3, 3]
    assert repository.claim_next(now=100.0).id == jobs[0].id
    assert repository.claim_next(now=100.0).id == jobs[1].id


def test_repository_enqueue_many_rolls_back_all_candidates_on_insert_failure(
    tmp_path,
):
    import sqlite3

    import pytest

    from vocal_more.infrastructure.dictionary_learning_repository import (
        DictionaryLearningRepository,
    )

    database_path = tmp_path / "learning.sqlite3"
    repository = DictionaryLearningRepository(database_path=database_path)
    assert repository.list_jobs() == []
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_second_candidate
            BEFORE INSERT ON dictionary_learning_jobs
            WHEN NEW.candidate_index = 1
            BEGIN
                SELECT RAISE(ABORT, 'second candidate rejected');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="second candidate"):
        repository.enqueue_many(_candidate_evidences(), now=100.0)

    assert repository.list_jobs() == []


def test_sqlite_repository_does_not_touch_disk_until_first_operation(tmp_path):
    from vocal_more.infrastructure.dictionary_learning_repository import (
        DictionaryLearningRepository,
    )

    path = tmp_path / "dictionary-learning.sqlite3"
    DictionaryLearningRepository(database_path=path)

    assert not path.exists()


def test_repository_migrates_legacy_single_candidate_database(tmp_path):
    import sqlite3

    from vocal_more.infrastructure.dictionary_learning_repository import (
        DictionaryLearningRepository,
    )

    path = tmp_path / "legacy-learning.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE dictionary_learning_jobs (
                id TEXT PRIMARY KEY,
                evidence_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                next_retry_at REAL NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '',
                result_json TEXT,
                term_created INTEGER NOT NULL DEFAULT 0,
                aliases_added_json TEXT NOT NULL DEFAULT '[]',
                model TEXT NOT NULL DEFAULT 'qwen3.7-plus',
                prompt_version INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        connection.execute(
            """
            INSERT INTO dictionary_learning_jobs (
                id, evidence_json, status, created_at, updated_at,
                attempt_count, next_retry_at, model, prompt_version
            ) VALUES (?, ?, 'pending', 100, 100, 0, 100, 'qwen3.7-plus', 1)
            """,
            (
                "legacy-job",
                json.dumps(_evidence().to_dict(), ensure_ascii=False),
            ),
        )

    jobs = DictionaryLearningRepository(database_path=path).list_jobs()

    assert len(jobs) == 1
    assert jobs[0].id == "legacy-job"
    assert jobs[0].observation_id == ""
    assert jobs[0].candidate_index == 0
    assert jobs[0].candidate_count == 1
    assert jobs[0].notification_emitted is False
    assert jobs[0].prompt_version == 1


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


def test_processor_applies_and_independently_undoes_two_sibling_candidates(
    tmp_path,
):
    from vocal_more.application.dictionary_learning_runtime import (
        AutomaticDictionaryLearningCoordinator,
    )
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
    jobs = learning_repository.enqueue_many(_candidate_evidences(), now=100.0)
    model = MagicMock()
    model.classify.side_effect = [
        _decision(
            term="FanUI",
            aliases=["范UI"],
            reason_code="product_name_correction",
        ),
        _decision(
            term="Shadcn/ui",
            aliases=["Shadcn UI"],
            reason_code="technical_term_correction",
        ),
    ]
    processor = DictionaryLearningProcessor(
        repository=learning_repository,
        dictionary=dictionary,
        model_client=model,
    )

    assert processor.process_next(now=100.0) is True
    assert processor.process_next(now=100.0) is True

    assert [(entry.term, entry.aliases) for entry in dictionary.entries] == [
        ("FanUI", ["范UI"]),
        ("Shadcn/ui", ["Shadcn UI"]),
    ]
    assert [learning_repository.get(job.id).status for job in jobs] == [
        "applied",
        "applied",
    ]

    coordinator = AutomaticDictionaryLearningCoordinator(
        config=SimpleNamespace(),
        observer_factory=MagicMock(),
        repository=learning_repository,
        queue_worker=MagicMock(),
        processor=processor,
        executor=_ImmediateExecutor(),
    )
    records = sorted(
        coordinator.list_recent(),
        key=lambda record: record["term"],
    )
    assert [(record["id"], record["term"]) for record in records] == [
        (jobs[0].id, "FanUI"),
        (jobs[1].id, "Shadcn/ui"),
    ]

    assert processor.undo(jobs[0].id) is True
    assert [(entry.term, entry.aliases) for entry in dictionary.entries] == [
        ("Shadcn/ui", ["Shadcn UI"]),
    ]
    assert learning_repository.get(jobs[0].id).status == "reverted"
    assert learning_repository.get(jobs[1].id).status == "applied"


def test_processor_emits_one_group_summary_after_all_siblings_finish(tmp_path):
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
    jobs = learning_repository.enqueue_many(_candidate_evidences(), now=100.0)
    model = MagicMock()
    model.classify.side_effect = [
        _decision(term="FanUI", aliases=["范UI"]),
        _decision(term="Shadcn/ui", aliases=["Shadcn UI"]),
    ]
    changes: list[dict] = []
    processor = DictionaryLearningProcessor(
        repository=learning_repository,
        dictionary=dictionary,
        model_client=model,
        on_change=changes.append,
    )

    processor.process_next(now=100.0)
    assert not any(change["status"] == "applied_group" for change in changes)

    processor.process_next(now=100.0)

    group_changes = [
        change for change in changes if change["status"] == "applied_group"
    ]
    assert group_changes == [
        {
            "id": "observation-multi",
            "status": "applied_group",
            "terms": ["FanUI", "Shadcn/ui"],
            "source": "automatic",
            "dictionary_changed": True,
        }
    ]
    assert learning_repository.claim_observation_notification(
        "observation-multi"
    ) is None
    assert all(
        learning_repository.get(job.id).notification_emitted
        for job in jobs
    )


def test_group_summary_excludes_duplicate_that_did_not_change_dictionary(
    tmp_path,
):
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
    dictionary.add_entry("FanUI", ["范UI"])
    learning_repository.enqueue_many(_candidate_evidences(), now=100.0)
    model = MagicMock()
    model.classify.side_effect = [
        _decision(term="FanUI", aliases=["范UI"]),
        _decision(term="Shadcn/ui", aliases=["Shadcn UI"]),
    ]
    changes: list[dict] = []
    processor = DictionaryLearningProcessor(
        repository=learning_repository,
        dictionary=dictionary,
        model_client=model,
        on_change=changes.append,
    )

    processor.process_next(now=100.0)
    processor.process_next(now=100.0)

    group_change = next(
        change for change in changes if change["status"] == "applied_group"
    )
    assert group_change["terms"] == ["Shadcn/ui"]


def test_processor_keeps_applied_sibling_while_other_candidate_retries(tmp_path):
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
    jobs = learning_repository.enqueue_many(_candidate_evidences(), now=100.0)
    model = MagicMock()
    model.classify.side_effect = [
        _decision(
            term="FanUI",
            aliases=["范UI"],
            reason_code="product_name_correction",
        ),
        DictionaryLearningRequestError("rate limited", retryable=True),
    ]
    processor = DictionaryLearningProcessor(
        repository=learning_repository,
        dictionary=dictionary,
        model_client=model,
    )

    processor.process_next(now=100.0)
    processor.process_next(now=100.0)

    assert [(entry.term, entry.aliases) for entry in dictionary.entries] == [
        ("FanUI", ["范UI"]),
    ]
    assert learning_repository.get(jobs[0].id).status == "applied"
    assert learning_repository.get(jobs[1].id).status == "retry"

    model.classify.side_effect = None
    model.classify.return_value = _decision(
        term="Shadcn/ui",
        aliases=["Shadcn UI"],
        reason_code="technical_term_correction",
    )
    processor.process_next(now=102.0)

    assert [(entry.term, entry.aliases) for entry in dictionary.entries] == [
        ("FanUI", ["范UI"]),
        ("Shadcn/ui", ["Shadcn UI"]),
    ]


def test_processor_keeps_valid_sibling_when_other_response_is_invalid(tmp_path):
    from vocal_more.application.dictionary_learning_service import (
        DictionaryLearningProcessor,
    )
    from vocal_more.application.dictionary_service import DictionaryService
    from vocal_more.core.dictionary_learning_model import (
        DictionaryLearningResponseError,
    )
    from vocal_more.infrastructure.dictionary_learning_repository import (
        DictionaryLearningRepository,
    )
    from vocal_more.infrastructure.dictionary_repository import DictionaryRepository

    learning_repository = DictionaryLearningRepository(
        database_path=tmp_path / "learning.sqlite3"
    )
    dictionary = DictionaryService(DictionaryRepository(base_dir=tmp_path))
    jobs = learning_repository.enqueue_many(_candidate_evidences(), now=100.0)
    model = MagicMock()
    model.classify.side_effect = [
        DictionaryLearningResponseError("invalid JSON"),
        _decision(term="Shadcn/ui", aliases=["Shadcn UI"]),
    ]
    changes: list[dict] = []
    processor = DictionaryLearningProcessor(
        repository=learning_repository,
        dictionary=dictionary,
        model_client=model,
        on_change=changes.append,
    )

    processor.process_next(now=100.0)
    processor.process_next(now=100.0)

    assert learning_repository.get(jobs[0].id).status == "failed"
    assert learning_repository.get(jobs[1].id).status == "applied"
    assert [(entry.term, entry.aliases) for entry in dictionary.entries] == [
        ("Shadcn/ui", ["Shadcn UI"]),
    ]
    group_change = next(
        change for change in changes if change["status"] == "applied_group"
    )
    assert group_change["terms"] == ["Shadcn/ui"]


def test_processor_applies_high_confidence_sibling_and_reviews_other(tmp_path):
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
    jobs = learning_repository.enqueue_many(_candidate_evidences(), now=100.0)
    model = MagicMock()
    model.classify.side_effect = [
        _decision(term="FanUI", aliases=["范UI"]),
        _decision(
            term="Shadcn/ui",
            aliases=["Shadcn UI"],
            confidence=0.82,
        ),
    ]
    processor = DictionaryLearningProcessor(
        repository=learning_repository,
        dictionary=dictionary,
        model_client=model,
    )

    processor.process_next(now=100.0)
    processor.process_next(now=100.0)

    assert learning_repository.get(jobs[0].id).status == "applied"
    assert learning_repository.get(jobs[1].id).status == "review"
    assert [(entry.term, entry.aliases) for entry in dictionary.entries] == [
        ("FanUI", ["范UI"]),
    ]

    assert processor.approve(jobs[1].id) is True
    assert [(entry.term, entry.aliases) for entry in dictionary.entries] == [
        ("FanUI", ["范UI"]),
        ("Shadcn/ui", ["Shadcn UI"]),
    ]


def test_processor_revalidates_sibling_conflict_against_latest_dictionary(
    tmp_path,
):
    from vocal_more.application.dictionary_learning_service import (
        DictionaryLearningProcessor,
    )
    from vocal_more.application.dictionary_service import DictionaryService
    from vocal_more.infrastructure.dictionary_learning_repository import (
        DictionaryLearningRepository,
    )
    from vocal_more.infrastructure.dictionary_repository import DictionaryRepository

    shared = {
        "raw_text": "Cloud Code first; Cloud Code second",
        "pasted_text": "Cloud Code first; Cloud Code second",
        "baseline_text": "Cloud Code first; Cloud Code second",
        "edited_text": "Claude Code first; CloudCode second",
        "observation_id": "observation-conflict",
        "candidate_count": 2,
    }
    candidates = [
        _evidence(
            **shared,
            candidate_index=0,
            candidate_before_text="Cloud Code first",
            candidate_after_text="Claude Code first",
        ),
        _evidence(
            **shared,
            candidate_index=1,
            candidate_before_text="Cloud Code second",
            candidate_after_text="CloudCode second",
        ),
    ]
    learning_repository = DictionaryLearningRepository(
        database_path=tmp_path / "learning.sqlite3"
    )
    dictionary = DictionaryService(DictionaryRepository(base_dir=tmp_path))
    jobs = learning_repository.enqueue_many(candidates, now=100.0)
    model = MagicMock()
    model.classify.side_effect = [
        _decision(term="Claude Code", aliases=["Cloud Code"]),
        _decision(term="CloudCode", aliases=["Cloud Code"]),
    ]
    processor = DictionaryLearningProcessor(
        repository=learning_repository,
        dictionary=dictionary,
        model_client=model,
    )

    processor.process_next(now=100.0)
    processor.process_next(now=100.0)

    assert [(entry.term, entry.aliases) for entry in dictionary.entries] == [
        ("Claude Code", ["Cloud Code"]),
    ]
    assert learning_repository.get(jobs[0].id).status == "applied"
    second = learning_repository.get(jobs[1].id)
    assert second.status == "ignored"
    assert second.result.reason_code == "dictionary_conflict"


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


def test_processor_recovers_journaled_sibling_then_processes_remaining_candidate(
    tmp_path,
):
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
    jobs = learning_repository.enqueue_many(_candidate_evidences(), now=100.0)
    claimed = learning_repository.claim_next(now=100.0)
    result = validate_decision(
        _decision(term="FanUI", aliases=["范UI"]),
        claimed.evidence,
    )
    mutation = DictionaryMutation(
        term=result.term,
        term_created=True,
        aliases_added=result.aliases,
    )
    learning_repository.mark_applying(
        jobs[0].id,
        result=result,
        mutation=mutation,
        now=100.0,
    )
    dictionary.add_entry(result.term, result.aliases)

    reopened = DictionaryLearningRepository(database_path=database_path)
    model = MagicMock()
    model.classify.return_value = _decision(
        term="Shadcn/ui",
        aliases=["Shadcn UI"],
    )
    processor = DictionaryLearningProcessor(
        repository=reopened,
        dictionary=dictionary,
        model_client=model,
    )

    processor.process_next(now=101.0)
    processor.process_next(now=101.0)

    model.classify.assert_called_once()
    assert [reopened.get(job.id).status for job in jobs] == [
        "applied",
        "applied",
    ]
    assert [(entry.term, entry.aliases) for entry in dictionary.entries] == [
        ("FanUI", ["范UI"]),
        ("Shadcn/ui", ["Shadcn UI"]),
    ]


class _SnapshotProvider:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)

    def capture_focused(self):
        if not self.snapshots:
            return None
        return self.snapshots.pop(0)


class _RetainedTargetProvider(_SnapshotProvider):
    def __init__(self, snapshots, retained_snapshot):
        super().__init__(snapshots)
        self.retained_snapshot = retained_snapshot
        self.captured_targets = []

    def capture_target(self, original):
        self.captured_targets.append(original)
        return self.retained_snapshot


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


def test_edit_observer_reads_final_correction_from_original_target_on_focus_change():
    from vocal_more.application.dictionary_edit_observer import DictionaryEditObserver

    original = _snapshot("")
    provider = _RetainedTargetProvider(
        [
            original,
            _snapshot("今天用阿里云白练测试。"),
            _snapshot("另一个输入框", target_id="123:other"),
        ],
        retained_snapshot=_snapshot("今天用阿里云百炼测试。"),
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
    assert provider.captured_targets == [original]


def test_edit_observer_recovers_when_focus_moves_before_first_post_paste_poll():
    from vocal_more.application.dictionary_edit_observer import DictionaryEditObserver

    original = _snapshot("")
    provider = _RetainedTargetProvider(
        [
            original,
            _snapshot("另一个输入框", target_id="123:other"),
        ],
        retained_snapshot=_snapshot("今天用阿里云百炼测试。"),
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
        recording_id="recording-fast-switch",
        mode_name="walkie_talkie",
    )
    evidence = observer.observe(ticket)

    assert evidence == _evidence(recording_id="recording-fast-switch")
    assert provider.captured_targets == [original]


def test_edit_observer_uses_selected_range_to_recover_fast_focus_switch():
    from vocal_more.application.dictionary_edit_observer import DictionaryEditObserver

    original = _snapshot(
        "开头旧内容结尾",
        selection_start=2,
        selection_length=3,
    )
    provider = _RetainedTargetProvider(
        [
            original,
            _snapshot("另一个输入框", target_id="123:other"),
        ],
        retained_snapshot=_snapshot("开头阿里云百炼结尾"),
    )
    clock, sleep = _fake_time()
    observer = DictionaryEditObserver(
        provider=provider,
        poll_interval=0.25,
        clock=clock,
        sleep=sleep,
    )

    ticket = observer.prepare(
        raw_text="阿里云白练",
        pasted_text="阿里云白练",
        recording_id=None,
        mode_name="walkie_talkie",
    )
    evidence = observer.observe(ticket)

    assert evidence is not None
    assert evidence.original_text == "旧内容"
    assert evidence.baseline_text == "阿里云白练"
    assert evidence.edited_text == "阿里云百炼"


def test_edit_observer_discards_retained_target_if_it_becomes_secure():
    from vocal_more.application.dictionary_edit_observer import DictionaryEditObserver

    provider = _RetainedTargetProvider(
        [
            _snapshot(""),
            _snapshot("今天用阿里云白练测试。"),
            _snapshot("今天用阿里云百炼测试。"),
            _snapshot("另一个输入框", target_id="123:other"),
        ],
        retained_snapshot=_snapshot("", is_secure=True),
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


def test_edit_observer_uses_last_polled_edit_if_original_target_is_stale():
    from vocal_more.application.dictionary_edit_observer import DictionaryEditObserver

    provider = _RetainedTargetProvider(
        [
            _snapshot(""),
            _snapshot("今天用阿里云白练测试。"),
            _snapshot("今天用阿里云百炼测试。"),
            _snapshot("另一个输入框", target_id="123:other"),
        ],
        retained_snapshot=None,
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
        recording_id="recording-1",
        mode_name="walkie_talkie",
    )

    assert observer.observe(ticket) == _evidence()


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


def test_macos_provider_rechecks_security_before_reading_retained_target(
    monkeypatch,
):
    import AppKit

    from vocal_more.core.accessibility_text import MacOSFocusedTextProvider

    api = types.ModuleType("ApplicationServices")
    api.kAXFocusedUIElementAttribute = "focused"
    api.kAXRoleAttribute = "role"
    api.kAXSubroleAttribute = "subrole"
    api.kAXValueAttribute = "value"
    api.kAXIdentifierAttribute = "identifier"
    api.AXUIElementCreateSystemWide = lambda: "system"
    state = {"secure": False}
    requested: list[str] = []

    def copy_attribute(element, attribute, _output):
        requested.append(attribute)
        if attribute == "focused":
            return 0, "retained-element"
        if attribute == "role":
            return 0, "AXTextField"
        if attribute == "subrole":
            return 0, "AXSecureTextField" if state["secure"] else ""
        if attribute == "identifier":
            return 0, "editor"
        if attribute == "value":
            if state["secure"]:
                raise AssertionError("secure retained value must not be read")
            return 0, "今天用阿里云白练测试。"
        raise AssertionError(f"unexpected attribute for {element}: {attribute}")

    api.AXUIElementCopyAttributeValue = copy_attribute
    api.AXUIElementGetPid = lambda _element, _output: (0, 123)
    core_foundation = types.ModuleType("CoreFoundation")
    core_foundation.CFHash = lambda _element: 456

    class _RunningApplication:
        @staticmethod
        def runningApplicationWithProcessIdentifier_(_pid):
            return SimpleNamespace(
                bundleIdentifier=lambda: "com.apple.Notes",
                localizedName=lambda: "Notes",
            )

    monkeypatch.setitem(sys.modules, "ApplicationServices", api)
    monkeypatch.setitem(sys.modules, "CoreFoundation", core_foundation)
    monkeypatch.setattr(
        AppKit,
        "NSRunningApplication",
        _RunningApplication,
        raising=False,
    )

    provider = MacOSFocusedTextProvider()
    original = provider.capture_focused()
    assert original is not None

    state["secure"] = True
    requested.clear()
    final = provider.capture_target(original)

    assert final is not None
    assert final.is_secure is True
    assert final.value == ""
    assert "value" not in requested


def test_macos_provider_captures_selected_text_range(monkeypatch):
    import AppKit

    from vocal_more.core.accessibility_text import MacOSFocusedTextProvider

    api = types.ModuleType("ApplicationServices")
    api.kAXFocusedUIElementAttribute = "focused"
    api.kAXRoleAttribute = "role"
    api.kAXSubroleAttribute = "subrole"
    api.kAXValueAttribute = "value"
    api.kAXIdentifierAttribute = "identifier"
    api.kAXSelectedTextRangeAttribute = "selected-range"
    api.kAXValueCFRangeType = "cf-range"
    api.AXUIElementCreateSystemWide = lambda: "system"

    def copy_attribute(_element, attribute, _output):
        values = {
            "focused": "editor",
            "role": "AXTextArea",
            "subrole": "",
            "identifier": "editor",
            "value": "开头旧内容结尾",
            "selected-range": "range-value",
        }
        return 0, values[attribute]

    api.AXUIElementCopyAttributeValue = copy_attribute
    api.AXUIElementGetPid = lambda _element, _output: (0, 123)
    api.AXValueGetValue = lambda value, value_type, _output: (
        True,
        SimpleNamespace(location=2, length=3),
    )
    core_foundation = types.ModuleType("CoreFoundation")
    core_foundation.CFHash = lambda _element: 456

    class _RunningApplication:
        @staticmethod
        def runningApplicationWithProcessIdentifier_(_pid):
            return SimpleNamespace(
                bundleIdentifier=lambda: "com.apple.Notes",
                localizedName=lambda: "Notes",
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
    assert snapshot.selection_start == 2
    assert snapshot.selection_length == 3


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
        observation_id_factory=lambda: "observation-single",
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
    repository.enqueue.assert_not_called()
    queued = repository.enqueue_many.call_args.args[0]
    assert len(queued) == 1
    assert queued[0].observation_id == "observation-single"
    assert queued[0].candidate_index == 0
    assert queued[0].candidate_count == 1
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


def test_coordinator_atomically_queues_multiple_candidates_and_wakes_once():
    from vocal_more.application.dictionary_learning_runtime import (
        AutomaticDictionaryLearningCoordinator,
    )

    config = SimpleNamespace(
        api_key="user-key",
        dictionary_learning=SimpleNamespace(
            enabled=True,
            excluded_bundle_ids=[],
        ),
    )
    observer = MagicMock()
    observer.prepare.return_value = "observer-ticket"
    observer.observe.return_value = _evidence(
        raw_text="范UI是一定要符合Shadcn UI的。",
        pasted_text="范UI是一定要符合Shadcn UI的。",
        baseline_text="范UI是一定要符合Shadcn UI的。",
        edited_text="FanUI是一定要符合Shadcn/ui的。",
    )
    repository = MagicMock()
    queue_worker = MagicMock()
    coordinator = AutomaticDictionaryLearningCoordinator(
        config=config,
        observer_factory=MagicMock(return_value=observer),
        repository=repository,
        queue_worker=queue_worker,
        executor=_ImmediateExecutor(),
        observation_id_factory=lambda: "observation-multi",
    )

    prepared = coordinator.prepare_paste(
        raw_text="范UI是一定要符合Shadcn UI的。",
        pasted_text="范UI是一定要符合Shadcn UI的。",
        recording_id="recording-multi",
        mode_name="realtime_long",
    )
    coordinator.observe_after_paste(prepared)

    queued = repository.enqueue_many.call_args.args[0]
    assert len(queued) == 2
    assert [candidate.candidate_index for candidate in queued] == [0, 1]
    assert all(
        candidate.observation_id == "observation-multi"
        for candidate in queued
    )
    queue_worker.wake.assert_called_once_with()


def test_coordinator_exposes_monitoring_and_pending_pipeline_states(tmp_path):
    from vocal_more.application.dictionary_learning_runtime import (
        AutomaticDictionaryLearningCoordinator,
    )
    from vocal_more.infrastructure.dictionary_learning_repository import (
        DictionaryLearningRepository,
    )

    config = SimpleNamespace(
        api_key="user-key",
        dictionary_learning=SimpleNamespace(
            enabled=True,
            excluded_bundle_ids=[],
        ),
    )
    observer = MagicMock()
    observer.prepare.return_value = SimpleNamespace(
        original=_snapshot("", app_name="Notes")
    )
    observer.observe.return_value = _evidence()
    repository = DictionaryLearningRepository(
        database_path=tmp_path / "learning.sqlite3"
    )
    queue_worker = MagicMock()
    changes: list[dict] = []
    coordinator = AutomaticDictionaryLearningCoordinator(
        config=config,
        observer_factory=MagicMock(return_value=observer),
        repository=repository,
        queue_worker=queue_worker,
        executor=_ImmediateExecutor(),
        observation_id_factory=lambda: "observation-visible",
    )
    coordinator.set_on_change(changes.append)

    prepared = coordinator.prepare_paste(
        raw_text="今天用阿里云白练测试。",
        pasted_text="今天用阿里云白练测试。",
        recording_id="recording-visible",
        mode_name="walkie_talkie",
    )

    monitoring = coordinator.list_recent()
    assert monitoring[0]["status"] == "monitoring"
    assert monitoring[0]["app_name"] == "Notes"

    coordinator.observe_after_paste(prepared)

    records = coordinator.list_recent()
    assert len(records) == 1
    assert records[0]["status"] == "pending"
    assert records[0]["term"] == "百炼"
    assert records[0]["aliases"] == ["白练"]
    assert [change["status"] for change in changes] == [
        "monitoring",
        "pending",
    ]
    queue_worker.wake.assert_called_once_with()


def test_multiple_terms_are_learned_after_edit_and_immediate_focus_switch(
    tmp_path,
):
    from vocal_more.application.dictionary_edit_observer import DictionaryEditObserver
    from vocal_more.application.dictionary_learning_runtime import (
        AutomaticDictionaryLearningCoordinator,
    )
    from vocal_more.application.dictionary_learning_service import (
        DictionaryLearningProcessor,
    )
    from vocal_more.application.dictionary_service import DictionaryService
    from vocal_more.infrastructure.dictionary_learning_repository import (
        DictionaryLearningRepository,
    )
    from vocal_more.infrastructure.dictionary_repository import DictionaryRepository

    before = "范UI是一定要符合Shadcn UI的。"
    after = "FanUI是一定要符合Shadcn/ui的。"
    original = _snapshot("")
    provider = _RetainedTargetProvider(
        [
            original,
            _snapshot(before),
            _snapshot("另一个输入框", target_id="123:other"),
        ],
        retained_snapshot=_snapshot(after),
    )
    clock, sleep = _fake_time()
    observer = DictionaryEditObserver(
        provider=provider,
        observation_seconds=15.0,
        poll_interval=0.25,
        clock=clock,
        sleep=sleep,
    )
    repository = DictionaryLearningRepository(
        database_path=tmp_path / "learning.sqlite3"
    )
    queue_worker = MagicMock()
    coordinator = AutomaticDictionaryLearningCoordinator(
        config=SimpleNamespace(
            api_key="user-key",
            dictionary_learning=SimpleNamespace(
                enabled=True,
                excluded_bundle_ids=[],
            ),
        ),
        observer_factory=MagicMock(return_value=observer),
        repository=repository,
        queue_worker=queue_worker,
        executor=_ImmediateExecutor(),
        observation_id_factory=lambda: "observation-end-to-end",
    )

    prepared = coordinator.prepare_paste(
        raw_text=before,
        pasted_text=before,
        recording_id="recording-end-to-end",
        mode_name="realtime_long",
    )
    coordinator.observe_after_paste(prepared)

    queued = repository.list_jobs()
    assert len(queued) == 2
    queued.sort(key=lambda job: job.candidate_index)
    assert [job.candidate_index for job in queued] == [0, 1]
    queue_worker.wake.assert_called_once_with()

    dictionary = DictionaryService(DictionaryRepository(base_dir=tmp_path))
    model = MagicMock()
    model.classify.side_effect = [
        _decision(
            term="FanUI",
            aliases=["范UI"],
            reason_code="product_name_correction",
        ),
        _decision(
            term="Shadcn/ui",
            aliases=["Shadcn UI"],
            reason_code="technical_term_correction",
        ),
    ]
    changes: list[dict] = []
    processor = DictionaryLearningProcessor(
        repository=repository,
        dictionary=dictionary,
        model_client=model,
        on_change=changes.append,
    )

    processor.process_next(now=10**12)
    processor.process_next(now=10**12)

    assert [(entry.term, entry.aliases) for entry in dictionary.entries] == [
        ("FanUI", ["范UI"]),
        ("Shadcn/ui", ["Shadcn UI"]),
    ]
    assert [
        change for change in changes if change["status"] == "applied_group"
    ] == [
        {
            "id": "observation-end-to-end",
            "status": "applied_group",
            "terms": ["FanUI", "Shadcn/ui"],
            "source": "automatic",
            "dictionary_changed": True,
        }
    ]


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
