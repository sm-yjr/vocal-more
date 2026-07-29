"""ASR engine module using DashScope Qwen ASR models."""

import base64
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import tempfile
import threading
import time
import wave
from dataclasses import asdict, dataclass
from typing import Any, Callable, Optional

import dashscope
from dashscope import MultiModalConversation
from dashscope.audio.qwen_omni import (
    MultiModality,
    OmniRealtimeCallback,
    OmniRealtimeConversation,
)
from dashscope.audio.qwen_omni.omni_realtime import TranscriptionParams

from ..config import get_asr_model_info, get_config
from ..dictionary import build_asr_corpus_text, normalize_terms
from ..infrastructure.asr.batch_engine import (
    analysis_window_frames as _batch_analysis_window_frames,
    audio_bytes_per_second as _batch_audio_bytes_per_second,
    audio_duration_seconds as _batch_audio_duration_seconds,
    find_silence_aware_chunk_end as _batch_find_silence_aware_chunk_end,
    frame_bytes as _batch_frame_bytes,
    silence_search_frames as _batch_silence_search_frames,
    split_audio_for_batch as _batch_split_audio_for_batch,
    window_rms as _batch_window_rms,
)
from ..infrastructure.asr.response_parsing import (
    extract_realtime_response_text as _extract_text_from_realtime_response,
    extract_text_from_content_part as _extract_text_from_content_part,
    extract_text_from_realtime_item as _extract_text_from_realtime_item,
    prefer_longer_text as _prefer_longer_text,
)
from ..infrastructure.asr.routing import (
    direct_offline_fallback_model as _routing_direct_offline_fallback_model,
    join_transcript_segments as _routing_join_transcript_segments,
    omni_offline_fallback_model as _routing_omni_offline_fallback_model,
)
from ..infrastructure.asr.session_policy import (
    conversation_socket_connected as _conversation_socket_connected,
    should_reuse_warm_session,
    supports_warm_realtime_session as _supports_warm_realtime_session,
)
from ..infrastructure.asr.streaming_engine import (
    adaptive_response_complete_timeout as _streaming_adaptive_response_complete_timeout,
    adaptive_response_start_timeout as _streaming_adaptive_response_start_timeout,
)
from ..infrastructure.asr.trace import (
    ASRDebugTrace,
    build_trace_timings as _build_trace_timings,
    event_id_for as _event_id_for,
    event_time_ms as _event_time_ms,
    finalize_trace as _finalize_trace,
    print_trace_summary as _print_trace_summary,
    set_trace_service_request_id as _set_trace_service_request_id,
    update_trace_ids_from_openai_chunk as _update_trace_ids_from_openai_chunk,
    update_trace_ids_from_openai_stream as _update_trace_ids_from_openai_stream,
    update_trace_ids_from_response as _update_trace_ids_from_response,
    update_trace_usage_from_response as _update_trace_usage_from_response,
)
from ..infrastructure.pricing import (
    build_asr_billing,
    extract_usage_from_response as _extract_usage_from_response,
)
from .text_polisher import (
    TextPolisher,
    build_omni_inline_polish_instructions,
    normalize_structured_list_spacing,
)

REALTIME_CHUNK_SIZE = 3200
# The current public docs list qwen3-asr-flash as supporting audio up to 3 minutes / 10 MB.
SHORT_FILE_MAX_DURATION_SECONDS = 180
SHORT_FILE_MAX_BYTES = 10 * 1024 * 1024
OMNI_OFFLINE_CHUNK_DURATION_SECONDS = 180
OMNI_REALTIME_DIRECT_OFFLINE_THRESHOLD_SECONDS = 240.0
OMNI_OFFLINE_SILENCE_SEARCH_SECONDS = 12.0
OMNI_OFFLINE_SILENCE_WINDOW_SECONDS = 0.25
OMNI_OFFLINE_SILENCE_RMS_THRESHOLD = 0.015
INLINE_RESPONSE_START_TIMEOUT_SECONDS = 3.0
INLINE_RESPONSE_TRANSCRIPT_TIMEOUT_SECONDS = 5.0
INLINE_RESPONSE_LATE_START_GRACE_SECONDS = 1.0
WARM_KEEPER_CHECK_INTERVAL_SECONDS = 5.0
WARM_KEEPER_MAX_IDLE_SECONDS = 600.0
WARM_KEEPER_SHUTDOWN_TIMEOUT_SECONDS = 0.25
MAX_ADAPTIVE_RESPONSE_START_TIMEOUT_SECONDS = 20.0
MAX_ADAPTIVE_RESPONSE_COMPLETE_TIMEOUT_SECONDS = 90.0
STREAMING_AUDIO_QUEUE_TARGET_SECONDS = 6.4
STREAMING_AUDIO_QUEUE_MIN_CHUNKS = 8
STREAMING_AUDIO_QUEUE_MAX_CHUNKS = 160
MIN_AUDIO_QUEUE_DRAIN_TIMEOUT_SECONDS = 2.0
MAX_AUDIO_QUEUE_DRAIN_TIMEOUT_SECONDS = 10.0
AUDIO_QUEUE_DRAIN_HEADROOM_SECONDS = 1.0
AUDIO_QUEUE_DRAIN_MULTIPLIER = 2.0

_AUDIO_QUEUE_STOP = object()
_CALLBACK_EVENT_STOP = object()
_THREAD_CLASS = threading.Thread


def _apply_dashscope_api_key(config=None) -> None:
    config = config or get_config()
    dashscope.api_key = config.api_key or None


def _get_corpus_text() -> Optional[str]:
    config = get_config()
    if not config.asr.use_dictionary_corpus:
        return None
    corpus_text = build_asr_corpus_text().strip()
    return corpus_text or None


def _resolve_asr_language(
    config=None,
    language_override: Optional[str] = None,
) -> Optional[str]:
    config = config or get_config()
    language = language_override or config.asr.language
    return None if language == "auto" else language


def _build_transcription_params(
    config=None,
    language_override: Optional[str] = None,
) -> TranscriptionParams:
    config = config or get_config()
    return TranscriptionParams(
        language=_resolve_asr_language(
            config=config,
            language_override=language_override,
        ),
        sample_rate=config.audio.sample_rate,
        input_audio_format="pcm",
        corpus_text=_get_corpus_text(),
    )


def _build_session_kwargs(
    model_info: Optional[dict],
    config=None,
    language_override: Optional[str] = None,
    context_instruction: str = "",
) -> dict:
    config = config or get_config()
    session_kwargs: dict = dict(
        output_modalities=[MultiModality.TEXT],
        enable_input_audio_transcription=True,
        enable_turn_detection=False,
    )
    if model_info and model_info.get("input_audio_transcription_model") is not None:
        # Omni models require a voice even for text-only output.
        session_kwargs["voice"] = "Tina"
        session_kwargs["input_audio_transcription_model"] = model_info[
            "input_audio_transcription_model"
        ]
        if config.enable_polish and model_info.get("handles_inline_polish"):
            session_kwargs["instructions"] = build_omni_inline_polish_instructions(
                config.llm,
                context_instruction=context_instruction,
            )
    else:
        session_kwargs["transcription_params"] = _build_transcription_params(
            config=config,
            language_override=language_override,
        )
    return session_kwargs


def _should_request_inline_polish(model_info: Optional[dict], transcript: str) -> bool:
    config = get_config()
    if not (config.enable_polish and model_info and model_info.get("handles_inline_polish")):
        return False
    return bool(normalize_terms(transcript).strip())


def _should_start_inline_response_now(
    model_info: Optional[dict],
    _callback,
) -> bool:
    """Decide whether to issue response.create immediately after commit."""
    config = get_config()
    if not (config.enable_polish and model_info and model_info.get("handles_inline_polish")):
        return False
    return True


def _get_omni_offline_fallback_model(model_id: str) -> Optional[str]:
    return _routing_omni_offline_fallback_model(model_id)


def _extract_multimodal_text(response) -> str:
    content = response.output.choices[0].message.content
    if isinstance(content, str):
        return content.strip()

    texts = []
    for item in content or []:
        if isinstance(item, str):
            texts.append(item)
        elif isinstance(item, dict) and "text" in item:
            texts.append(str(item["text"]))

    return "".join(texts).strip()


def _debug_dir() -> Optional[Path]:
    raw = os.environ.get("VOCAL_MORE_DEBUG_DIR", "").strip()
    if not raw:
        return None
    path = Path(os.path.expanduser(raw))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _pcm_duration_seconds(audio_data: Optional[bytes], sample_rate: int, channels: int) -> float:
    if not audio_data:
        return 0.0
    bytes_per_second = sample_rate * max(1, channels) * 2
    if bytes_per_second <= 0:
        return 0.0
    return len(audio_data) / bytes_per_second


def _streaming_audio_chunk_bytes(config=None) -> int:
    config = config or get_config()
    return max(1, config.audio.blocksize) * max(1, config.audio.channels) * 2


def _streaming_audio_chunk_duration_seconds(config=None) -> float:
    config = config or get_config()
    sample_rate = max(1, config.audio.sample_rate)
    return max(1, config.audio.blocksize) / sample_rate


def _streaming_audio_queue_max_chunks(config=None) -> int:
    chunk_duration = _streaming_audio_chunk_duration_seconds(config)
    if chunk_duration <= 0:
        return STREAMING_AUDIO_QUEUE_MAX_CHUNKS
    target_chunks = int(STREAMING_AUDIO_QUEUE_TARGET_SECONDS / chunk_duration)
    if STREAMING_AUDIO_QUEUE_TARGET_SECONDS % chunk_duration:
        target_chunks += 1
    return max(
        STREAMING_AUDIO_QUEUE_MIN_CHUNKS,
        min(STREAMING_AUDIO_QUEUE_MAX_CHUNKS, target_chunks),
    )


def _audio_queue_drain_timeout_seconds(pending_chunks: int, config=None) -> float:
    if pending_chunks <= 0:
        return MIN_AUDIO_QUEUE_DRAIN_TIMEOUT_SECONDS
    pending_seconds = pending_chunks * _streaming_audio_chunk_duration_seconds(config)
    timeout = AUDIO_QUEUE_DRAIN_HEADROOM_SECONDS + pending_seconds * AUDIO_QUEUE_DRAIN_MULTIPLIER
    return max(
        MIN_AUDIO_QUEUE_DRAIN_TIMEOUT_SECONDS,
        min(MAX_AUDIO_QUEUE_DRAIN_TIMEOUT_SECONDS, timeout),
    )


def _adaptive_response_start_timeout(duration_seconds: float) -> float:
    return _streaming_adaptive_response_start_timeout(
        duration_seconds,
        start_timeout_seconds=INLINE_RESPONSE_START_TIMEOUT_SECONDS,
        max_timeout_seconds=MAX_ADAPTIVE_RESPONSE_START_TIMEOUT_SECONDS,
    )


