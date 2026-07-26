from __future__ import annotations


def _evidence(
    before: str,
    after: str,
):
    from vocal_more.domain.dictionary_learning_models import (
        DictionaryLearningEvidence,
    )

    return DictionaryLearningEvidence(
        raw_text=before,
        pasted_text=before,
        original_text="",
        baseline_text=before,
        edited_text=after,
        app_bundle_id="com.openai.codex",
        app_name="Codex",
        mode_name="realtime_long",
        recording_id="recording-multi",
    )


def test_plausibility_accepts_multiple_distant_small_edits():
    from vocal_more.application.dictionary_edit_observer import _is_plausible_edit

    assert (
        _is_plausible_edit(
            "范UI是一定要符合Shadcn UI的。",
            "FanUI是一定要符合Shadcn/ui的。",
        )
        is True
    )


def test_plausibility_keeps_large_rewrite_and_clear_rejections():
    from vocal_more.application.dictionary_edit_observer import _is_plausible_edit

    assert _is_plausible_edit("一段需要保留的听写内容", "") is False
    assert (
        _is_plausible_edit(
            "一段需要保留的听写内容",
            "这已经变成完全不同的长句子",
        )
        is False
    )


def test_splitter_extracts_two_candidates_from_fanui_example():
    from vocal_more.application.dictionary_learning_candidates import (
        split_dictionary_learning_evidence,
    )

    candidates = split_dictionary_learning_evidence(
        _evidence(
            "范UI是一定要符合Shadcn UI的。",
            "FanUI是一定要符合Shadcn/ui的。",
        ),
        observation_id="observation-1",
    )

    assert len(candidates) == 2
    assert [candidate.observation_id for candidate in candidates] == [
        "observation-1",
        "observation-1",
    ]
    assert [candidate.candidate_index for candidate in candidates] == [0, 1]
    assert [candidate.candidate_count for candidate in candidates] == [2, 2]
    assert "范UI" in candidates[0].candidate_before_text
    assert "FanUI" in candidates[0].candidate_after_text
    assert "Shadcn UI" in candidates[1].candidate_before_text
    assert "Shadcn/ui" in candidates[1].candidate_after_text


def test_splitter_merges_nearby_hunks_belonging_to_one_candidate():
    from vocal_more.application.dictionary_learning_candidates import (
        split_dictionary_learning_evidence,
    )

    candidates = split_dictionary_learning_evidence(
        _evidence("abXcdYef", "ab1cd2ef"),
        observation_id="observation-nearby",
    )

    assert len(candidates) == 1
    assert candidates[0].candidate_before_text == "abXcdYef"
    assert candidates[0].candidate_after_text == "ab1cd2ef"


def test_splitter_filters_punctuation_only_and_reverted_edits():
    from vocal_more.application.dictionary_learning_candidates import (
        split_dictionary_learning_evidence,
    )

    assert (
        split_dictionary_learning_evidence(
            _evidence("你好。", "你好！"),
            observation_id="observation-punctuation",
        )
        == []
    )
    assert (
        split_dictionary_learning_evidence(
            _evidence("FanUI", "FanUI"),
            observation_id="observation-reverted",
        )
        == []
    )


def test_splitter_deduplicates_repeated_identical_mappings():
    from vocal_more.application.dictionary_learning_candidates import (
        split_dictionary_learning_evidence,
    )

    candidates = split_dictionary_learning_evidence(
        _evidence(
            "Cloud Code----Cloud Code",
            "Claude Code----Claude Code",
        ),
        observation_id="observation-duplicate",
    )

    assert len(candidates) == 1


def test_splitter_rejects_instead_of_truncating_more_than_five_candidates():
    from vocal_more.application.dictionary_learning_candidates import (
        split_dictionary_learning_evidence,
    )

    candidates = split_dictionary_learning_evidence(
        _evidence(
            "a----b----c----d----e----f",
            "A----B----C----D----E----F",
        ),
        observation_id="observation-too-many",
    )

    assert candidates == []
