"""ASR engine module using DashScope Qwen ASR models."""

import base64
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import wave
from dataclasses import asdict, dataclass, field
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
from .text_polisher import (
    build_omni_inline_polish_instructions,
    should_polish_text,
)

REALTIME_CHUNK_SIZE = 3200
# The current public docs list qwen3-asr-flash as supporting audio up to 3 minutes / 10 MB.
SHORT_FILE_MAX_DURATION_SECONDS = 180
SHORT_FILE_MAX_BYTES = 10 * 1024 * 1024
INLINE_POLISH_DECISION_TIMEOUT_SECONDS = 0.2
WARM_SESSION_TTL_SECONDS = 15.0


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
    normalized = normalize_terms(transcript)
    return should_polish_text(config.llm, transcript, normalized)


def _should_start_inline_response_now(
    model_info: Optional[dict],
    callback,
    decision_timeout: float = INLINE_POLISH_DECISION_TIMEOUT_SECONDS,
) -> bool:
    """Decide whether to issue response.create immediately after commit.

    In smart mode, give transcript completion a brief chance to arrive so
    obviously tiny utterances can still skip polishing without blocking the
    whole tail on a full transcription wait.
    """
    config = get_config()
    if not (config.enable_polish and model_info and model_info.get("handles_inline_polish")):
        return False

    if config.llm.polish_mode != "smart":
        return True

    if callback.wait_for_transcription_complete(timeout=decision_timeout):
        transcript = callback.get_full_text().strip()
        if transcript:
            return _should_request_inline_polish(model_info, transcript)
    return True


def _supports_warm_realtime_session(model_info: Optional[dict]) -> bool:
    return bool(
        model_info
        and model_info.get("transport") == "realtime_ws"
        and model_info.get("input_audio_transcription_model") is not None
    )


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


@dataclass
class ASRDebugTrace:
    """Debug trace for a single batch transcription."""

    backend: str
    request_mode: str
    model: str
    sample_rate: int
    audio_bytes: int
    audio_duration_ms: float
    corpus_text: Optional[str]
    events: list[dict] = field(default_factory=list)
    partial_texts: list[str] = field(default_factory=list)
    final_transcripts: list[str] = field(default_factory=list)
    result_text: str = ""
    result_source: str = ""
    timings_ms: dict[str, Optional[float]] = field(default_factory=dict)
    response_requested: bool = False
    warm_session_reused: bool = False
    fallback_reason: str = ""
    recognition_timed_out: bool = False
    cleanup_timed_out: bool = False
    error: str = ""


def _event_time_ms(trace: ASRDebugTrace, event_type: str) -> Optional[float]:
    for event in trace.events:
        if event.get("type") == event_type:
            return event.get("t_ms")
    return None


def _build_trace_timings(trace: ASRDebugTrace) -> dict[str, Optional[float]]:
    timings = {
        "socket_open_ms": _event_time_ms(trace, "socket.open"),
        "session_ready_ms": _event_time_ms(trace, "session.updated"),
        "first_partial_ms": _event_time_ms(
            trace, "conversation.item.input_audio_transcription.text"
        ),
        "transcription_complete_ms": _event_time_ms(
            trace, "conversation.item.input_audio_transcription.completed"
        ),
        "commit_ms": _event_time_ms(trace, "client.commit"),
        "response_requested_ms": _event_time_ms(trace, "client.response.requested"),
        "response_first_delta_ms": _event_time_ms(trace, "response.text.delta"),
        "response_done_ms": _event_time_ms(trace, "response.done"),
        "result_selected_ms": _event_time_ms(trace, "client.result.selected"),
        "socket_close_ms": _event_time_ms(trace, "socket.close"),
    }
    timings["total_result_ms"] = timings["result_selected_ms"]
    return timings


def _finalize_trace(trace: ASRDebugTrace, result_source: str) -> None:
    trace.result_source = result_source
    trace.timings_ms = _build_trace_timings(trace)