def _adaptive_response_complete_timeout(
    duration_seconds: float,
    *,
    base_timeout: float = 30.0,
) -> float:
    return _streaming_adaptive_response_complete_timeout(
        duration_seconds,
        base_timeout=base_timeout,
        max_timeout_seconds=MAX_ADAPTIVE_RESPONSE_COMPLETE_TIMEOUT_SECONDS,
    )


def _get_direct_offline_fallback_model(
    model_id: str,
    duration_seconds: float,
) -> Optional[str]:
    return _routing_direct_offline_fallback_model(
        model_id,
        duration_seconds,
        threshold_seconds=OMNI_REALTIME_DIRECT_OFFLINE_THRESHOLD_SECONDS,
    )


def _join_transcript_segments(segments: list[str]) -> str:
    return _routing_join_transcript_segments(segments)


def _format_trace_ids(trace: Optional[ASRDebugTrace]) -> str:
    if trace is None:
        return (
            "service_request_id=- completion_id=- session_id=- input_item_id=- "
            "response_id=- transcript_event_id=- response_created_event_id=- "
            "response_done_event_id=-"
        )
    return (
        f"service_request_id={trace.service_request_id or '-'} "
        f"completion_id={trace.completion_id or '-'} "
        f"session_id={trace.session_id or '-'} "
        f"input_item_id={trace.input_item_id or '-'} "
        f"response_id={trace.response_id or '-'} "
        f"transcript_event_id={_event_id_for(trace, 'conversation.item.input_audio_transcription.completed') or '-'} "
        f"response_created_event_id={_event_id_for(trace, 'response.created') or '-'} "
        f"response_text_done_event_id={_event_id_for(trace, 'response.text.done') or '-'} "
        f"response_output_item_done_event_id={_event_id_for(trace, 'response.output_item.done') or '-'} "
        f"response_done_event_id={_event_id_for(trace, 'response.done') or '-'}"
    )


def _get_completed_response_result(callback) -> tuple[str, str]:
    response_text = callback.get_response_text().strip()
    response_source = callback.get_response_result_source()
    if response_text and response_source:
        return response_text, response_source
    return "", ""


def _try_finalize_late_response(callback, response_complete_timeout: float) -> tuple[str, str, bool]:
    late_started = callback.wait_for_response_started(
        timeout=INLINE_RESPONSE_LATE_START_GRACE_SECONDS
    )
    if not late_started:
        return "", "", False

    late_completed = callback.wait_for_response_complete(timeout=response_complete_timeout)
    if not late_completed:
        return "", "", True

    response_text, response_source = _get_completed_response_result(callback)
    if response_text and response_source:
        return response_text, response_source, True
    return "", "", True


@dataclass
class ASRResult:
    """ASR recognition result."""

    text: str
    is_final: bool
    sentence_id: int = 0


class BatchASRCallback(OmniRealtimeCallback):
    """Callback handler for batch ASR recognition."""

    def __init__(
        self,
        on_text: Optional[Callable[[str], None]] = None,
        on_final: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_complete: Optional[Callable[[], None]] = None,
    ):
        self._on_text = on_text
        self._on_final = on_final
        self._on_error = on_error
        self._on_complete = on_complete

        self._full_text = ""
        self._lock = threading.Lock()
        self._session_finished = threading.Event()
        self._session_updated = threading.Event()
        self._transcription_completed = threading.Event()
        self._response_completed = threading.Event()
        self._response_started = threading.Event()
        self._started_at = time.perf_counter()
        self._debug_trace: Optional[ASRDebugTrace] = None
        self._response_text = ""
        self._response_done_received = False
        self._response_done_status = ""
        self._response_text_done_received = False
        self._response_content_part_done_received = False
        self._response_output_item_done_received = False
        self._response_output_item_status = ""

    def set_debug_trace(self, trace: ASRDebugTrace) -> None:
        self._debug_trace = trace

    def _record_event(self, event_type: str, **payload) -> None:
        if self._debug_trace is None:
            return

        event = {
            "t_ms": round((time.perf_counter() - self._started_at) * 1000, 2),
            "type": event_type,
        }
        if payload:
            event.update(payload)
        self._debug_trace.events.append(event)

    def mark_client_event(self, event_type: str, **payload) -> None:
        self._record_event(event_type, **payload)

    def on_open(self):
        print("[ASRCallback] Connection opened")
        self._record_event("socket.open")

    def on_close(self, code, msg):
        print(f"[ASRCallback] Connection closed: code={code}, msg={msg}")
        self._session_finished.set()
        self._session_updated.set()
        self._response_started.set()
        self._response_completed.set()
        self._record_event("socket.close", code=code, msg=msg)
        if self._on_complete:
            self._on_complete()

    def on_event(self, response):
        try:
            event_type = response.get("type", "")
            print(f"[ASRCallback] Event: {event_type}")
            metadata = _update_trace_ids_from_response(
                self._debug_trace,
                event_type,
                response,
            )
            self._record_event(event_type, **metadata)

            if event_type == "session.created":
                session_id = response.get("session", {}).get("id", "unknown")
                print(f"[ASRCallback] Session created: {session_id}")

            elif event_type == "session.updated":
                print("[ASRCallback] Session updated, ready to receive audio")
                self._session_updated.set()

            elif event_type == "conversation.item.input_audio_transcription.text":
                text = response.get("text", "") or response.get("stash", "")
                if text and self._debug_trace is not None:
                    self._debug_trace.partial_texts.append(text)
                    self._record_event(event_type, **metadata, text=text)
                if text and self._on_text:
                    self._on_text(text)

            elif event_type == "conversation.item.input_audio_transcription.completed":
                transcript = response.get("transcript", "")
                print(f"[ASRCallback] Final transcript: {transcript}")
                with self._lock:
                    self._full_text += transcript
                self._transcription_completed.set()
                if transcript and self._debug_trace is not None:
                    self._debug_trace.final_transcripts.append(transcript)
                    self._record_event(event_type, **metadata, transcript=transcript)
                if self._on_final:
                    self._on_final(transcript)

            elif event_type == "response.text.delta":
                delta = response.get("delta", "")
                if delta:
                    self._response_started.set()
                    with self._lock:
                        self._response_text += delta
                    self._record_event(event_type, **metadata, delta=delta)

            elif event_type == "response.text.done":
                text = response.get("text", "")
                self._response_started.set()
                self._response_text_done_received = True
                with self._lock:
                    self._response_text = _prefer_longer_text(
                        self._response_text,
                        text,
                    )
                self._record_event(event_type, **metadata, text=text)

            elif event_type == "response.content_part.done":
                part = response.get("part")
                part_text = _extract_text_from_content_part(part)
                self._response_started.set()
                self._response_content_part_done_received = True
                with self._lock:
                    self._response_text = _prefer_longer_text(
                        self._response_text,
                        part_text,
                    )
                if part_text:
                    self._record_event(event_type, **metadata, text=part_text)

            elif event_type == "response.output_item.done":
                item = response.get("item")
                item_text = _extract_text_from_realtime_item(item)
                status = ""
                if isinstance(item, dict):
                    status = str(item.get("status", "") or "")
                self._response_started.set()
                self._response_output_item_done_received = True
                self._response_output_item_status = status
                with self._lock:
                    self._response_text = _prefer_longer_text(
                        self._response_text,
                        item_text,
                    )
                self._response_completed.set()
                self._record_event(event_type, **metadata, status=status, text=item_text)

            elif event_type == "response.created":
                self._response_started.set()

            elif event_type == "response.done":
                self._response_started.set()
                self._response_done_received = True
                _update_trace_usage_from_response(self._debug_trace, response)
                response_obj = response.get("response")
                response_status = ""
                if isinstance(response_obj, dict):
                    response_status = str(response_obj.get("status", "") or "")
                self._response_done_status = response_status
                with self._lock:
                    self._response_text = _prefer_longer_text(
                        self._response_text,
                        _extract_text_from_realtime_response(response_obj),
                    )
                self._response_completed.set()
                self._record_event(event_type, status=response_status)

            elif event_type == "session.finished":
                transcript = response.get("transcript", "")
                print(f"[ASRCallback] Session finished, transcript: {transcript}")
                if transcript:
                    with self._lock:
                        if not self._full_text:
                            self._full_text = transcript
                    if self._debug_trace is not None:
                        self._debug_trace.final_transcripts.append(transcript)
                self._session_finished.set()
                self._record_event(event_type, **metadata, transcript=transcript)

            elif event_type == "error":
                error_msg = response.get("error", {}).get("message", str(response))
                print(f"[ASRCallback] Error: {error_msg}")
                self._transcription_completed.set()
                self._record_event(event_type, **metadata, error=error_msg)
                if self._on_error:
                    self._on_error(error_msg)

        except Exception as e:
            print(f"[ASRCallback] Exception in on_event: {e}")
            if self._on_error:
                self._on_error(str(e))

    def get_full_text(self) -> str:
        with self._lock:
            return self._full_text

    def wait_for_session_updated(self, timeout: float = 10.0) -> bool:
        return self._session_updated.wait(timeout=timeout)

    def wait_for_transcription_complete(self, timeout: float = 30.0) -> bool:
        return self._transcription_completed.wait(timeout=timeout)

    def wait_for_finish(self, timeout: float = 30.0) -> bool:
        return self._session_finished.wait(timeout=timeout)

    def wait_for_response_complete(self, timeout: float = 30.0) -> bool:
        return self._response_completed.wait(timeout=timeout)

    def wait_for_response_started(self, timeout: float = 30.0) -> bool:
        return self._response_started.wait(timeout=timeout)

    def get_response_text(self) -> str:
        with self._lock:
            return self._response_text

    def did_receive_response_done(self) -> bool:
        return self._response_done_received

    def did_receive_response_text_done(self) -> bool:
        return self._response_text_done_received

    def did_receive_response_output_item_done(self) -> bool:
        return self._response_output_item_done_received

    def get_response_output_item_status(self) -> str:
        return self._response_output_item_status

    def get_response_done_status(self) -> str:
        return self._response_done_status

    def get_response_result_source(self) -> str:
        response_text = self.get_response_text().strip()
        if not response_text:
            return ""
        if self._response_done_received and self._response_done_status in ("", "completed"):
            return "response"
        if (
            self._response_text_done_received
            and self._response_output_item_done_received
            and self._response_output_item_status in ("", "completed")
        ):
            return "response_output_item_done"
        return ""

    def reset(self):
        with self._lock:
            self._full_text = ""
        self._session_finished.clear()
        self._session_updated.clear()
        self._transcription_completed.clear()
        self._response_text = ""
        self._response_done_received = False
        self._response_done_status = ""
        self._response_text_done_received = False
        self._response_content_part_done_received = False
        self._response_output_item_done_received = False
        self._response_output_item_status = ""
        self._response_started.clear()
        self._response_completed.clear()
        self._started_at = time.perf_counter()


