"""Shared post-recording dictation workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from ..config import asr_model_handles_inline_polish


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
    ) -> DictationWorkflowResult:
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
            if not raw_text.strip():
                error_message = messages.empty_transcription
                if recording_id and self._recording_store is not None:
                    self._recording_store.update(
                        recording_id,
                        "failed",
                        error=error_message,
                    )
                return DictationWorkflowResult(
                    error_code="empty_transcription",
                    error_message=error_message,
                    recording_id=recording_id,
                )

            if recording_id and self._recording_store is not None:
                self._recording_store.update(
                    recording_id,
                    "success",
                    raw_text,
                    error=None,
                )

            final_text = self._normalize_text(raw_text)
            warnings: list[str] = []
            uses_inline_polish = asr_model_handles_inline_polish(asr_model)
            if self.config.enable_polish and text_polisher and not uses_inline_polish:
                try:
                    if on_processing_stage is not None:
                        on_processing_stage("polishing")
                    polish_result = text_polisher.polish(raw_text)
                    final_text = polish_result.polished_text
                except Exception as exc:
                    warnings.append(messages.polish_error(str(exc)))

            pasted = False
            if self.config.auto_paste:
                self._keyboard.paste_text(final_text)
                pasted = True

            return DictationWorkflowResult(
                raw_text=raw_text,
                final_text=final_text,
                pasted=pasted,
                warnings=warnings,
                recording_id=recording_id,
            )
        except Exception as exc:
            if recording_id and self._recording_store is not None:
                self._recording_store.update(recording_id, "failed", error=str(exc))
            return DictationWorkflowResult(
                error_code="processing_error",
                error_message=messages.processing_error(str(exc)),
                recording_id=recording_id,
            )