def _print_trace_summary(trace: ASRDebugTrace) -> None:
    total = trace.timings_ms.get("total_result_ms")
    ready = trace.timings_ms.get("session_ready_ms")
    transcript = trace.timings_ms.get("transcription_complete_ms")
    response_done = trace.timings_ms.get("response_done_ms")
    print(
        "[ASRTiming] "
        f"mode={trace.request_mode} backend={trace.backend} model={trace.model} "
        f"source={trace.result_source or 'unknown'} warm={trace.warm_session_reused} "
        f"total_ms={total} ready_ms={ready} transcript_ms={transcript} "
        f"response_ms={response_done}"
    )


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
        self._started_at = time.perf_counter()
        self._debug_trace: Optional[ASRDebugTrace] = None
        self._response_text = ""
        self._response_done_received = False

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
        self._response_completed.set()
        self._record_event("socket.close", code=code, msg=msg)
        if self._on_complete:
            self._on_complete()

    def on_event(self, response):
        try:
            event_type = response.get("type", "")
            print(f"[ASRCallback] Event: {event_type}")
            self._record_event(event_type)

            if event_type == "session.created":
                session_id = response.get("session", {}).get("id", "unknown")
                print(f"[ASRCallback] Session created: {session_id}")
                self._record_event(event_type, session_id=session_id)

            elif event_type == "session.updated":
                print("[ASRCallback] Session updated, ready to receive audio")
                self._session_updated.set()

            elif event_type == "conversation.item.input_audio_transcription.text":
                text = response.get("text", "") or response.get("stash", "")
                if text and self._debug_trace is not None:
                    self._debug_trace.partial_texts.append(text)
                    self._record_event(event_type, text=text)
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
                    self._record_event(event_type, transcript=transcript)
                if self._on_final:
                    self._on_final(transcript)

            elif event_type == "response.text.delta":
                delta = response.get("delta", "")
                if delta:
                    with self._lock:
                        self._response_text += delta
                    self._record_event(event_type, delta=delta)

            elif event_type == "response.done":
                self._response_done_received = True
                self._response_completed.set()
                self._record_event(event_type)

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

            elif event_type == "error":
                error_msg = response.get("error", {}).get("message", str(response))
                print(f"[ASRCallback] Error: {error_msg}")
                self._transcription_completed.set()
                self._record_event(event_type, error=error_msg)
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

    def get_response_text(self) -> str:
        with self._lock:
            return self._response_text

    def did_receive_response_done(self) -> bool:
        return self._response_done_received

    def reset(self):
        with self._lock:
            self._full_text = ""
        self._session_finished.clear()
        self._session_updated.clear()
        self._transcription_completed.clear()
        self._response_text = ""
        self._response_done_received = False
        self._response_completed.clear()
        self._started_at = time.perf_counter()


