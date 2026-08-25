"""Shared post-recording dictation workflow."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..config import asr_model_handles_inline_polish
from ..domain.bilingual_formatting import format_bilingual_text
from ..infrastructure.pricing import merge_billing


def streamed_prefix_tail(full_text: str, streamed_raw_text: str) -> Optional[str]:
    """Return the remainder of ``full_text`` after ``streamed_raw_text``.

    The streaming ASR engine accumulates its full text by concatenating each
    finalized segment transcript directly (``full_text += transcript``, no
    separator), while the streaming-paste path inserts its own whitespace
    between pasted segments. Alignment therefore ignores all whitespace.

    Returns ``None`` when the already-streamed text cannot be aligned as a
    prefix of the final aggregated text. Callers must treat that as
    "paste nothing more": losing the tail is acceptable, duplicating it is
    not.
    """
    prefix_chars = [char for char in streamed_raw_text if not char.isspace()]
    if not prefix_chars:
        return full_text
    consumed = 0
    for index, char in enumerate(full_text):
        if consumed >= len(prefix_chars):
            return full_text[index:]
        if char.isspace():
            continue
        if char != prefix_chars[consumed]:
            return None
        consumed += 1
    return "" if consumed >= len(prefix_chars) else None


@dataclass
class DictationWorkflowResult:
    """Outcome of finishing a recorded dictation."""

    raw_text: str = ""
    final_text: str = ""
    pasted: bool = False
    error_code: str | None = None
    error_message: str | None = None
    warnings: list[str] = field(default_factory=list)
    recording_id: str | None = None
    billing: dict | None = None


class DictationWorkflow:
    """Own the shared record -> transcribe -> normalize -> polish -> paste flow."""

    def __init__(
        self,
        *,
        config,
        asr_engine,
        keyboard,
        recording_store=None,
        normalize_text: Callable[[str], str] | None = None,
        dictionary_learning=None,
    ) -> None:
        self.config = config
        self._asr_engine = asr_engine
        self._keyboard = keyboard
        self._recording_store = recording_store
        self._normalize_text = normalize_text or (lambda text: text)
        self._dictionary_learning = dictionary_learning

    @staticmethod
    def _streaming_alignment_warning(messages) -> str:
        message = getattr(messages, "streaming_paste_mismatch", None)
        if isinstance(message, str) and message:
            return message
        return (
            "Streaming paste could not be aligned with the final "
            "transcription; the remaining text was not pasted to avoid "
            "duplicates."
        )

    def finish_recording(
        self,
        pcm_data: bytes,
        *,
        mode_name: str,
        asr_model: str,
        text_polisher: object | None,
        messages,
        on_processing_stage: Optional[Callable[[str], None]] = None,
        should_abort: Optional[Callable[[], bool]] = None,
        streamed_raw_text: str | None = None,
    ) -> DictationWorkflowResult:
        """Finish one recording.

        ``streamed_raw_text`` is only supplied by sessions that already
        pasted finalized segments while recording (streaming paste). It is
        the raw concatenation of the segment transcripts that were pasted.
        When present, the finish flow skips second-stage polish (streamed
        segments were unpolished, so the tail must stay unpolished too) and
        pastes only the remaining tail of the aggregated text.
        """
        get_asr_metering = getattr(self._asr_engine, "get_last_metering", None)

        def _aborted() -> bool:
            return bool(should_abort and should_abort())

        # Persist the recording on a background thread so local disk I/O
        # (WAV write + index fsync) overlaps with the network-bound ASR
        # finalization instead of delaying it. The thread is always joined
        # (without timeout: this is a local operation) before any
        # recording_store.update() call and before this method returns, so
        # retry transcription can rely on the file being fully on disk.
        recording_id = None
        save_result: dict[str, str | None] = {"recording_id": None}
        save_thread: threading.Thread | None = None
        if self._recording_store is not None:
            def _save_recording() -> None:
                try:
                    save_result["recording_id"] = self._recording_store.save(
                        pcm_data,
                        mode_name,
                        asr_model,
                        language=self.config.asr.language,
                    )
                except Exception as exc:
                    print(f"[DictationWorkflow] Failed to save recording: {exc}")

            save_thread = threading.Thread(
                target=_save_recording,
                name="vocal-more-recording-save",
                daemon=True,
            )
            try:
                save_thread.start()
            except Exception as exc:
                # Keep the original swallow-and-continue semantics: if the
                # worker cannot even start, proceed without a recording_id.
                print(f"[DictationWorkflow] Failed to save recording: {exc}")
                save_thread = None

        def _join_save() -> None:
            nonlocal recording_id
            if save_thread is not None:
                save_thread.join()
            recording_id = save_result["recording_id"]

        try:
            try:
                if on_processing_stage is not None:
                    on_processing_stage("transcribing")
                raw_text = self._asr_engine.stop(pcm_data=pcm_data)
            finally:
                # Even when ASR finalization fails or the session was
                # aborted, wait for the save thread so no orphan thread
                # outlives this workflow and recording_id is final before
                # any update() or return below.
                _join_save()
            asr_billing = (
                get_asr_metering()
                if callable(get_asr_metering)
                else None
            )
            if not raw_text.strip():
                error_message = messages.empty_transcription
                if recording_id and self._recording_store is not None:
                    self._recording_store.update(
                        recording_id,
                        "failed",
                        error=error_message,
                        billing=merge_billing(asr_billing),
                    )
                return DictationWorkflowResult(
                    error_code="empty_transcription",
                    error_message=error_message,
                    recording_id=recording_id,
                    billing=merge_billing(asr_billing),
                )

            final_text = self._normalize_text(raw_text)
            warnings: list[str] = []
            if _aborted():
                return DictationWorkflowResult(
                    raw_text=raw_text,
                    final_text=final_text,
                    recording_id=recording_id,
                    billing=merge_billing(asr_billing),
                )
            uses_inline_polish = asr_model_handles_inline_polish(asr_model)
            # A streaming-paste session already inserted unpolished segment
            # text while recording; polishing only the tail now would mix
            # two writing styles in one document, so the first-phase
            # contract is: streamed session => no second-stage polish.
            streamed_session = bool(streamed_raw_text)
            polish_billing = None
            if (
                self.config.enable_polish
                and text_polisher
                and not uses_inline_polish
                and not streamed_session
            ):
                try:
                    if on_processing_stage is not None:
                        on_processing_stage("polishing")
                    polish_result = text_polisher.polish(raw_text)
                    final_text = polish_result.polished_text
                    polish_billing = getattr(polish_result, "billing", None)
                except Exception as exc:
                    warnings.append(messages.polish_error(str(exc)))

            # Deterministic bilingual formatting: pure-local post-processing
            # applied to both the normalize-only and polished paths, after
            # final_text is settled and before any update/paste. Always on —
            # the rules are conservative and lossless, so no config switch.
            # (Command mode output must never go through this: see
            # CommandWorkflow, which does not use this workflow.)
            final_text = format_bilingual_text(final_text)

            billing = merge_billing(asr_billing, polish_billing)
            if recording_id and self._recording_store is not None:
                self._recording_store.update(
                    recording_id,
                    "success",
                    raw_text,
                    error=None,
                    billing=billing,
                )

            pasted = False
            paste_payload: str | None = None
            if self.config.auto_paste and not _aborted():
                if streamed_session:
                    tail_raw = streamed_prefix_tail(
                        raw_text, streamed_raw_text or ""
                    )
                    if tail_raw is None:
                        # The aggregated text cannot be aligned with what
                        # was already pasted (e.g. a fallback transcription
                        # rewrote it). Never guess: do not paste again.
                        warnings.append(
                            self._streaming_alignment_warning(messages)
                        )
                    else:
                        # Segments were pasted during recording, so text has
                        # reached the target app even when nothing is left.
                        pasted = True
                        tail_final = format_bilingual_text(
                            self._normalize_text(tail_raw)
                        ).strip()
                        if tail_final:
                            # One leading space separates the tail from the
                            # last streamed segment, mirroring the segment
                            # separator used during recording.
                            paste_payload = f" {tail_final}"
                else:
                    paste_payload = final_text

            if paste_payload is not None:
                learning_ticket = None
                if self._dictionary_learning is not None:
                    try:
                        learning_ticket = self._dictionary_learning.prepare_paste(
                            raw_text=raw_text,
                            pasted_text=paste_payload,
                            recording_id=recording_id,
                            mode_name=mode_name,
                        )
                    except Exception as exc:
                        print(
                            "[DictationWorkflow] Dictionary observation "
                            f"preparation failed: {exc}"
                        )
                self._keyboard.paste_text(paste_payload)
                pasted = True
                if learning_ticket is not None:
                    try:
                        self._dictionary_learning.observe_after_paste(
                            learning_ticket
                        )
                    except Exception as exc:
                        print(
                            "[DictationWorkflow] Dictionary observation "
                            f"startup failed: {exc}"
                        )

            return DictationWorkflowResult(
                raw_text=raw_text,
                final_text=final_text,
                pasted=pasted,
                warnings=warnings,
                recording_id=recording_id,
                billing=billing,
            )
        except Exception as exc:
            asr_billing = (
                get_asr_metering()
                if callable(get_asr_metering)
                else None
            )
            if recording_id and self._recording_store is not None:
                self._recording_store.update(
                    recording_id,
                    "failed",
                    error=str(exc),
                    billing=merge_billing(asr_billing),
                )
            return DictationWorkflowResult(
                error_code="processing_error",
                error_message=messages.processing_error(str(exc)),
                recording_id=recording_id,
                billing=merge_billing(asr_billing),
            )