class BatchASREngine:
    """Batch ASR for processing complete audio files."""

    def __init__(self):
        self.config = get_config()
        _apply_dashscope_api_key(self.config)
        self._last_metering: dict[str, Any] | None = None
        self._context_instruction = ""

    def _frame_bytes(self) -> int:
        return _batch_frame_bytes(self.config.audio.channels)

    def _audio_bytes_per_second(self) -> int:
        return _batch_audio_bytes_per_second(
            self.config.audio.sample_rate,
            self.config.audio.channels,
        )

    def _audio_duration_seconds(self, audio_data: bytes) -> float:
        return _batch_audio_duration_seconds(
            audio_data,
            self.config.audio.sample_rate,
            self.config.audio.channels,
        )

    def _analysis_window_frames(self) -> int:
        return _batch_analysis_window_frames(
            self.config.audio.sample_rate,
            OMNI_OFFLINE_SILENCE_WINDOW_SECONDS,
        )

    def _silence_search_frames(self) -> int:
        return _batch_silence_search_frames(
            self.config.audio.sample_rate,
            OMNI_OFFLINE_SILENCE_SEARCH_SECONDS,
        )

    def _window_rms(
        self,
        samples: memoryview,
        *,
        frame_start: int,
        frame_end: int,
    ) -> float:
        return _batch_window_rms(
            samples,
            channels=self.config.audio.channels,
            frame_start=frame_start,
            frame_end=frame_end,
        )

    def _find_silence_aware_chunk_end(
        self,
        samples: memoryview,
        *,
        start_frame: int,
        target_end_frame: int,
        total_frames: int,
    ) -> int:
        return _batch_find_silence_aware_chunk_end(
            samples,
            sample_rate=self.config.audio.sample_rate,
            channels=self.config.audio.channels,
            silence_window_seconds=OMNI_OFFLINE_SILENCE_WINDOW_SECONDS,
            silence_search_seconds=OMNI_OFFLINE_SILENCE_SEARCH_SECONDS,
            silence_rms_threshold=OMNI_OFFLINE_SILENCE_RMS_THRESHOLD,
            start_frame=start_frame,
            target_end_frame=target_end_frame,
            total_frames=total_frames,
        )

    def _split_audio_for_batch(
        self,
        audio_data: bytes,
        *,
        max_duration_seconds: int = OMNI_OFFLINE_CHUNK_DURATION_SECONDS,
    ) -> list[bytes]:
        return _batch_split_audio_for_batch(
            audio_data,
            sample_rate=self.config.audio.sample_rate,
            channels=self.config.audio.channels,
            max_duration_seconds=max_duration_seconds,
            silence_window_seconds=OMNI_OFFLINE_SILENCE_WINDOW_SECONDS,
            silence_search_seconds=OMNI_OFFLINE_SILENCE_SEARCH_SECONDS,
            silence_rms_threshold=OMNI_OFFLINE_SILENCE_RMS_THRESHOLD,
        )

    def _transcribe_chunked_audio(
        self,
        audio_data: bytes,
        *,
        model: str,
        language_override: Optional[str],
        context_instruction: str = "",
    ) -> str:
        chunks = self._split_audio_for_batch(audio_data)
        if len(chunks) <= 1:
            kwargs = {"model_override": model}
            if context_instruction:
                kwargs["context_instruction"] = context_instruction
            return self._transcribe_omni_offline(audio_data, **kwargs)

        print(
            f"[BatchASR] Long audio detected for {model}; "
            f"splitting into {len(chunks)} chunks for retry-safe transcription"
        )

        transcripts: list[str] = []
        bytes_per_second = self._audio_bytes_per_second()
        for index, chunk in enumerate(chunks, start=1):
            duration_seconds = (len(chunk) / bytes_per_second) if bytes_per_second else 0.0
            print(
                f"[BatchASR] Transcribing chunk {index}/{len(chunks)} "
                f"({duration_seconds:.1f}s, {len(chunk)} bytes)"
            )
            try:
                kwargs = {
                    "model_override": model,
                    "language_override": language_override,
                    "allow_chunking": False,
                }
                if context_instruction:
                    kwargs["context_instruction"] = context_instruction
                chunk_text = self.transcribe(chunk, **kwargs)
            except Exception as exc:
                raise RuntimeError(
                    f"Chunk {index}/{len(chunks)} transcription failed: {exc}"
                ) from exc

            if chunk_text.strip():
                transcripts.append(chunk_text.strip())

        return _join_transcript_segments(transcripts)

    def transcribe(
        self,
        audio_data: bytes,
        model_override: Optional[str] = None,
        language_override: Optional[str] = None,
        *,
        allow_chunking: bool = True,
        context_instruction: str = "",
    ) -> str:
        """Transcribe complete audio data.

        Routing is based on the selected model's catalog ``transport`` field
        rather than a separate backend string.
        """
        _apply_dashscope_api_key(self.config)
        self._last_metering = None
        model = model_override or self.config.asr.model
        model_info = get_asr_model_info(model)
        transport = model_info["transport"] if model_info else self.config.asr.backend
        audio_duration_seconds = self._audio_duration_seconds(audio_data)

        if allow_chunking and transport == "realtime_ws":
            fallback_model = _get_direct_offline_fallback_model(
                model,
                audio_duration_seconds,
            )
            if fallback_model:
                print(
                    "[BatchASR] Long batch audio "
                    f"({audio_duration_seconds:.1f}s) bypassing realtime_ws for {model}; "
                    f"using {fallback_model} directly"
                )
                kwargs = {
                    "model_override": fallback_model,
                    "language_override": language_override,
                    "allow_chunking": True,
                }
                if context_instruction:
                    kwargs["context_instruction"] = context_instruction
                return self.transcribe(audio_data, **kwargs)

        if transport == "short_file":
            if self._supports_short_file(audio_data):
                return self._transcribe_short_file(
                    audio_data,
                    model_override=model,
                    language_override=language_override,
                )

            print("[BatchASR] short_file backend skipped, falling back to realtime_ws")

        if transport == "omni_offline":
            if allow_chunking:
                chunks = self._split_audio_for_batch(audio_data)
                if len(chunks) > 1:
                    kwargs = {
                        "model": model,
                        "language_override": language_override,
                    }
                    if context_instruction:
                        kwargs["context_instruction"] = context_instruction
                    return self._transcribe_chunked_audio(audio_data, **kwargs)
            kwargs = {"model_override": model}
            if context_instruction:
                kwargs["context_instruction"] = context_instruction
            return self._transcribe_omni_offline(audio_data, **kwargs)

        kwargs = {
            "model_override": model,
            "language_override": language_override,
        }
        if context_instruction:
            kwargs["context_instruction"] = context_instruction
        return self._transcribe_realtime_ws(audio_data, **kwargs)

    def transcribe_with_system_prompt(
        self,
        audio_data: bytes,
        *,
        system_prompt: str,
        model_override: Optional[str] = None,
        language_override: Optional[str] = None,
    ) -> str:
        """Transcribe audio with an Omni offline instruction prompt."""
        del language_override
        return self._transcribe_omni_offline(
            audio_data,
            model_override=model_override,
            system_prompt=system_prompt,
        )

    def _build_debug_trace(
        self,
        backend: str,
        model: str,
        audio_data: Optional[bytes],
        corpus_text: Optional[str],
        request_mode: str = "batch",
    ) -> ASRDebugTrace:
        bytes_per_second = (
            self.config.audio.sample_rate * self.config.audio.channels * 2
        )
        audio_bytes = len(audio_data) if audio_data is not None else 0
        duration_ms = (audio_bytes / bytes_per_second * 1000) if bytes_per_second else 0.0
        return ASRDebugTrace(
            backend=backend,
            request_mode=request_mode,
            model=model,
            sample_rate=self.config.audio.sample_rate,
            audio_bytes=audio_bytes,
            audio_duration_ms=round(duration_ms, 2),
            corpus_text=corpus_text,
        )

    def _dump_debug_artifacts(self, audio_data: bytes, trace: ASRDebugTrace) -> None:
        debug_dir = _debug_dir()
        if debug_dir is None:
            return

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        prefix = f"{timestamp}-{trace.request_mode}-{trace.backend}"
        wav_path = debug_dir / f"{prefix}.wav"
        json_path = debug_dir / f"{prefix}.json"

        if audio_data:
            with wave.open(str(wav_path), "wb") as wav_file:
                wav_file.setnchannels(self.config.audio.channels)
                wav_file.setsampwidth(2)
                wav_file.setframerate(self.config.audio.sample_rate)
                wav_file.writeframes(audio_data)

        json_path.write_text(
            json.dumps(asdict(trace), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _print_trace_summary(trace)
        print(f"[BatchASR] Debug artifacts written to {json_path}")

    def _finalize_trace_billing(self, trace: ASRDebugTrace) -> None:
        trace.billing = build_asr_billing(
            model=trace.model,
            audio_seconds=trace.audio_duration_ms / 1000.0,
            usage=trace.usage or None,
        ) or {}
        self._last_metering = dict(trace.billing) if trace.billing else None

    def get_last_metering(self) -> dict[str, Any] | None:
        return dict(self._last_metering) if self._last_metering else None

    def _supports_short_file(self, audio_data: bytes) -> bool:
        bytes_per_second = (
            self.config.audio.sample_rate * self.config.audio.channels * 2
        )
        duration_seconds = len(audio_data) / bytes_per_second if bytes_per_second else 0
        wav_size = len(audio_data) + 44
        return (
            duration_seconds <= SHORT_FILE_MAX_DURATION_SECONDS
            and wav_size <= SHORT_FILE_MAX_BYTES
        )

    def _recover_failed_omni_response(
        self,
        audio_data: bytes,
        model: str,
        transcript_text: str,
        reason: str,
        trace: Optional[ASRDebugTrace] = None,
        context_instruction: str = "",
    ) -> tuple[str, str]:
        fallback_model = _get_omni_offline_fallback_model(model)
        if fallback_model and audio_data:
            print(
                "[BatchASR] Omni realtime response "
                f"{reason}, falling back to {fallback_model} "
                f"({_format_trace_ids(trace)})"
            )
            try:
                kwargs = {"model_override": fallback_model}
                if context_instruction:
                    kwargs["context_instruction"] = context_instruction
                return (
                    self.transcribe(audio_data, **kwargs),
                    "omni_offline_fallback",
                )
            except Exception as exc:
                print(f"[BatchASR] Omni offline fallback failed: {exc}")

        if transcript_text and self.config.enable_polish:
            print(
                "[BatchASR] Omni realtime response "
                f"{reason}, falling back to second-stage text polish "
                f"({_format_trace_ids(trace)})"
            )
            try:
                polisher = TextPolisher()
                polisher.set_context_instruction(context_instruction)
                polished = polisher.polish(transcript_text)
                source = "polisher_fallback" if polished.used_llm else "transcript"
                return polished.polished_text.strip(), source
            except Exception as exc:
                print(f"[BatchASR] Text polish fallback failed: {exc}")

        return transcript_text, "transcript" if transcript_text else "empty"

    def _transcribe_realtime_ws(
        self,
        audio_data: bytes,
        model_override: Optional[str] = None,
        language_override: Optional[str] = None,
        context_instruction: str = "",
    ) -> str:
        model = model_override or self.config.asr.model
        print(f"[BatchASR] Starting transcription, audio size: {len(audio_data)} bytes")
        print(
            f"[BatchASR] Backend: realtime_ws, Model: {model}, "
            f"Sample rate: {self.config.audio.sample_rate}"
        )

        error_msg = ""
        corpus_text = _get_corpus_text()
        trace = self._build_debug_trace(
            backend="realtime_ws",
            model=model,
            audio_data=audio_data,
            corpus_text=corpus_text,
        )
        result_text = ""
        result_source = "empty"
        response_requested = False
        fallback_reason = ""
        audio_duration_seconds = _pcm_duration_seconds(
            audio_data,
            self.config.audio.sample_rate,
            self.config.audio.channels,
        )
        response_start_timeout = _adaptive_response_start_timeout(audio_duration_seconds)
        response_complete_timeout = _adaptive_response_complete_timeout(
            audio_duration_seconds,
            base_timeout=30.0,
        )

        def on_error(msg: str):
            nonlocal error_msg
            error_msg = msg
            trace.error = msg

        callback = BatchASRCallback(on_error=on_error)
        callback.set_debug_trace(trace)
        conversation: Optional[OmniRealtimeConversation] = None

        try:
            conversation = OmniRealtimeConversation(
                model=model,
                url="wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
                callback=callback,
            )
            conversation.connect()

            model_info = get_asr_model_info(model)
            session_kwargs = _build_session_kwargs(
                model_info,
                config=self.config,
                language_override=language_override,
                context_instruction=context_instruction,
            )
            conversation.update_session(**session_kwargs)

            if not callback.wait_for_session_updated(timeout=10.0):
                print("[BatchASR] Timeout waiting for session.updated")
                trace.recognition_timed_out = True

            for i in range(0, len(audio_data), REALTIME_CHUNK_SIZE):
                chunk = audio_data[i:i + REALTIME_CHUNK_SIZE]
                audio_b64 = base64.b64encode(chunk).decode("ascii")
                conversation.append_audio(audio_b64)

            print(f"[BatchASR] Sent {max(1, (len(audio_data) + REALTIME_CHUNK_SIZE - 1) // REALTIME_CHUNK_SIZE)} audio chunks")

            conversation.commit()
            callback.mark_client_event("client.commit")

            should_request_response = _should_start_inline_response_now(
                model_info,
                callback,
            )
            if should_request_response:
                try:
                    response_requested = True
                    callback.mark_client_event("client.response.requested")
                    conversation.create_response()
                except Exception as response_error:
                    should_request_response = False
                    print(f"[BatchASR] Inline polish response failed: {response_error}")

            transcript_text = callback.get_full_text().strip()
            result_text = transcript_text
            if should_request_response:
                response_started = callback.wait_for_response_started(
                    timeout=response_start_timeout
                )
                if response_started:
                    response_completed = callback.wait_for_response_complete(
                        timeout=response_complete_timeout
                    )
                    response_text, response_source = _get_completed_response_result(
                        callback
                    )
                    if response_completed and response_text and response_source:
                        result_text = response_text
                        result_source = response_source
                    else:
                        fallback_reason = "response_incomplete"
                else:
                    fallback_reason = "response_not_started"

                if fallback_reason:
                    if not callback.wait_for_transcription_complete(
                        timeout=INLINE_RESPONSE_TRANSCRIPT_TIMEOUT_SECONDS
                    ):
                        print("[BatchASR] Timeout waiting for transcription completion")
                        trace.recognition_timed_out = True
                    transcript_text = callback.get_full_text().strip()
                    if fallback_reason == "response_not_started":
                        late_response_text, late_response_source, late_started = (
                            _try_finalize_late_response(
                                callback,
                                response_complete_timeout,
                            )
                        )
                        if late_response_text and late_response_source:
                            fallback_reason = ""
                            result_text = late_response_text
                            result_source = late_response_source
                        elif late_started:
                            fallback_reason = "response_incomplete"
                    if fallback_reason:
                        result_text, result_source = self._recover_failed_omni_response(
                            audio_data,
                            model,
                            transcript_text,
                            fallback_reason,
                            trace=trace,
                        )
            else:
                if not callback.wait_for_transcription_complete(timeout=30.0):
                    print("[BatchASR] Timeout waiting for transcription completion")
                    trace.recognition_timed_out = True
                transcript_text = callback.get_full_text().strip()
                result_text = transcript_text
                result_source = "transcript" if transcript_text else "empty"

            if self.config.enable_polish:
                result_text = normalize_structured_list_spacing(
                    result_text,
                    self.config.llm,
                )

            trace.result_text = result_text
            trace.response_requested = response_requested
            trace.fallback_reason = fallback_reason
            callback.mark_client_event("client.result.selected", source=result_source)
            print(f"[BatchASR] Final result: '{result_text}'")

            # Omni models don't support end_session; skip it and just close
            is_omni = model_info and model_info.get("input_audio_transcription_model") is not None
            if not is_omni:
                try:
                    conversation.end_session(timeout=5)
                    if not callback.wait_for_finish(timeout=5.0):
                        trace.cleanup_timed_out = True
                        print("[BatchASR] Timeout waiting for session cleanup")
                except Exception as close_error:
                    print(f"[BatchASR] Graceful end_session failed: {close_error}")
                    trace.cleanup_timed_out = True

        except Exception as e:
            print(f"[BatchASR] Exception: {e}")
            error_msg = str(e)
            trace.error = error_msg
            result_source = "error"
        finally:
            if not trace.result_source:
                _finalize_trace(trace, result_source or ("error" if trace.error else "empty"))
            self._finalize_trace_billing(trace)
            if conversation is not None:
                try:
                    conversation.close()
                except Exception:
                    pass
            self._dump_debug_artifacts(audio_data, trace)

        if error_msg:
            print(f"[BatchASR] Returning with error: {error_msg}")

        return result_text

    def _transcribe_short_file(
        self,
        audio_data: bytes,
        model_override: Optional[str] = None,
        language_override: Optional[str] = None,
    ) -> str:
        short_file_model = model_override or self.config.asr.model
        print(f"[BatchASR] Starting transcription, audio size: {len(audio_data)} bytes")
        print(
            f"[BatchASR] Backend: short_file, Model: {short_file_model}, "
            f"Sample rate: {self.config.audio.sample_rate}"
        )

        corpus_text = _get_corpus_text()
        trace = self._build_debug_trace(
            backend="short_file",
            model=short_file_model,
            audio_data=audio_data,
            corpus_text=corpus_text,
        )
        temp_path = self._write_temp_wav(audio_data)
        try:
            messages = []
            if corpus_text:
                messages.append(
                    {
                        "role": "system",
                        "content": [{"text": corpus_text}],
                    }
                )

            messages.append(
                {
                    "role": "user",
                    "content": [{"audio": temp_path}],
                }
            )

            asr_language = _resolve_asr_language(
                config=self.config,
                language_override=language_override,
            )
            asr_options = {"enable_itn": True}
            if asr_language is not None:
                asr_options["language"] = asr_language

            response = MultiModalConversation.call(
                model=short_file_model,
                messages=messages,
                result_format="message",
                asr_options=asr_options,
            )
            trace.usage = _extract_usage_from_response(response) or {}

            if response.status_code != 200:
                raise Exception(f"API error: {response.code} - {response.message}")

            result_text = _extract_multimodal_text(response)
            trace.result_text = result_text
            print(f"[BatchASR] Final result: '{result_text}'")
            return result_text
        except Exception as exc:
            trace.error = str(exc)
            raise
        finally:
            if not trace.result_source:
                _finalize_trace(
                    trace,
                    "transcript" if trace.result_text else ("error" if trace.error else "empty"),
                )
            self._finalize_trace_billing(trace)
            self._dump_debug_artifacts(audio_data, trace)
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    def _transcribe_omni_offline(
        self,
        audio_data: bytes,
        model_override: Optional[str] = None,
        system_prompt: Optional[str] = None,
        context_instruction: str = "",
    ) -> str:
        model = model_override or self.config.asr.model
        print(f"[BatchASR] Starting Omni offline transcription, audio size: {len(audio_data)} bytes")
        print(f"[BatchASR] Backend: omni_offline, Model: {model}")

        trace = self._build_debug_trace(
            backend="omni_offline",
            model=model,
            audio_data=audio_data,
            corpus_text=None,
        )
        try:
            import io
            wav_buf = io.BytesIO()
            with wave.open(wav_buf, "wb") as wf:
                wf.setnchannels(self.config.audio.channels)
                wf.setsampwidth(2)
                wf.setframerate(self.config.audio.sample_rate)
                wf.writeframes(audio_data)
            audio_b64 = base64.b64encode(wav_buf.getvalue()).decode("ascii")

            if system_prompt is not None:
                prompt = system_prompt
            elif self.config.enable_polish:
                prompt = build_omni_inline_polish_instructions(
                    self.config.llm,
                    context_instruction=context_instruction,
                )
            else:
                prompt = "请将以下音频准确转录为文字，直接输出转录结果。"

            from openai import OpenAI
            client = OpenAI(
                api_key=dashscope.api_key or os.environ.get("DASHSCOPE_API_KEY", ""),
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            )

            t0 = time.time()
            completion = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_audio",
                                "input_audio": {
                                    "data": f"data:;base64,{audio_b64}",
                                    "format": "wav",
                                },
                            },
                        ],
                    },
                ],
                modalities=["text"],
                stream=True,
                stream_options={"include_usage": True},
            )
            _update_trace_ids_from_openai_stream(trace, completion)

            result_text = ""
            for chunk in completion:
                _update_trace_ids_from_openai_chunk(trace, chunk)
                trace.usage = _extract_usage_from_response(chunk) or trace.usage
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        result_text += delta.content

            elapsed = time.time() - t0
            result_text = result_text.strip()
            if self.config.enable_polish:
                result_text = normalize_structured_list_spacing(
                    result_text,
                    self.config.llm,
                )
            trace.result_text = result_text
            print(f"[BatchASR] Omni offline result ({elapsed:.1f}s): '{result_text}'")
            return result_text
        except Exception as exc:
            trace.error = str(exc)
            raise
        finally:
            if not trace.result_source:
                _finalize_trace(
                    trace,
                    "response" if trace.result_text else ("error" if trace.error else "empty"),
                )
            self._finalize_trace_billing(trace)
            self._dump_debug_artifacts(audio_data, trace)

    def _write_temp_wav(self, audio_data: bytes) -> str:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
            temp_path = temp_file.name

        with wave.open(temp_path, "wb") as wav_file:
            wav_file.setnchannels(self.config.audio.channels)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.config.audio.sample_rate)
            wav_file.writeframes(audio_data)

        return temp_path