class BatchASREngine:
    """Batch ASR for processing complete audio files."""

    def __init__(self):
        self.config = get_config()
        if self.config.api_key:
            dashscope.api_key = self.config.api_key

    def transcribe(self, audio_data: bytes, model_override: Optional[str] = None, language_override: Optional[str] = None) -> str:
        """Transcribe complete audio data.

        Routing is based on the selected model's catalog ``transport`` field
        rather than a separate backend string.
        """
        model = model_override or self.config.asr.model
        model_info = get_asr_model_info(model)
        transport = model_info["transport"] if model_info else self.config.asr.backend

        if transport == "short_file":
            if self._supports_short_file(audio_data):
                return self._transcribe_short_file(
                    audio_data,
                    model_override=model,
                    language_override=language_override,
                )

            print("[BatchASR] short_file backend skipped, falling back to realtime_ws")

        if transport == "omni_offline":
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
                response_completed = callback.wait_for_response_complete(timeout=30.0)
                response_text = callback.get_response_text().strip()
                if (
                    response_completed
                    and callback.did_receive_response_done()
                    and response_text
                ):
                    result_text = response_text
                    result_source = "response"
                else:
                    if not callback.wait_for_transcription_complete(timeout=30.0):
                        print("[BatchASR] Timeout waiting for transcription completion")
                        trace.recognition_timed_out = True
                    transcript_text = callback.get_full_text().strip()
                    result_text = transcript_text
                    result_source = "transcript" if transcript_text else "empty"
            else:
                if not callback.wait_for_transcription_complete(timeout=30.0):
                    print("[BatchASR] Timeout waiting for transcription completion")
                    trace.recognition_timed_out = True
                transcript_text = callback.get_full_text().strip()
                result_text = transcript_text
                result_source = "transcript" if transcript_text else "empty"

            trace.result_text = result_text
            trace.response_requested = response_requested
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

            result_text = ""
            for chunk in completion:
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
        self._response_text = ""
        self._response_done_received = False
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
        self._response_completed.set()
        self._record_event("socket.close", code=code, msg=msg)
        if self._on_complete:
            self._on_complete()

    def on_event(self, response):
        try:
            event_type = response.get("type", "")

            if event_type == "session.updated":
                print("[StreamingASR] Session updated, ready")
                self._session_updated.set()
                self._record_event(event_type)

            elif event_type == "conversation.item.input_audio_transcription.text":
                text = response.get("text", "") or response.get("stash", "")
                if text and self._on_partial:
                    self._on_partial(ASRResult(text=text, is_final=False))
                if text and self._debug_trace is not None:
                    self._debug_trace.partial_texts.append(text)
                    self._record_event(event_type, text=text)

            elif event_type == "conversation.item.input_audio_transcription.completed":
                transcript = response.get("transcript", "")
                with self._lock:
                    self._full_text += transcript
                    accumulated = self._full_text
                self._transcription_completed.set()
                if transcript and self._debug_trace is not None:
                    self._debug_trace.final_transcripts.append(transcript)
                    self._record_event(event_type, transcript=transcript)
                if self._on_partial:
                    self._on_partial(ASRResult(text=accumulated, is_final=False))
                if self._on_final:
                    self._on_final(ASRResult(text=transcript, is_final=True))

            elif event_type == "response.text.delta":
                delta = response.get("delta", "")
                if delta:
                    with self._lock:
                        self._response_text += delta
                        response_text = self._response_text
                    self._record_event(event_type, delta=delta)
                    if self._on_partial:
                        self._on_partial(ASRResult(text=response_text, is_final=False))

            elif event_type == "response.done":
                self._response_done_received = True
                self._response_completed.set()
                self._record_event(event_type)

            elif event_type == "session.finished":
                transcript = response.get("transcript", "")
                if transcript:
                    with self._lock:
                        if not self._full_text:
                            self._full_text = transcript
                    if self._debug_trace is not None:
                        self._debug_trace.final_transcripts.append(transcript)
                self._complete_event.set()
                self._record_event(event_type, transcript=transcript)

            elif event_type == "error":
                error_msg = response.get("error", {}).get("message", str(response))
                self._transcription_completed.set()
                self._record_event(event_type, error=error_msg)
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

    def get_response_text(self) -> str:
        with self._lock:
            return self._response_text

    def did_receive_response_done(self) -> bool:
        return self._response_done_received

    def reset(self):
        with self._lock:
            self._full_text = ""
            self._response_text = ""
            self._response_done_received = False
        self._session_updated.clear()
        self._transcription_completed.clear()
        self._complete_event.clear()
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
        self._session_ready = False
        self._connect_failed = False
        self._pending_chunks: list[bytes] = []
        self._lock = threading.Lock()
        self._batch_fallback = BatchASREngine()
        self._session_model_id = self.config.asr.model
        self._conversation_model_id: Optional[str] = None
        self._warm_close_timer: Optional[threading.Timer] = None
        self._active_trace: Optional[ASRDebugTrace] = None
        self._trace_warm_reused = False

        if self.config.api_key:
            dashscope.api_key = self.config.api_key

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
        return bool(
            _supports_warm_realtime_session(model_info)
            and self._conversation is not None
            and self._conversation_model_id == self._session_model_id
        )

    def start(self) -> None:
        """Start the ASR session. Non-blocking — session setup runs in background."""
        if self._is_running:
            return

        self._session_model_id = self.config.asr.model
        model_info = get_asr_model_info(self._session_model_id)
        transport = model_info["transport"] if model_info else self.config.asr.backend
        print(
            f"[StreamingASR] Starting session: model={self._session_model_id}, "
            f"backend={transport}, language={self.config.asr.language}"
        )

        self._is_running = True
        self._session_ready = False
        self._connect_failed = False
        self._connect_done = threading.Event()
        self._pending_chunks = []
        self._cancel_warm_close()
        self._trace_warm_reused = False
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

                # Session is ready — flush any audio that arrived during connection
                with self._lock:
                    self._session_ready = True
                    pending = self._pending_chunks
                    self._pending_chunks = []

                for chunk in pending:
                    audio_b64 = base64.b64encode(chunk).decode("ascii")
                    self._conversation.append_audio(audio_b64)

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
        """Send audio chunk to ASR. Buffers if session not yet ready."""
        if not self._is_running:
            return

        with self._lock:
            if not self._session_ready:
                self._pending_chunks.append(audio_chunk)
                return

        if self._conversation:
            audio_b64 = base64.b64encode(audio_chunk).decode("ascii")
            try:
                self._conversation.append_audio(audio_b64)
            except Exception:
                self._is_running = False
                with self._lock:
                    self._connect_failed = True
                stale = self._drop_conversation()
                self._close_conversation(stale)

    def stop(self, timeout: float = 30.0, pcm_data: Optional[bytes] = None) -> str:
        """Stop ASR: commit audio, wait for transcription, return result.

        Args:
            timeout: Max wait time for transcription result.
            pcm_data: Complete recorded PCM data for batch fallback.
        """
        if not self._is_running:
            return ""

        self._is_running = False

        # Wait for _connect() thread to finish (success or failure) before proceeding
        if not self._connect_done.wait(timeout=12.0):
            print("[StreamingASR] Timeout waiting for connection thread, falling back to batch")
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

        if failed:
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

        model_info = get_asr_model_info(self._session_model_id)
        is_omni = bool(model_info and model_info.get("input_audio_transcription_model") is not None)
        transcript_result = ""
        response_result = ""
        response_requested = False
        result_source = "empty"

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
                response_completed = self._callback.wait_for_response_complete(timeout=timeout)
                response_result = self._callback.get_response_text().strip()
                if not (
                    response_completed and self._callback.did_receive_response_done()
                ):
                    response_result = ""
                else:
                    result_source = "response"

            if self._callback and not response_result:
                self._callback.wait_for_transcription_complete(timeout=timeout)
                transcript_result = self._callback.get_full_text().strip()
                result_source = "transcript" if transcript_result else "empty"

            if not is_omni:
                try:
                    self._conversation.end_session(timeout=5)
                    if self._callback:
                        self._callback.wait_for_complete(timeout=5.0)
                except Exception:
                    pass

            if _supports_warm_realtime_session(model_info):
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
            )

        return result

    def is_running(self) -> bool:
        """Check if ASR is currently running."""
        return self._is_running

    def reset(self) -> None:
        """Reset the ASR engine state."""
        self._cancel_warm_close()
        stale = self._drop_conversation()
        self._close_conversation(stale)
        self._active_trace = None
        self._trace_warm_reused = False
        if self._callback:
            self._callback.reset()


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
