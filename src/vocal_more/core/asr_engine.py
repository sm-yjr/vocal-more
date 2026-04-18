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
from typing import Callable, Optional

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
)
from .text_polisher import (
    TextPolisher,
    build_omni_inline_polish_instructions,
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
WARM_SESSION_TTL_SECONDS = 15.0
MAX_ADAPTIVE_RESPONSE_START_TIMEOUT_SECONDS = 20.0
MAX_ADAPTIVE_RESPONSE_COMPLETE_TIMEOUT_SECONDS = 90.0
STREAMING_AUDIO_QUEUE_MAX_CHUNKS = 64

_AUDIO_QUEUE_STOP = object()
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
                config.llm
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
    if not model_id.endswith("-realtime"):
        return None
    fallback_model = model_id.removesuffix("-realtime")
    fallback_info = get_asr_model_info(fallback_model)
    if fallback_info and fallback_info.get("transport") == "omni_offline":
        return fallback_model
    return None


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
    fallback_model = _get_omni_offline_fallback_model(model_id)
    if (
        fallback_model
        and duration_seconds >= OMNI_REALTIME_DIRECT_OFFLINE_THRESHOLD_SECONDS
    ):
        return fallback_model
    return None


def _join_transcript_segments(segments: list[str]) -> str:
    """Combine chunk transcripts without collapsing natural punctuation boundaries."""
    normalized = [segment.strip() for segment in segments if segment and segment.strip()]
    if not normalized:
        return ""

    result = normalized[0]
    trailing_punctuation = "。！？!?\n"
    leading_punctuation = "，。！？、；：,.!?;:"

    for segment in normalized[1:]:
        if not result:
            result = segment
            continue
        if result.endswith(tuple(trailing_punctuation)):
            result += segment
        elif segment[:1] in leading_punctuation:
            result += segment
        else:
            result += "\n" + segment

    return result


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
    ) -> str:
        chunks = self._split_audio_for_batch(audio_data)
        if len(chunks) <= 1:
            return self._transcribe_omni_offline(audio_data, model_override=model)

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
                chunk_text = self.transcribe(
                    chunk,
                    model_override=model,
                    language_override=language_override,
                    allow_chunking=False,
                )
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
    ) -> str:
        """Transcribe complete audio data.

        Routing is based on the selected model's catalog ``transport`` field
        rather than a separate backend string.
        """
        _apply_dashscope_api_key(self.config)
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
                return self.transcribe(
                    audio_data,
                    model_override=fallback_model,
                    language_override=language_override,
                    allow_chunking=True,
                )

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
                    return self._transcribe_chunked_audio(
                        audio_data,
                        model=model,
                        language_override=language_override,
                    )
            return self._transcribe_omni_offline(audio_data, model_override=model)

        return self._transcribe_realtime_ws(
            audio_data,
            model_override=model,
            language_override=language_override,
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
    ) -> tuple[str, str]:
        fallback_model = _get_omni_offline_fallback_model(model)
        if fallback_model and audio_data:
            print(
                "[BatchASR] Omni realtime response "
                f"{reason}, falling back to {fallback_model} "
                f"({_format_trace_ids(trace)})"
            )
            try:
                return (
                    self.transcribe(audio_data, model_override=fallback_model),
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
                polished = TextPolisher().polish(transcript_text)
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
            self._dump_debug_artifacts(audio_data, trace)
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    def _transcribe_omni_offline(self, audio_data: bytes, model_override: Optional[str] = None) -> str:
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

            if self.config.enable_polish:
                system_prompt = build_omni_inline_polish_instructions(self.config.llm)
            else:
                system_prompt = "请将以下音频准确转录为文字，直接输出转录结果。"

            from openai import OpenAI
            client = OpenAI(
                api_key=dashscope.api_key or os.environ.get("DASHSCOPE_API_KEY", ""),
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            )

            t0 = time.time()
            completion = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
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
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        result_text += delta.content

            elapsed = time.time() - t0
            result_text = result_text.strip()
            trace.result_text = result_text
            print(f"[BatchASR] Omni offline result ({elapsed:.1f}s): '{result_text}'")
            return result_text
        except Exception as exc:
            trace.error = str(exc)
            raise
        finally:
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
        print("[StreamingASR] Connection opened")
        self._record_event("socket.open")

    def on_close(self, code, msg):
        print(f"[StreamingASR] Connection closed: code={code}, msg={msg}")
        self._complete_event.set()
        self._session_updated.set()
        self._transcription_completed.set()
        self._response_started.set()
        self._response_completed.set()
        self._record_event("socket.close", code=code, msg=msg)
        if self._on_complete:
            self._on_complete()

    def on_event(self, response):
        try:
            event_type = response.get("type", "")
            metadata = _update_trace_ids_from_response(
                self._debug_trace,
                event_type,
                response,
            )
            self._record_event(event_type, **metadata)

            if event_type == "session.created":
                session_id = response.get("session", {}).get("id", "unknown")
                print(f"[StreamingASR] Session created: {session_id}")

            elif event_type == "session.updated":
                print("[StreamingASR] Session updated, ready")
                self._session_updated.set()

            elif event_type == "conversation.item.input_audio_transcription.text":
                text = response.get("text", "") or response.get("stash", "")
                if text and self._on_partial:
                    self._on_partial(ASRResult(text=text, is_final=False))
                if text and self._debug_trace is not None:
                    self._debug_trace.partial_texts.append(text)
                    self._record_event(event_type, **metadata, text=text)

            elif event_type == "conversation.item.input_audio_transcription.completed":
                transcript = response.get("transcript", "")
                with self._lock:
                    self._full_text += transcript
                    accumulated = self._full_text
                self._transcription_completed.set()
                if transcript and self._debug_trace is not None:
                    self._debug_trace.final_transcripts.append(transcript)
                    self._record_event(event_type, **metadata, transcript=transcript)
                if self._on_partial:
                    self._on_partial(ASRResult(text=accumulated, is_final=False))
                if self._on_final:
                    self._on_final(ASRResult(text=transcript, is_final=True))

            elif event_type == "response.text.delta":
                delta = response.get("delta", "")
                if delta:
                    self._response_started.set()
                    with self._lock:
                        self._response_text += delta
                        response_text = self._response_text
                    self._record_event(event_type, **metadata, delta=delta)
                    if self._on_partial:
                        self._on_partial(ASRResult(text=response_text, is_final=False))

            elif event_type == "response.text.done":
                text = response.get("text", "")
                self._response_started.set()
                self._response_text_done_received = True
                with self._lock:
                    self._response_text = _prefer_longer_text(
                        self._response_text,
                        text,
                    )
                    response_text = self._response_text
                self._record_event(event_type, **metadata, text=text)
                if self._on_partial and response_text:
                    self._on_partial(ASRResult(text=response_text, is_final=False))

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
                    response_text = self._response_text
                if part_text:
                    self._record_event(event_type, **metadata, text=part_text)
                    if self._on_partial and response_text:
                        self._on_partial(ASRResult(text=response_text, is_final=False))

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
                    response_text = self._response_text
                self._response_completed.set()
                self._record_event(event_type, **metadata, status=status, text=item_text)
                if self._on_partial and response_text:
                    self._on_partial(ASRResult(text=response_text, is_final=False))

            elif event_type == "response.created":
                self._response_started.set()

            elif event_type == "response.done":
                self._response_started.set()
                self._response_done_received = True
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
                if transcript:
                    with self._lock:
                        if not self._full_text:
                            self._full_text = transcript
                    if self._debug_trace is not None:
                        self._debug_trace.final_transcripts.append(transcript)
                self._complete_event.set()
                self._record_event(event_type, **metadata, transcript=transcript)

            elif event_type == "error":
                error_msg = response.get("error", {}).get("message", str(response))
                self._transcription_completed.set()
                self._record_event(event_type, **metadata, error=error_msg)
                if self._on_error:
                    self._on_error(error_msg)

        except Exception as e:
            if self._on_error:
                self._on_error(str(e))

    def get_full_text(self) -> str:
        with self._lock:
            return self._full_text

    def wait_for_session_updated(self, timeout: float = 10.0) -> bool:
        return self._session_updated.wait(timeout=timeout)

    def wait_for_transcription_complete(self, timeout: float = 30.0) -> bool:
        return self._transcription_completed.wait(timeout=timeout)

    def wait_for_complete(self, timeout: float = 10.0) -> bool:
        return self._complete_event.wait(timeout=timeout)

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
            maxsize=STREAMING_AUDIO_QUEUE_MAX_CHUNKS
        )
        self._pending_audio_chunks = 0
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
        self._warm_close_timer: Optional[threading.Timer] = None
        self._active_trace: Optional[ASRDebugTrace] = None
        self._trace_warm_reused = False

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
        self._batch_fallback._dump_debug_artifacts(pcm_data or b"", trace)

    def _cancel_warm_close(self) -> None:
        timer: Optional[threading.Timer] = None
        with self._lock:
            timer = self._warm_close_timer
            self._warm_close_timer = None
        if timer is not None:
            timer.cancel()

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
            self._session_ready = False
        return conversation

    def _close_warm_session_if_idle(self) -> None:
        conversation = None
        with self._lock:
            self._warm_close_timer = None
            if self._is_running:
                return
            conversation = self._conversation
            self._conversation = None
            self._conversation_model_id = None
            self._session_ready = False
        self._close_conversation(conversation)

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
        print(f"[StreamingASR] Realtime path degraded: reason={reason}")

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

    def _wait_for_audio_queue_drain(self, timeout: float = 5.0) -> bool:
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
            try:
                item = self._audio_queue.get(timeout=0.1)
            except queue.Empty:
                continue

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

    def _schedule_warm_close(self) -> None:
        self._cancel_warm_close()
        timer = threading.Timer(
            WARM_SESSION_TTL_SECONDS,
            self._close_warm_session_if_idle,
        )
        timer.daemon = True
        with self._lock:
            if self._is_running or self._conversation is None:
                return
            self._warm_close_timer = timer
        timer.start()

    def _can_reuse_warm_session(self, model_info: Optional[dict]) -> bool:
        return should_reuse_warm_session(
            supports_warm_session=_supports_warm_realtime_session(model_info),
            is_connected=_conversation_socket_connected(self._conversation),
            matches_model=(
                self._conversation is not None
                and self._conversation_model_id == self._session_model_id
            ),
        )

    def start(self) -> None:
        """Start the ASR session. Non-blocking — session setup runs in background."""
        if self._is_running:
            return

        _apply_dashscope_api_key(self.config)
        self._session_model_id = self.config.asr.model
        model_info = get_asr_model_info(self._session_model_id)
        transport = model_info["transport"] if model_info else self.config.asr.backend
        print(
            f"[StreamingASR] Starting session: model={self._session_model_id}, "
            f"backend={transport}, language={self.config.asr.language}"
        )

        self._is_running = True
        self._accepting_audio = True
        self._session_ready = False
        self._connect_failed = False
        self._connect_done = threading.Event()
        self._cancel_warm_close()
        self._trace_warm_reused = False
        self._clear_audio_queue()
        with self._lock:
            self._pending_audio_chunks = 0
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

        self._cancel_warm_close()
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

                if self._can_reuse_warm_session(model_info):
                    print("[StreamingASR] Reusing warm realtime session")
                    self._trace_warm_reused = True
                    if self._callback:
                        self._callback.mark_client_event("client.warm_session.reused")
                else:
                    if self._conversation is not None:
                        stale = self._drop_conversation()
                        self._close_conversation(stale)
                    self._conversation = OmniRealtimeConversation(
                        model=self._session_model_id,
                        url="wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
                        callback=self._callback,
                    )
                    self._conversation.connect()
                    self._conversation_model_id = self._session_model_id

                session_kwargs = _build_session_kwargs(model_info)
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
        with self._lock:
            if not self._is_running or not self._accepting_audio:
                return
            if self._streaming_degraded:
                return
            self._pending_audio_chunks += 1

        try:
            self._audio_queue.put_nowait(audio_chunk)
        except queue.Full:
            with self._audio_queue_drained:
                if self._pending_audio_chunks > 0:
                    self._pending_audio_chunks -= 1
                self._audio_queue_drained.notify_all()
            self._mark_streaming_degraded("audio_queue_full")
            return

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
                return self._batch_fallback.transcribe(pcm_data)
            return ""

        # If streaming connection failed, fall back to batch transcription
        with self._lock:
            failed = self._connect_failed
            degraded = self._streaming_degraded
            degraded_reason = self._streaming_degraded_reason

        if degraded:
            print("[StreamingASR] Realtime queue degraded; falling back to batch transcription")
            self._is_running = False
            self._clear_audio_queue()
            self._cancel_warm_close()
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
                return self._batch_fallback.transcribe(pcm_data)
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
                return self._batch_fallback.transcribe(pcm_data)
            self._finish_active_trace(
                pcm_data,
                result_source="empty",
                fallback_reason="connect_failed_no_pcm",
            )
            return ""

        if not self._wait_for_audio_queue_drain(timeout=5.0):
            print("[StreamingASR] Audio sender drain timed out, falling back to batch")
            self._is_running = False
            self._clear_audio_queue()
            self._cancel_warm_close()
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
                return self._batch_fallback.transcribe(pcm_data)
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
        response_start_timeout = min(
            max(timeout, INLINE_RESPONSE_START_TIMEOUT_SECONDS),
            _adaptive_response_start_timeout(audio_duration_seconds),
        )
        response_complete_timeout = _adaptive_response_complete_timeout(
            audio_duration_seconds,
            base_timeout=timeout,
        )

        if pcm_data and is_omni and direct_offline_model:
            print(
                "[StreamingASR] Long recording "
                f"({audio_duration_seconds:.1f}s) bypassing realtime response for "
                f"{self._session_model_id}; using {direct_offline_model} directly"
            )
            if self._callback:
                self._callback.mark_client_event(
                    "client.fallback.started",
                    reason="long_audio_direct_offline",
                )
            self._cancel_warm_close()
            stale = self._drop_conversation()
            self._close_conversation(stale)
            self._finish_active_trace(
                pcm_data,
                result_source="batch_fallback",
                fallback_reason="long_audio_direct_offline",
            )
            self._is_running = False
            return self._batch_fallback.transcribe(
                pcm_data,
                model_override=direct_offline_model,
            )

        if self._conversation:
            try:
                self._conversation.commit()
                if self._callback:
                    self._callback.mark_client_event("client.commit")
            except Exception:
                pass

            should_request_response = bool(
                self._callback
                and _should_start_inline_response_now(model_info, self._callback)
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
                    (
                        transcript_result,
                        result_source,
                    ) = self._batch_fallback._recover_failed_omni_response(
                        pcm_data or b"",
                        self._session_model_id,
                        transcript_result,
                        response_fallback_reason,
                        trace=self._active_trace,
                    )

            if not is_omni:
                try:
                    self._conversation.end_session(timeout=5)
                    if self._callback:
                        self._callback.wait_for_complete(timeout=5.0)
                except Exception:
                    pass

            if (
                _supports_warm_realtime_session(model_info)
                and not response_fallback_reason
            ):
                self._schedule_warm_close()
            else:
                stale = self._drop_conversation()
                self._close_conversation(stale)

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
            self._finish_active_trace(
                pcm_data,
                result_source="batch_fallback",
                response_requested=response_requested,
                fallback_reason="empty_result",
            )
            result = self._batch_fallback.transcribe(pcm_data)
        else:
            self._finish_active_trace(
                pcm_data,
                result_source=result_source if result.strip() else "empty",
                response_requested=response_requested,
                fallback_reason=response_fallback_reason,
            )

        self._is_running = False
        return result

    def is_running(self) -> bool:
        """Check if ASR is currently running."""
        return self._is_running

    def reset(self) -> None:
        """Reset the ASR engine state."""
        self._is_running = False
        self._accepting_audio = False
        self._clear_audio_queue()
        self._cancel_warm_close()
        stale = self._drop_conversation()
        self._close_conversation(stale)
        self._active_trace = None
        self._trace_warm_reused = False
        if self._callback:
            self._callback.reset()

    def close(self) -> None:
        """Release background resources owned by the engine."""
        self.reset()
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