class StreamingASRCallback(OmniRealtimeCallback):
    """Callback handler for streaming ASR recognition."""

    def __init__(
        self,
        on_partial: Optional[Callable[[ASRResult], None]] = None,
        on_final: Optional[Callable[[ASRResult], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_complete: Optional[Callable[[], None]] = None,
    ):
        self._on_partial = on_partial
        self._on_final = on_final
        self._on_error = on_error
        self._on_complete = on_complete

        self._full_text = ""
        self._lock = threading.Lock()
        self._session_updated = threading.Event()
        self._transcription_completed = threading.Event()
        self._complete_event = threading.Event()
        self._response_completed = threading.Event()
        self._response_started = threading.Event()
        self._response_text = ""
        self._response_done_received = False
        self._response_done_status = ""
        self._response_text_done_received = False
        self._response_content_part_done_received = False
        self._response_output_item_done_received = False
        self._response_output_item_status = ""
        self._started_at = time.perf_counter()
        self._debug_trace: Optional[ASRDebugTrace] = None
        self._event_queue: queue.Queue[Any] = queue.Queue()
        self._closed = False
        self._event_worker = _THREAD_CLASS(
            target=self._run_event_loop,
            name="vocal-more-asr-inbound",
            daemon=True,
        )
        self._event_worker.start()

    def set_debug_trace(self, trace: ASRDebugTrace) -> None:
        self._debug_trace = trace

    def _elapsed_ms(self) -> float:
        return round((time.perf_counter() - self._started_at) * 1000, 2)

    def _record_event(
        self,
        event_type: str,
        *,
        t_ms: Optional[float] = None,
        **payload,
    ) -> None:
        if self._debug_trace is None:
            return

        event = {
            "t_ms": self._elapsed_ms() if t_ms is None else t_ms,
            "type": event_type,
        }
        if payload:
            event.update(payload)
        self._debug_trace.events.append(event)

    def mark_client_event(self, event_type: str, **payload) -> None:
        self._record_event(event_type, **payload)

    def on_open(self):
        print("[StreamingASR] Connection opened")
        self._enqueue_inbound_event(
            {
                "kind": "socket_open",
                "event_type": "socket.open",
                "t_ms": self._elapsed_ms(),
                "metadata": {},
            }
        )

    def on_close(self, code, msg):
        print(f"[StreamingASR] Connection closed: code={code}, msg={msg}")
        self._enqueue_inbound_event(
            {
                "kind": "socket_close",
                "event_type": "socket.close",
                "t_ms": self._elapsed_ms(),
                "metadata": {"code": code, "msg": msg},
                "code": code,
                "message": msg,
            }
        )

    def on_event(self, response):
        try:
            event_type = response.get("type", "")
            metadata = _update_trace_ids_from_response(
                self._debug_trace,
                event_type,
                response,
            )
            inbound_event: dict[str, Any] = {
                "kind": "server_event",
                "event_type": event_type,
                "t_ms": self._elapsed_ms(),
                "metadata": metadata,
            }

            if event_type == "session.created":
                inbound_event["kind"] = "session_created"
                inbound_event["session_id"] = response.get("session", {}).get("id", "unknown")

            elif event_type == "session.updated":
                inbound_event["kind"] = "session_updated"

            elif event_type == "conversation.item.input_audio_transcription.text":
                inbound_event["kind"] = "transcript_partial"
                inbound_event["text"] = response.get("text", "") or response.get("stash", "")

            elif event_type == "conversation.item.input_audio_transcription.completed":
                inbound_event["kind"] = "transcript_completed"
                inbound_event["transcript"] = response.get("transcript", "")

            elif event_type == "response.text.delta":
                inbound_event["kind"] = "response_delta"
                inbound_event["delta"] = response.get("delta", "")

            elif event_type == "response.text.done":
                inbound_event["kind"] = "response_text_done"
                inbound_event["text"] = response.get("text", "")

            elif event_type == "response.content_part.done":
                inbound_event["kind"] = "response_content_part_done"
                inbound_event["part"] = response.get("part")

            elif event_type == "response.output_item.done":
                item = response.get("item")
                status = ""
                if isinstance(item, dict):
                    status = str(item.get("status", "") or "")
                inbound_event["kind"] = "response_output_item_done"
                inbound_event["item"] = item
                inbound_event["status"] = status

            elif event_type == "response.created":
                inbound_event["kind"] = "response_created"

            elif event_type == "response.done":
                response_obj = response.get("response")
                response_status = ""
                if isinstance(response_obj, dict):
                    response_status = str(response_obj.get("status", "") or "")
                inbound_event["kind"] = "response_done"
                inbound_event["response_obj"] = response_obj
                inbound_event["status"] = response_status
                if isinstance(response_obj, dict):
                    inbound_event["usage"] = response_obj.get("usage")

            elif event_type == "session.finished":
                inbound_event["kind"] = "session_finished"
                inbound_event["transcript"] = response.get("transcript", "")

            elif event_type == "error":
                inbound_event["kind"] = "error"
                inbound_event["error_message"] = response.get("error", {}).get("message", str(response))

            self._enqueue_inbound_event(inbound_event)
        except Exception as e:
            self._enqueue_inbound_event(
                {
                    "kind": "error",
                    "event_type": "client.callback.error",
                    "t_ms": self._elapsed_ms(),
                    "metadata": {},
                    "error_message": str(e),
                }
            )

    def _enqueue_inbound_event(self, event: dict[str, Any]) -> None:
        if self._closed:
            return
        self._event_queue.put(event)

    def _run_event_loop(self) -> None:
        while True:
            item = self._event_queue.get()
            if item is _CALLBACK_EVENT_STOP:
                break
            if isinstance(item, threading.Event):
                item.set()
                continue
            try:
                self._apply_inbound_event(item)
            except Exception as exc:
                print(f"[StreamingASR] Callback worker failed: {exc}")
                if self._on_error:
                    self._on_error(str(exc))

    def _apply_inbound_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("event_type", "") or "")
        event_t_ms = float(event.get("t_ms", self._elapsed_ms()) or 0.0)
        metadata = dict(event.get("metadata", {}) or {})
        self._record_event(event_type, t_ms=event_t_ms, **metadata)

        kind = event.get("kind")

        if kind == "session_created":
            print(f"[StreamingASR] Session created: {event.get('session_id', 'unknown')}")
            return

        if kind == "session_updated":
            print("[StreamingASR] Session updated, ready")
            self._session_updated.set()
            return

        if kind == "transcript_partial":
            text = str(event.get("text", "") or "")
            if text and self._debug_trace is not None:
                self._debug_trace.partial_texts.append(text)
                self._record_event(event_type, t_ms=event_t_ms, **metadata, text=text)
            if text and self._on_partial:
                self._on_partial(ASRResult(text=text, is_final=False))
            return

        if kind == "transcript_completed":
            transcript = str(event.get("transcript", "") or "")
            with self._lock:
                self._full_text += transcript
                accumulated = self._full_text
            self._transcription_completed.set()
            if transcript and self._debug_trace is not None:
                self._debug_trace.final_transcripts.append(transcript)
                self._record_event(
                    event_type,
                    t_ms=event_t_ms,
                    **metadata,
                    transcript=transcript,
                )
            if self._on_partial:
                self._on_partial(ASRResult(text=accumulated, is_final=False))
            if self._on_final:
                self._on_final(ASRResult(text=transcript, is_final=True))
            return

        if kind == "response_delta":
            delta = str(event.get("delta", "") or "")
            if not delta:
                return
            self._response_started.set()
            with self._lock:
                self._response_text += delta
                response_text = self._response_text
            self._record_event(event_type, t_ms=event_t_ms, **metadata, delta=delta)
            if self._on_partial:
                self._on_partial(ASRResult(text=response_text, is_final=False))
            return

        if kind == "response_text_done":
            text = str(event.get("text", "") or "")
            self._response_started.set()
            self._response_text_done_received = True
            with self._lock:
                self._response_text = _prefer_longer_text(
                    self._response_text,
                    text,
                )
                response_text = self._response_text
            self._record_event(event_type, t_ms=event_t_ms, **metadata, text=text)
            if self._on_partial and response_text:
                self._on_partial(ASRResult(text=response_text, is_final=False))
            return

        if kind == "response_content_part_done":
            part = event.get("part")
            part_text = _extract_text_from_content_part(part)
            self._response_started.set()
            self._response_content_part_done_received = True
            with self._lock:
                self._response_text = _prefer_longer_text(
                    self._response_text,
                    part_text,
                )
                response_text = self._response_text
            if part_text:
                self._record_event(event_type, t_ms=event_t_ms, **metadata, text=part_text)
                if self._on_partial and response_text:
                    self._on_partial(ASRResult(text=response_text, is_final=False))
            return

        if kind == "response_output_item_done":
            item = event.get("item")
            item_text = _extract_text_from_realtime_item(item)
            status = str(event.get("status", "") or "")
            self._response_started.set()
            self._response_output_item_done_received = True
            self._response_output_item_status = status
            with self._lock:
                self._response_text = _prefer_longer_text(
                    self._response_text,
                    item_text,
                )
                response_text = self._response_text
            self._response_completed.set()
            self._record_event(
                event_type,
                t_ms=event_t_ms,
                **metadata,
                status=status,
                text=item_text,
            )
            if self._on_partial and response_text:
                self._on_partial(ASRResult(text=response_text, is_final=False))
            return

        if kind == "response_created":
            self._response_started.set()
            return

        if kind == "response_done":
            response_obj = event.get("response_obj")
            response_status = str(event.get("status", "") or "")
            self._response_started.set()
            self._response_done_received = True
            self._response_done_status = response_status
            if self._debug_trace is not None and isinstance(event.get("usage"), dict):
                self._debug_trace.usage = dict(event["usage"])
            with self._lock:
                self._response_text = _prefer_longer_text(
                    self._response_text,
                    _extract_text_from_realtime_response(response_obj),
                )
            self._response_completed.set()
            self._record_event(event_type, t_ms=event_t_ms, status=response_status)
            return

        if kind == "session_finished":
            transcript = str(event.get("transcript", "") or "")
            if transcript:
                with self._lock:
                    if not self._full_text:
                        self._full_text = transcript
                if self._debug_trace is not None:
                    self._debug_trace.final_transcripts.append(transcript)
            self._complete_event.set()
            self._record_event(event_type, t_ms=event_t_ms, **metadata, transcript=transcript)
            return

        if kind == "socket_close":
            self._complete_event.set()
            self._session_updated.set()
            self._transcription_completed.set()
            self._response_started.set()
            self._response_completed.set()
            self._record_event(
                event_type,
                t_ms=event_t_ms,
                code=event.get("code"),
                msg=event.get("message"),
            )
            if self._on_complete:
                self._on_complete()
            return

        if kind == "error":
            error_msg = str(event.get("error_message", "") or "")
            self._transcription_completed.set()
            self._record_event(event_type, t_ms=event_t_ms, **metadata, error=error_msg)
            if self._on_error:
                self._on_error(error_msg)

    def _flush_inbound_events(self, timeout: float = 1.0) -> None:
        if self._closed:
            return
        done = threading.Event()
        self._event_queue.put(done)
        done.wait(timeout=timeout)

    def _drop_queued_events(self) -> None:
        while True:
            try:
                item = self._event_queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, threading.Event):
                item.set()
            elif item is _CALLBACK_EVENT_STOP:
                self._event_queue.put(_CALLBACK_EVENT_STOP)
                break

    def get_full_text(self) -> str:
        self._flush_inbound_events()
        with self._lock:
            return self._full_text

    def wait_for_session_updated(self, timeout: float = 10.0) -> bool:
        ready = self._session_updated.wait(timeout=timeout)
        if ready:
            self._flush_inbound_events()
        return ready

    def wait_for_transcription_complete(self, timeout: float = 30.0) -> bool:
        complete = self._transcription_completed.wait(timeout=timeout)
        if complete:
            self._flush_inbound_events()
        return complete

    def wait_for_complete(self, timeout: float = 10.0) -> bool:
        complete = self._complete_event.wait(timeout=timeout)
        if complete:
            self._flush_inbound_events()
        return complete

    def wait_for_response_complete(self, timeout: float = 30.0) -> bool:
        complete = self._response_completed.wait(timeout=timeout)
        if complete:
            self._flush_inbound_events()
        return complete

    def wait_for_response_started(self, timeout: float = 30.0) -> bool:
        started = self._response_started.wait(timeout=timeout)
        if started:
            self._flush_inbound_events()
        return started

    def get_response_text(self) -> str:
        self._flush_inbound_events()
        with self._lock:
            return self._response_text

    def did_receive_response_done(self) -> bool:
        self._flush_inbound_events()
        return self._response_done_received

    def did_receive_response_text_done(self) -> bool:
        self._flush_inbound_events()
        return self._response_text_done_received

    def did_receive_response_output_item_done(self) -> bool:
        self._flush_inbound_events()
        return self._response_output_item_done_received

    def get_response_output_item_status(self) -> str:
        self._flush_inbound_events()
        return self._response_output_item_status

    def get_response_done_status(self) -> str:
        self._flush_inbound_events()
        return self._response_done_status

    def get_response_result_source(self) -> str:
        response_text = self.get_response_text().strip()
        if not response_text:
            return ""
        if self._response_done_received and self._response_done_status in ("", "completed"):
            return "response"
        if (
            self._response_text_done_received
            and self._response_output_item_done_received
            and self._response_output_item_status in ("", "completed")
        ):
            return "response_output_item_done"
        return ""

    def reset(self):
        self._drop_queued_events()
        with self._lock:
            self._full_text = ""
            self._response_text = ""
            self._response_done_received = False
            self._response_done_status = ""
            self._response_text_done_received = False
            self._response_content_part_done_received = False
            self._response_output_item_done_received = False
            self._response_output_item_status = ""
        self._session_updated.clear()
        self._transcription_completed.clear()
        self._complete_event.clear()
        self._response_started.clear()
        self._response_completed.clear()
        self._started_at = time.perf_counter()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._drop_queued_events()
        self._event_queue.put(_CALLBACK_EVENT_STOP)
        self._event_worker.join(timeout=1.0)


