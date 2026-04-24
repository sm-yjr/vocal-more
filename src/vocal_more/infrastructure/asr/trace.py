"""Trace DTOs and helpers for ASR debug logs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ASRDebugTrace:
    """Debug trace for a single ASR request."""

    backend: str
    request_mode: str
    model: str
    sample_rate: int
    audio_bytes: int
    audio_duration_ms: float
    corpus_text: Optional[str]
    session_id: str = ""
    conversation_id: str = ""
    input_item_id: str = ""
    response_id: str = ""
    response_output_item_id: str = ""
    service_request_id: str = ""
    completion_id: str = ""
    server_event_ids: dict[str, str] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    partial_texts: list[str] = field(default_factory=list)
    final_transcripts: list[str] = field(default_factory=list)
    result_text: str = ""
    result_source: str = ""
    usage: dict = field(default_factory=dict)
    billing: dict = field(default_factory=dict)
    timings_ms: dict[str, Optional[float]] = field(default_factory=dict)
    response_requested: bool = False
    warm_session_reused: bool = False
    fallback_reason: str = ""
    recognition_timed_out: bool = False
    cleanup_timed_out: bool = False
    error: str = ""


def event_time_ms(trace: ASRDebugTrace, event_type: str) -> Optional[float]:
    for event in trace.events:
        if event.get("type") == event_type:
            return event.get("t_ms")
    return None


def event_id_for(trace: ASRDebugTrace, event_type: str) -> str:
    return trace.server_event_ids.get(event_type, "")


def update_trace_ids_from_response(
    trace: Optional[ASRDebugTrace],
    event_type: str,
    response: dict,
) -> dict:
    """Extract stable identifiers from a realtime event."""
    payload: dict = {}
    if trace is None:
        return payload

    event_id = response.get("event_id")
    if event_id:
        payload["event_id"] = event_id
        trace.server_event_ids.setdefault(event_type, str(event_id))

    session = response.get("session")
    if isinstance(session, dict):
        session_id = session.get("id")
        if session_id:
            trace.session_id = str(session_id)
            payload.setdefault("session_id", trace.session_id)

    top_level_response_id = response.get("response_id")
    if top_level_response_id:
        trace.response_id = str(top_level_response_id)
        payload.setdefault("response_id", trace.response_id)

    response_obj = response.get("response")
    if isinstance(response_obj, dict):
        response_id = response_obj.get("id")
        if response_id:
            trace.response_id = str(response_id)
            payload.setdefault("response_id", trace.response_id)

        conversation_id = response_obj.get("conversation_id")
        if conversation_id:
            trace.conversation_id = str(conversation_id)
            payload.setdefault("conversation_id", trace.conversation_id)

        output_items = response_obj.get("output")
        if isinstance(output_items, list):
            for item in output_items:
                if isinstance(item, dict) and item.get("id"):
                    trace.response_output_item_id = str(item["id"])
                    payload.setdefault(
                        "response_output_item_id",
                        trace.response_output_item_id,
                    )
                    break

    if event_type == "conversation.item.created":
        item = response.get("item")
        if isinstance(item, dict):
            item_id = item.get("id")
            if item_id:
                trace.input_item_id = str(item_id)
                payload.setdefault("input_item_id", trace.input_item_id)

    item_id = response.get("item_id")
    if item_id and (
        event_type.startswith("conversation.item.")
        or event_type.startswith("input_audio_buffer.")
    ):
        trace.input_item_id = str(item_id)
        payload.setdefault("input_item_id", trace.input_item_id)
    elif item_id and event_type.startswith("response."):
        trace.response_output_item_id = str(item_id)
        payload.setdefault(
            "response_output_item_id",
            trace.response_output_item_id,
        )

    if event_type.startswith("response.output_item."):
        item = response.get("item")
        if isinstance(item, dict) and item.get("id"):
            trace.response_output_item_id = str(item["id"])
            payload.setdefault(
                "response_output_item_id",
                trace.response_output_item_id,
            )

    output_index = response.get("output_index")
    if output_index is not None:
        payload["output_index"] = output_index

    return payload


def update_trace_usage_from_response(
    trace: Optional[ASRDebugTrace],
    response: dict,
) -> None:
    """Capture usage payloads from realtime response events."""
    if trace is None:
        return
    response_obj = response.get("response")
    if not isinstance(response_obj, dict):
        return
    usage = response_obj.get("usage")
    if isinstance(usage, dict):
        trace.usage = dict(usage)


def build_trace_timings(trace: ASRDebugTrace) -> dict[str, Optional[float]]:
    response_done_ms = event_time_ms(trace, "response.done")
    response_output_item_done_ms = event_time_ms(trace, "response.output_item.done")
    timings = {
        "socket_open_ms": event_time_ms(trace, "socket.open"),
        "session_ready_ms": event_time_ms(trace, "session.updated"),
        "first_partial_ms": event_time_ms(
            trace, "conversation.item.input_audio_transcription.text"
        ),
        "transcription_complete_ms": event_time_ms(
            trace, "conversation.item.input_audio_transcription.completed"
        ),
        "commit_ms": event_time_ms(trace, "client.commit"),
        "response_requested_ms": event_time_ms(trace, "client.response.requested"),
        "response_first_delta_ms": event_time_ms(trace, "response.text.delta"),
        "response_text_done_ms": event_time_ms(trace, "response.text.done"),
        "response_output_item_done_ms": response_output_item_done_ms,
        "response_done_ms": response_done_ms,
        "result_selected_ms": event_time_ms(trace, "client.result.selected"),
        "socket_close_ms": event_time_ms(trace, "socket.close"),
    }
    timings["response_complete_ms"] = response_done_ms or response_output_item_done_ms
    timings["total_result_ms"] = timings["result_selected_ms"]
    return timings


def set_trace_service_request_id(
    trace: Optional[ASRDebugTrace],
    request_id: object,
) -> None:
    if trace is None or request_id in (None, ""):
        return
    trace.service_request_id = str(request_id)


def update_trace_ids_from_openai_stream(
    trace: Optional[ASRDebugTrace],
    stream: object,
) -> None:
    """Extract server identifiers from an OpenAI-compatible streaming response."""
    if trace is None:
        return

    request_id = getattr(stream, "request_id", None)
    if request_id:
        set_trace_service_request_id(trace, request_id)
        return

    response = getattr(stream, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return

    for header_name in ("x-request-id", "x-dashscope-request-id", "x-acs-request-id"):
        header_value = headers.get(header_name)
        if header_value:
            set_trace_service_request_id(trace, header_value)
            return


def update_trace_ids_from_openai_chunk(
    trace: Optional[ASRDebugTrace],
    chunk: object,
) -> None:
    """Extract stable identifiers from an OpenAI-compatible stream chunk."""
    if trace is None:
        return

    set_trace_service_request_id(trace, getattr(chunk, "_request_id", None))

    completion_id = getattr(chunk, "id", None)
    if completion_id:
        trace.completion_id = str(completion_id)


def finalize_trace(trace: ASRDebugTrace, result_source: str) -> None:
    trace.result_source = result_source
    trace.timings_ms = build_trace_timings(trace)


def print_trace_summary(trace: ASRDebugTrace) -> None:
    total = trace.timings_ms.get("total_result_ms")
    ready = trace.timings_ms.get("session_ready_ms")
    transcript = trace.timings_ms.get("transcription_complete_ms")
    response_done = trace.timings_ms.get("response_complete_ms")
    print(
        "[ASRTiming] "
        f"mode={trace.request_mode} backend={trace.backend} model={trace.model} "
        f"source={trace.result_source or 'unknown'} warm={trace.warm_session_reused} "
        f"total_ms={total} ready_ms={ready} transcript_ms={transcript} "
        f"response_ms={response_done}"
    )
    print(
        "[ASRIds] "
        f"mode={trace.request_mode} model={trace.model} "
        f"service_request_id={trace.service_request_id or '-'} "
        f"completion_id={trace.completion_id or '-'} "
        f"session_id={trace.session_id or '-'} "
        f"conversation_id={trace.conversation_id or '-'} "
        f"input_item_id={trace.input_item_id or '-'} "
        f"response_id={trace.response_id or '-'} "
        f"response_output_item_id={trace.response_output_item_id or '-'} "
        f"error={trace.error or '-'}"
    )


__all__ = [
    "ASRDebugTrace",
    "build_trace_timings",
    "event_id_for",
    "event_time_ms",
    "finalize_trace",
    "print_trace_summary",
    "set_trace_service_request_id",
    "update_trace_ids_from_openai_chunk",
    "update_trace_ids_from_openai_stream",
    "update_trace_ids_from_response",
    "update_trace_usage_from_response",
]
