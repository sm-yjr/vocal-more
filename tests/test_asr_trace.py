from __future__ import annotations

from types import SimpleNamespace


def test_session_policy_reuses_only_connected_warm_session():
    from vocal_more.infrastructure.asr.session_policy import should_reuse_warm_session

    assert should_reuse_warm_session(
        supports_warm_session=True,
        is_connected=True,
        matches_model=True,
    ) is True
    assert should_reuse_warm_session(
        supports_warm_session=True,
        is_connected=False,
        matches_model=True,
    ) is False
    assert should_reuse_warm_session(
        supports_warm_session=False,
        is_connected=True,
        matches_model=True,
    ) is False
    assert should_reuse_warm_session(
        supports_warm_session=True,
        is_connected=True,
        matches_model=False,
    ) is False


def test_response_parser_accepts_completed_output_item_without_response_done():
    from vocal_more.infrastructure.asr.response_parsing import (
        extract_realtime_response_text,
    )

    text = extract_realtime_response_text(
        {
            "output": [
                {
                    "id": "item-assistant-123",
                    "status": "completed",
                    "content": [
                        {"type": "text", "text": "final text"},
                    ],
                }
            ]
        }
    )

    assert text == "final text"


def test_trace_update_keeps_response_item_id_separate_from_input_item_id():
    from vocal_more.infrastructure.asr.trace import (
        ASRDebugTrace,
        update_trace_ids_from_response,
    )

    trace = ASRDebugTrace(
        backend="realtime_ws",
        request_mode="streaming",
        model="qwen3.5-omni-plus-realtime",
        sample_rate=16000,
        audio_bytes=0,
        audio_duration_ms=0.0,
        corpus_text=None,
    )

    update_trace_ids_from_response(
        trace,
        "conversation.item.created",
        {
            "event_id": "evt-user-item",
            "item": {"id": "item-user-123"},
        },
    )
    update_trace_ids_from_response(
        trace,
        "response.text.done",
        {
            "event_id": "evt-text-done",
            "response_id": "resp-456",
            "item_id": "item-assistant-789",
            "text": "好的。",
        },
    )

    assert trace.input_item_id == "item-user-123"
    assert trace.response_output_item_id == "item-assistant-789"
    assert trace.response_id == "resp-456"


def test_trace_timings_fall_back_to_output_item_done_when_response_done_missing():
    from vocal_more.infrastructure.asr.trace import (
        ASRDebugTrace,
        build_trace_timings,
    )

    trace = ASRDebugTrace(
        backend="realtime_ws",
        request_mode="streaming",
        model="qwen3.5-omni-plus-realtime",
        sample_rate=16000,
        audio_bytes=0,
        audio_duration_ms=0.0,
        corpus_text=None,
        events=[
            {"type": "socket.open", "t_ms": 12.0},
            {"type": "response.output_item.done", "t_ms": 88.0},
            {"type": "client.result.selected", "t_ms": 91.0},
        ],
    )

    timings = build_trace_timings(trace)

    assert timings["socket_open_ms"] == 12.0
    assert timings["response_done_ms"] is None
    assert timings["response_output_item_done_ms"] == 88.0
    assert timings["response_complete_ms"] == 88.0
    assert timings["total_result_ms"] == 91.0


def test_session_policy_detects_connected_conversation_socket():
    from vocal_more.infrastructure.asr.session_policy import conversation_socket_connected

    connected = SimpleNamespace(
        ws=SimpleNamespace(sock=SimpleNamespace(connected=True))
    )
    disconnected = SimpleNamespace(
        ws=SimpleNamespace(sock=SimpleNamespace(connected=False))
    )

    assert conversation_socket_connected(connected) is True
    assert conversation_socket_connected(disconnected) is False
    assert conversation_socket_connected(SimpleNamespace(ws=None)) is True
    assert conversation_socket_connected(None) is False