class ASREngine:
    """Streaming ASR engine using qwen3-asr-flash-realtime.

    Streams audio in real-time during recording for low-latency results.
    """

    def __init__(
        self,
        on_partial_result: Optional[Callable[[ASRResult], None]] = None,
        on_final_result: Optional[Callable[[ASRResult], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ):
        self.config = get_config()
        self.on_partial_result = on_partial_result
        self.on_final_result = on_final_result
        self.on_error = on_error

        self._conversation: Optional[OmniRealtimeConversation] = None
        self._callback: Optional[StreamingASRCallback] = None
        self._is_running = False
        self._accepting_audio = False
        self._session_ready = False
        self._connect_failed = False
        self._lock = threading.Lock()
        self._audio_queue: queue.Queue[bytes | object] = queue.Queue(
            maxsize=_streaming_audio_queue_max_chunks(self.config)
        )
        self._pending_audio_chunks = 0
        self._audio_queue_high_watermark = 0
        self._audio_queue_drained = threading.Condition(self._lock)
        self._streaming_degraded = False
        self._streaming_degraded_reason = ""
        self._sender_shutdown = threading.Event()
        self._sender_thread = _THREAD_CLASS(
            target=self._run_audio_sender_loop,
            name="vocal-more-asr-audio-sender",
            daemon=True,
        )
        self._sender_thread.start()
        self._batch_fallback = BatchASREngine()
        self._session_model_id = self.config.asr.model
        self._conversation_model_id: Optional[str] = None
        self._conversation_is_clean = False
        self._warm_keeper_stop = threading.Event()
        self._warm_keeper_thread: Optional[threading.Thread] = None
        self._warm_generation = 0
        self._warm_session_idle_since: Optional[float] = None
        self._active_trace: Optional[ASRDebugTrace] = None
        self._trace_warm_reused = False
        self._last_metering: dict[str, Any] | None = None

        _apply_dashscope_api_key(self.config)

    def _update_trace_audio_stats(
        self,
        trace: ASRDebugTrace,
        pcm_data: Optional[bytes],
    ) -> None:
        if pcm_data is None:
            return
        bytes_per_second = (
            self.config.audio.sample_rate * self.config.audio.channels * 2
        )
        trace.audio_bytes = len(pcm_data)
        trace.audio_duration_ms = round(
            (len(pcm_data) / bytes_per_second * 1000) if bytes_per_second else 0.0,
            2,
        )

    def _finish_active_trace(
        self,
        pcm_data: Optional[bytes],
        result_source: str,
        response_requested: bool = False,
        fallback_reason: str = "",
    ) -> None:
        trace = self._active_trace
        if trace is None:
            return

        self._active_trace = None
        self._update_trace_audio_stats(trace, pcm_data)
        trace.response_requested = response_requested
        trace.warm_session_reused = self._trace_warm_reused
        trace.fallback_reason = fallback_reason
        if self._callback:
            self._callback.mark_client_event("client.result.selected", source=result_source)
        _finalize_trace(trace, result_source)
        trace.billing = build_asr_billing(
            model=trace.model,
            audio_seconds=trace.audio_duration_ms / 1000.0,
            usage=trace.usage or None,
        ) or {}
        self._last_metering = dict(trace.billing) if trace.billing else None
        self._batch_fallback._dump_debug_artifacts(pcm_data or b"", trace)

    def _queue_stats(self) -> tuple[int, int, int]:
        with self._lock:
            return (
                self._pending_audio_chunks,
                self._audio_queue_high_watermark,
                self._audio_queue.maxsize,
            )

    def _log_queue_state(self, event: str, **payload) -> None:
        pending, high_watermark, max_chunks = self._queue_stats()
        fields = {
            "event": event,
            "model": self._session_model_id,
            "queue_depth": pending,
            "queue_high_watermark": high_watermark,
            "queue_max": max_chunks,
        }
        fields.update(payload)
        details = " ".join(f"{key}={value}" for key, value in fields.items())
        print(f"[StreamingASRQueue] {details}")

    def _log_fallback(self, reason: str, **payload) -> None:
        fields = {
            "reason": reason,
            "model": self._session_model_id,
        }
        fields.update(payload)
        details = " ".join(f"{key}={value}" for key, value in fields.items())
        print(f"[StreamingASRFallback] {details}")

    def _transcribe_batch_fallback(self, pcm_data: bytes) -> str:
        kwargs = {}
        if self._context_instruction:
            kwargs["context_instruction"] = self._context_instruction
        return self._batch_fallback.transcribe(pcm_data, **kwargs)

    def _close_conversation(self, conversation: Optional[OmniRealtimeConversation]) -> None:
        if conversation is None:
            return
        try:
            conversation.close()
        except Exception:
            pass

    def _drop_conversation(self) -> Optional[OmniRealtimeConversation]:
        with self._lock:
            conversation = self._conversation
            self._conversation = None
            self._conversation_model_id = None
            self._conversation_is_clean = False
            self._session_ready = False
        return conversation

    def _drop_conversation_with_callback(
        self,
    ) -> tuple[
        Optional[OmniRealtimeConversation],
        Optional[StreamingASRCallback],
    ]:
        with self._lock:
            conversation = self._conversation
            callback = self._callback
            self._conversation = None
            self._callback = None
            self._conversation_model_id = None
            self._conversation_is_clean = False
            self._session_ready = False
        return conversation, callback

    def _stop_warm_keeper(self) -> None:
        with self._lock:
            keeper = self._warm_keeper_thread
            stop_event = self._warm_keeper_stop
            stop_event.set()
            self._warm_generation += 1
        if keeper is not None and keeper is not threading.current_thread():
            keeper.join(timeout=WARM_KEEPER_SHUTDOWN_TIMEOUT_SECONDS)
            if keeper.is_alive():
                print("[StreamingASR] Warm keeper shutdown timed out; abandoning reconnect")
        with self._lock:
            if (
                keeper is self._warm_keeper_thread
                and stop_event is self._warm_keeper_stop
            ):
                self._warm_keeper_thread = None
                self._warm_keeper_stop = threading.Event()

    def _establish_conversation(
        self,
        model_id: str,
        model_info: Optional[dict],
        callback: StreamingASRCallback,
        context_instruction: str = "",
    ) -> OmniRealtimeConversation:
        conversation = OmniRealtimeConversation(
            model=model_id,
            url="wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
            callback=callback,
        )
        conversation.connect()
        conversation.update_session(
            **_build_session_kwargs(
                model_info,
                context_instruction=context_instruction,
            )
        )
        if not callback.wait_for_session_updated(timeout=10.0):
            raise Exception("session.updated timeout")
        return conversation

    def _run_warm_keeper_loop(self) -> None:
        """Create and retain one unused conversation for the next dictation."""
        # Hold the stop event and generation this keeper was started with:
        # _stop_warm_keeper replaces the event after an abandoned join, and
        # reading the fresh state would let an abandoned keeper publish late.
        stop_event = self._warm_keeper_stop
        with self._lock:
            generation = self._warm_generation
            model_id = self._conversation_model_id or self._session_model_id

        while not stop_event.is_set():
            with self._lock:
                if self._is_running:
                    return
                idle_since = self._warm_session_idle_since
                conversation = self._conversation

            if idle_since is None or time.monotonic() - idle_since >= WARM_KEEPER_MAX_IDLE_SECONDS:
                stale, stale_callback = self._drop_conversation_with_callback()
                self._close_conversation(stale)
                if stale_callback:
                    stale_callback.close()
                return

            if _conversation_socket_connected(conversation):
                if stop_event.wait(WARM_KEEPER_CHECK_INTERVAL_SECONDS):
                    return
                continue

            model_info = get_asr_model_info(model_id)
            if not _supports_warm_realtime_session(model_info):
                return

            replacement: Optional[OmniRealtimeConversation] = None
            replacement_callback: Optional[StreamingASRCallback] = None
            previous_callback: Optional[StreamingASRCallback] = None
            abandoned = False
            try:
                replacement_callback = StreamingASRCallback(
                    on_partial=self.on_partial_result,
                    on_final=self.on_final_result,
                    on_error=self.on_error,
                    on_complete=lambda: None,
                )
                replacement = self._establish_conversation(
                    model_id,
                    model_info,
                    replacement_callback,
                )
                with self._lock:
                    # If start() invalidated this reconnect, do not overwrite
                    # the callback used by its active connection.
                    if (
                        self._is_running
                        or generation != self._warm_generation
                        or stop_event.is_set()
                    ):
                        abandoned = True
                    else:
                        stale = self._conversation
                        previous_callback = self._callback
                        self._conversation = replacement
                        self._callback = replacement_callback
                        self._conversation_model_id = model_id
                        self._conversation_is_clean = True
                        self._session_ready = True
                        replacement = None
                        replacement_callback = None
                if abandoned:
                    self._close_conversation(replacement)
                    replacement = None
                    if replacement_callback:
                        replacement_callback.close()
                        replacement_callback = None
                    return
                self._close_conversation(stale)
                if previous_callback:
                    previous_callback.close()
                print("[StreamingASR] Fresh realtime session is warm and ready")
            except Exception as exc:
                print(f"[StreamingASR] Fresh warm session setup failed: {exc}")
            finally:
                self._close_conversation(replacement)
                if replacement_callback:
                    replacement_callback.close()
            if stop_event.wait(WARM_KEEPER_CHECK_INTERVAL_SECONDS):
                return

    def _start_warm_keeper(self, model_info: Optional[dict]) -> None:
        """Retire the consumed conversation and preconnect a clean replacement."""
        if not _supports_warm_realtime_session(model_info):
            return

        with self._lock:
            if self._is_running:
                return
            keeper = self._warm_keeper_thread
            if keeper is not None and keeper.is_alive():
                return
            consumed = self._conversation
            consumed_callback = self._callback
            self._conversation = None
            self._callback = None
            self._conversation_model_id = None
            self._conversation_is_clean = False
            self._session_ready = False
            self._warm_session_idle_since = time.monotonic()
            self._warm_keeper_stop = threading.Event()
            keeper = _THREAD_CLASS(
                target=self._run_warm_keeper_loop,
                name="vocal-more-asr-warm-keeper",
                daemon=True,
            )
            self._warm_keeper_thread = keeper

        self._close_conversation(consumed)
        if consumed_callback:
            consumed_callback.close()
        keeper.start()

    def _mark_streaming_degraded(self, reason: str) -> None:
        with self._lock:
            if self._streaming_degraded:
                return
            self._streaming_degraded = True
            self._streaming_degraded_reason = reason
        if self._callback:
            self._callback.mark_client_event(
                "client.realtime.degraded",
                reason=reason,
            )
        self._log_queue_state("degraded", reason=reason)

    def _clear_audio_queue(self) -> None:
        cleared = 0
        while True:
            try:
                item = self._audio_queue.get_nowait()
            except queue.Empty:
                break
            if item is not _AUDIO_QUEUE_STOP:
                cleared += 1

        if cleared == 0:
            return

        with self._lock:
            self._pending_audio_chunks = 0
            self._audio_queue_drained.notify_all()
        self._log_queue_state("cleared", cleared=cleared)

    def _wait_for_audio_queue_drain(self, timeout: Optional[float] = None) -> bool:
        if timeout is None:
            timeout = _audio_queue_drain_timeout_seconds(
                self._queue_stats()[0],
                self.config,
            )
        deadline = time.perf_counter() + timeout
        with self._audio_queue_drained:
            while self._pending_audio_chunks > 0:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    return False
                self._audio_queue_drained.wait(timeout=remaining)
            return True

    def _run_audio_sender_loop(self) -> None:
        while not self._sender_shutdown.is_set():
            item = self._audio_queue.get()

            if item is _AUDIO_QUEUE_STOP:
                break

            chunk = item
            try:
                while not self._sender_shutdown.is_set():
                    with self._lock:
                        session_ready = self._session_ready
                        connect_failed = self._connect_failed
                        conversation = self._conversation if session_ready else None
                        connect_done = getattr(self, "_connect_done", None)

                    if connect_failed:
                        break

                    if conversation is None:
                        if connect_done is not None and connect_done.is_set():
                            break
                        time.sleep(0.01)
                        continue

                    audio_b64 = base64.b64encode(chunk).decode("ascii")
                    with self._lock:
                        if conversation is self._conversation:
                            self._conversation_is_clean = False
                    conversation.append_audio(audio_b64)
                    break
            except Exception as exc:
                print(f"[StreamingASR] Failed to append audio chunk: {exc}")
                with self._lock:
                    self._connect_failed = True
                    self._accepting_audio = False
                connect_done = getattr(self, "_connect_done", None)
                if connect_done is not None:
                    connect_done.set()
                stale = self._drop_conversation()
                self._close_conversation(stale)
            finally:
                with self._audio_queue_drained:
                    if self._pending_audio_chunks > 0:
                        self._pending_audio_chunks -= 1
                    if self._pending_audio_chunks == 0:
                        self._audio_queue_drained.notify_all()

    def _can_reuse_warm_session(self, model_info: Optional[dict]) -> bool:
        return should_reuse_warm_session(
            supports_warm_session=_supports_warm_realtime_session(model_info),
            is_connected=_conversation_socket_connected(self._conversation),
            matches_model=(
                self._conversation is not None
                and self._conversation_model_id == self._session_model_id
                and self._conversation_is_clean
            ),
        )

    def start(self, *, context_instruction: str = "") -> None:
        """Start the ASR session. Non-blocking — session setup runs in background."""
        if self._is_running:
            return

        _apply_dashscope_api_key(self.config)
        self._context_instruction = str(context_instruction or "").strip()
        self._session_model_id = self.config.asr.model
        model_info = get_asr_model_info(self._session_model_id)
        transport = model_info["transport"] if model_info else self.config.asr.backend
        print(
            f"[StreamingASR] Starting session: model={self._session_model_id}, "
            f"backend={transport}, language={self.config.asr.language}"
        )
        self._log_queue_state(
            "session_start",
            chunk_bytes=_streaming_audio_chunk_bytes(self.config),
            chunk_ms=round(_streaming_audio_chunk_duration_seconds(self.config) * 1000, 2),
        )

        self._stop_warm_keeper()
        self._is_running = True
        self._accepting_audio = True
        self._session_ready = False
        self._connect_failed = False
        self._connect_done = threading.Event()
        self._warm_session_idle_since = None
        self._trace_warm_reused = False
        self._last_metering = None
        self._clear_audio_queue()
        with self._lock:
            self._pending_audio_chunks = 0
            self._audio_queue_high_watermark = 0
            self._streaming_degraded = False
            self._streaming_degraded_reason = ""
        self._active_trace = self._batch_fallback._build_debug_trace(
            backend=transport,
            model=self._session_model_id,
            audio_data=None,
            corpus_text=_get_corpus_text(),
            request_mode="streaming",
        )

        if transport == "omni_offline":
            with self._lock:
                self._connect_failed = True
            self._connect_done.set()
            print("[StreamingASR] Offline model — skipping WebSocket, will use batch")
            return

        if self._callback is None:
            self._callback = StreamingASRCallback(
                on_partial=self.on_partial_result,
                on_final=self.on_final_result,
                on_error=self.on_error,
                on_complete=lambda: None,
            )
        self._callback.set_debug_trace(self._active_trace)

        # Connect + update session in background thread to avoid blocking hotkey
        threading.Thread(target=self._connect, daemon=True).start()

    def refresh_runtime_config(self, drop_idle_session: bool = False) -> None:
        """Apply current config and optionally drop any idle warm session."""
        _apply_dashscope_api_key(self.config)
        if not drop_idle_session or self._is_running:
            return

        self._stop_warm_keeper()
        stale = self._drop_conversation()
        self._close_conversation(stale)

    def refresh_api_key(self) -> None:
        """Apply the latest API key and drop any idle warm session."""
        self.refresh_runtime_config(drop_idle_session=True)

    def _connect(self) -> None:
        """Connect WebSocket with retry, then flush buffered chunks."""
        max_retries = 2
        model_info = get_asr_model_info(self._session_model_id)
        for attempt in range(max_retries + 1):
            try:
                if self._callback:
                    self._callback.reset()

                reusing_warm_session = self._can_reuse_warm_session(model_info)
                if reusing_warm_session:
                    print("[StreamingASR] Claiming clean prewarmed realtime session")
                    self._trace_warm_reused = True
                    if self._callback:
                        self._callback.mark_client_event("client.warm_session.reused")
                else:
                    if self._conversation is not None:
                        stale = self._drop_conversation()
                        self._close_conversation(stale)
                    self._conversation = self._establish_conversation(
                        self._session_model_id,
                        model_info,
                        self._callback,
                        self._context_instruction,
                    )
                    self._conversation_model_id = self._session_model_id
                    self._conversation_is_clean = True

                if reusing_warm_session:
                    session_kwargs = _build_session_kwargs(
                        model_info,
                        context_instruction=self._context_instruction,
                    )
                    self._conversation.update_session(**session_kwargs)

                    if not self._callback.wait_for_session_updated(timeout=10.0):
                        print("[StreamingASR] Timeout waiting for session.updated")
                        raise Exception("session.updated timeout")

                with self._lock:
                    self._session_ready = True

                self._connect_done.set()
                print(f"[StreamingASR] Connected (attempt {attempt + 1})")
                return  # Success

            except Exception as e:
                print(f"[StreamingASR] Connection attempt {attempt + 1}/{max_retries + 1} failed: {e}")
                try:
                    if self._conversation:
                        stale = self._drop_conversation()
                        self._close_conversation(stale)
                except Exception:
                    pass

                if attempt < max_retries:
                    time.sleep(0.5)

        # All retries exhausted — mark as failed, stop() will fall back to batch
        print("[StreamingASR] All connection attempts failed, will fall back to batch")
        with self._lock:
            self._connect_failed = True
        self._connect_done.set()

    def send_audio(self, audio_chunk: bytes) -> None:
        """Queue audio for realtime ASR without blocking the audio callback thread."""
        should_log_depth = False
        queue_depth = 0
        with self._lock:
            if not self._is_running or not self._accepting_audio:
                return
            if self._streaming_degraded:
                return
            self._pending_audio_chunks += 1
            queue_depth = self._pending_audio_chunks
            if queue_depth > self._audio_queue_high_watermark:
                self._audio_queue_high_watermark = queue_depth
                should_log_depth = (
                    queue_depth == 1
                    or queue_depth == self._audio_queue.maxsize
                    or queue_depth % 8 == 0
                )

        try:
            self._audio_queue.put_nowait(audio_chunk)
        except queue.Full:
            with self._audio_queue_drained:
                if self._pending_audio_chunks > 0:
                    self._pending_audio_chunks -= 1
                self._audio_queue_drained.notify_all()
            self._mark_streaming_degraded("audio_queue_full")
            return
        if should_log_depth:
            self._log_queue_state("queued", chunk_bytes=len(audio_chunk))

    def stop(self, timeout: float = 30.0, pcm_data: Optional[bytes] = None) -> str:
        """Stop ASR: commit audio, wait for transcription, return result.

        Args:
            timeout: Max wait time for transcription result.
            pcm_data: Complete recorded PCM data for batch fallback.
        """
        if not self._is_running:
            return ""

        with self._lock:
            self._accepting_audio = False

        # Wait for _connect() thread to finish (success or failure) before proceeding
        if not self._connect_done.wait(timeout=12.0):
            print("[StreamingASR] Timeout waiting for connection thread, falling back to batch")
            self._log_fallback("connect_timeout")
            self._is_running = False
            self._clear_audio_queue()
            if self._callback:
                self._callback.mark_client_event(
                    "client.fallback.started",
                    reason="connect_timeout",
                )
            self._finish_active_trace(
                pcm_data,
                result_source="batch_fallback",
                fallback_reason="connect_timeout",
            )
            if pcm_data:
                result = self._transcribe_batch_fallback(pcm_data)
                self._last_metering = self._batch_fallback.get_last_metering()
                return result
            return ""

        # If streaming connection failed, fall back to batch transcription
        with self._lock:
            failed = self._connect_failed
            degraded = self._streaming_degraded
            degraded_reason = self._streaming_degraded_reason

        if degraded:
            print("[StreamingASR] Realtime queue degraded; falling back to batch transcription")
            self._log_fallback(degraded_reason, degraded=True)
            self._is_running = False
            self._clear_audio_queue()
            stale = self._drop_conversation()
            self._close_conversation(stale)
            if pcm_data:
                if self._callback:
                    self._callback.mark_client_event(
                        "client.fallback.started",
                        reason=degraded_reason,
                    )
                self._finish_active_trace(
                    pcm_data,
                    result_source="batch_fallback",
                    fallback_reason=degraded_reason,
                )
                result = self._transcribe_batch_fallback(pcm_data)
                self._last_metering = self._batch_fallback.get_last_metering()
                return result
            self._finish_active_trace(
                pcm_data,
                result_source="empty",
                fallback_reason=f"{degraded_reason}_no_pcm",
            )
            return ""

        if failed:
            self._is_running = False
            self._clear_audio_queue()
            if pcm_data:
                print("[StreamingASR] Falling back to batch transcription")
                self._log_fallback("connect_failed")
                if self._callback:
                    self._callback.mark_client_event(
                        "client.fallback.started",
                        reason="connect_failed",
                    )
                self._finish_active_trace(
                    pcm_data,
                    result_source="batch_fallback",
                    fallback_reason="connect_failed",
                )
                result = self._transcribe_batch_fallback(pcm_data)
                self._last_metering = self._batch_fallback.get_last_metering()
                return result
            self._finish_active_trace(
                pcm_data,
                result_source="empty",
                fallback_reason="connect_failed_no_pcm",
            )
            return ""

        drain_timeout = _audio_queue_drain_timeout_seconds(self._queue_stats()[0], self.config)
        if not self._wait_for_audio_queue_drain(timeout=drain_timeout):
            print("[StreamingASR] Audio sender drain timed out, falling back to batch")
            self._log_fallback("audio_drain_timeout", drain_timeout=round(drain_timeout, 2))
            self._is_running = False
            self._clear_audio_queue()
            stale = self._drop_conversation()
            self._close_conversation(stale)
            if pcm_data:
                if self._callback:
                    self._callback.mark_client_event(
                        "client.fallback.started",
                        reason="audio_drain_timeout",
                    )
                self._finish_active_trace(
                    pcm_data,
                    result_source="batch_fallback",
                    fallback_reason="audio_drain_timeout",
                )
                result = self._transcribe_batch_fallback(pcm_data)
                self._last_metering = self._batch_fallback.get_last_metering()
                return result
            self._finish_active_trace(
                pcm_data,
                result_source="empty",
                fallback_reason="audio_drain_timeout_no_pcm",
            )
            return ""

        self._is_running = False
        model_info = get_asr_model_info(self._session_model_id)
        is_omni = bool(model_info and model_info.get("input_audio_transcription_model") is not None)
        transcript_result = ""
        response_result = ""
        response_requested = False
        result_source = "empty"
        response_fallback_reason = ""
        audio_duration_seconds = _pcm_duration_seconds(
            pcm_data,
            self.config.audio.sample_rate,
            self.config.audio.channels,
        )
        direct_offline_model = _get_direct_offline_fallback_model(
            self._session_model_id,
            audio_duration_seconds,
        )
        skip_inline_response = bool(pcm_data and is_omni and direct_offline_model)
        response_start_timeout = min(
            max(timeout, INLINE_RESPONSE_START_TIMEOUT_SECONDS),
            _adaptive_response_start_timeout(audio_duration_seconds),
        )
        response_complete_timeout = _adaptive_response_complete_timeout(
            audio_duration_seconds,
            base_timeout=timeout,
        )

        if self._conversation:
            with self._lock:
                self._conversation_is_clean = False
            try:
                self._conversation.commit()
                if self._callback:
                    self._callback.mark_client_event("client.commit")
            except Exception:
                pass

            should_request_response = bool(
                self._callback
                and not skip_inline_response
                and _should_start_inline_response_now(model_info, self._callback)
            )
            if skip_inline_response:
                print(
                    "[StreamingASR] Long recording "
                    f"({audio_duration_seconds:.1f}s) using realtime transcript; "
                    "skipping inline response"
                )
                if self._callback:
                    self._callback.mark_client_event(
                        "client.response.skipped",
                        reason="long_audio_realtime_transcript",
                    )
            if should_request_response:
                try:
                    response_requested = True
                    if self._callback:
                        self._callback.mark_client_event("client.response.requested")
                    self._conversation.create_response()
                except Exception as response_error:
                    should_request_response = False
                    print(f"[StreamingASR] Inline polish response failed: {response_error}")

            if should_request_response and self._callback:
                response_started = self._callback.wait_for_response_started(
                    timeout=response_start_timeout
                )
                if response_started:
                    response_completed = self._callback.wait_for_response_complete(
                        timeout=response_complete_timeout
                    )
                    response_result, response_source = _get_completed_response_result(
                        self._callback
                    )
                    if not (response_completed and response_result and response_source):
                        response_result = ""
                        response_fallback_reason = "response_incomplete"
                    else:
                        result_source = response_source
                else:
                    response_fallback_reason = "response_not_started"

            if self._callback and not response_result:
                transcript_timeout = timeout
                if response_fallback_reason:
                    transcript_timeout = min(
                        timeout,
                        INLINE_RESPONSE_TRANSCRIPT_TIMEOUT_SECONDS,
                    )
                self._callback.wait_for_transcription_complete(timeout=transcript_timeout)
                transcript_result = self._callback.get_full_text().strip()
                result_source = "transcript" if transcript_result else "empty"

                if response_fallback_reason:
                    if response_fallback_reason == "response_not_started":
                        late_response_text, late_response_source, late_started = (
                            _try_finalize_late_response(
                                self._callback,
                                response_complete_timeout,
                            )
                        )
                        if late_response_text and late_response_source:
                            response_result = late_response_text
                            result_source = late_response_source
                            response_fallback_reason = ""
                        elif late_started:
                            response_fallback_reason = "response_incomplete"

                if response_fallback_reason:
                    if self._callback:
                        self._callback.mark_client_event(
                            "client.fallback.started",
                            reason=response_fallback_reason,
                        )
                    self._log_fallback(
                        response_fallback_reason,
                        duration_s=round(audio_duration_seconds, 2),
                    )
                    (
                        transcript_result,
                        result_source,
                    ) = self._batch_fallback._recover_failed_omni_response(
                        pcm_data or b"",
                        self._session_model_id,
                        transcript_result,
                        response_fallback_reason,
                        trace=self._active_trace,
                        context_instruction=self._context_instruction,
                    )

            if not is_omni:
                try:
                    self._conversation.end_session(timeout=5)
                    if self._callback:
                        self._callback.wait_for_complete(timeout=5.0)
                except Exception:
                    pass

        result = response_result or transcript_result or (
            self._callback.get_full_text() if self._callback else ""
        )

        # If streaming returned nothing but we have PCM data, try batch
        if not result.strip() and pcm_data and len(pcm_data) >= 3200:
            print("[StreamingASR] Empty result, falling back to batch")
            if self._callback:
                self._callback.mark_client_event(
                    "client.fallback.started",
                    reason="empty_result",
                )
            self._log_fallback("empty_result")
            self._finish_active_trace(
                pcm_data,
                result_source="batch_fallback",
                response_requested=response_requested,
                fallback_reason="empty_result",
            )
            result = self._transcribe_batch_fallback(pcm_data)
            self._last_metering = self._batch_fallback.get_last_metering()
        else:
            self._finish_active_trace(
                pcm_data,
                result_source=result_source if result.strip() else "empty",
                response_requested=response_requested,
                fallback_reason=response_fallback_reason,
            )

        self._is_running = False
        if _supports_warm_realtime_session(model_info):
            self._start_warm_keeper(model_info)
        else:
            stale = self._drop_conversation()
            self._close_conversation(stale)
        self._log_queue_state(
            "session_stop",
            result_source=result_source if result.strip() else "empty",
            response_requested=response_requested,
            fallback_reason=response_fallback_reason or "-",
        )
        return result

    def is_running(self) -> bool:
        """Check if ASR is currently running."""
        return self._is_running

    def get_last_metering(self) -> dict[str, Any] | None:
        return dict(self._last_metering) if self._last_metering else None

    def reset(self) -> None:
        """Reset the ASR engine state."""
        self._is_running = False
        self._accepting_audio = False
        self._clear_audio_queue()
        self._stop_warm_keeper()
        stale = self._drop_conversation()
        self._close_conversation(stale)
        self._active_trace = None
        self._trace_warm_reused = False
        self._last_metering = None
        self._context_instruction = ""
        with self._lock:
            self._audio_queue_high_watermark = 0
        if self._callback:
            self._callback.reset()

    def close(self) -> None:
        """Release background resources owned by the engine."""
        self.reset()
        if self._callback:
            self._callback.close()
        self._sender_shutdown.set()
        try:
            self._audio_queue.put_nowait(_AUDIO_QUEUE_STOP)
        except queue.Full:
            self._clear_audio_queue()
            self._audio_queue.put_nowait(_AUDIO_QUEUE_STOP)
        self._sender_thread.join(timeout=1.0)


if __name__ == "__main__":
    import time

    from .audio_recorder import AudioRecorder

    print("Testing BatchASREngine...")
    print("Recording for 5 seconds, speak now...")

    recorder = AudioRecorder()
    recorder.start()
    time.sleep(5)
    pcm_data = recorder.stop()

    print(f"Recorded {len(pcm_data)} bytes")

    asr = BatchASREngine()
    text = asr.transcribe(pcm_data)
    print(f"Transcription: {text}")
