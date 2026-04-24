"""Shared post-recording dictation workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from ..config import asr_model_handles_inline_polish
from ..infrastructure.pricing import merge_billing


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
    ) -> None:
        self.config = config
        self._asr_engine = asr_engine
        self._keyboard = keyboard
        self._recording_store = recording_store
        self._normalize_text = normalize_text or (lambda text: text)

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
    ) -> DictationWorkflowResult:
        get_asr_metering = getattr(self._asr_engine, "get_last_metering", None)

        def _aborted() -> bool:
            return bool(should_abort and should_abort())

        recording_id = None
        if self._recording_store is not None:
            try:
                recording_id = self._recording_store.save(
                    pcm_data,
                    mode_name,
                    asr_model,
                    language=self.config.asr.language,
                )
            except Exception as exc:
                print(f"[DictationWorkflow] Failed to save recording: {exc}")

        try:
            if on_processing_stage is not None:
                on_processing_stage("transcribing")
            raw_text = self._asr_engine.stop(pcm_data=pcm_data)
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
            polish_billing = None
            if self.config.enable_polish and text_polisher and not uses_inline_polish:
                try:
                    if on_processing_stage is not None:
                        on_processing_stage("polishing")
                    polish_result = text_polisher.polish(raw_text)
                    final_text = polish_result.polished_text
                    polish_billing = getattr(polish_result, "billing", None)
                except Exception as exc:
                    warnings.append(messages.polish_error(str(exc)))

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
            if self.config.auto_paste and not _aborted():
                self._keyboard.paste_text(final_text)
                pasted = True

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
