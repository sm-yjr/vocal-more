"""Shared post-recording dictation workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from ..config import asr_model_handles_inline_polish
from ..core.text_output import PasteOutcome
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
        keyboard=None,
        text_output=None,
        recording_store=None,
        normalize_text: Callable[[str], str] | None = None,
        dictionary_learning=None,
    ) -> None:
        self.config = config
        self._asr_engine = asr_engine
        # ``keyboard`` is the pre-Linux constructor name.  Keep accepting it
        # for existing callers while making the architecture boundary explicit
        # for new composition roots.
        self._text_output = text_output if text_output is not None else keyboard
        if self._text_output is None:
            raise TypeError("DictationWorkflow requires a text_output port")
        self._recording_store = recording_store
        self._normalize_text = normalize_text or (lambda text: text)
        self._dictionary_learning = dictionary_learning

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
            pasted = False
            if self.config.auto_paste and not _aborted():
                learning_ticket = None
                if self._dictionary_learning is not None:
                    try:
                        learning_ticket = self._dictionary_learning.prepare_paste(
                            raw_text=raw_text,
                            pasted_text=final_text,
                            recording_id=recording_id,
                            mode_name=mode_name,
                        )
                    except Exception as exc:
                        print(
                            "[DictationWorkflow] Dictionary observation "
                            f"preparation failed: {exc}"
                        )
                paste_result = self._text_output.paste_text(final_text)
                if isinstance(paste_result, PasteOutcome):
                    pasted = paste_result.success
                    paste_error = paste_result.error
                elif isinstance(paste_result, bool):
                    # A few lightweight integrations used a boolean before
                    # PasteOutcome was introduced.  Preserve an explicit
                    # ``False`` rather than accidentally reporting success.
                    pasted = paste_result
                    paste_error = None
                elif paste_result is None:
                    # Legacy output adapters returned None on success.
                    pasted = True
                    paste_error = None
                else:
                    # Keep third-party/test doubles that predate PasteOutcome
                    # successful unless they expose an explicit ``success``
                    # field.  This is a one-way compatibility path; built-in
                    # adapters always return PasteOutcome.
                    explicit_success = getattr(paste_result, "success", None)
                    if explicit_success is None:
                        pasted = True
                        paste_error = None
                    else:
                        pasted = bool(explicit_success)
                        paste_error = getattr(paste_result, "error", None)

                if pasted and learning_ticket is not None:
                    try:
                        self._dictionary_learning.observe_after_paste(
                            learning_ticket
                        )
                    except Exception as exc:
                        print(
                            "[DictationWorkflow] Dictionary observation "
                            f"startup failed: {exc}"
                        )

                if not pasted:
                    detail = str(paste_error or "paste failed")
                    if recording_id and self._recording_store is not None:
                        self._recording_store.update(
                            recording_id,
                            "failed",
                            raw_text,
                            error=detail,
                            billing=billing,
                        )
                    paste_error_factory = getattr(messages, "paste_error", None)
                    error_message = (
                        paste_error_factory(detail)
                        if callable(paste_error_factory)
                        else (
                            messages.processing_error(detail)
                            if callable(getattr(messages, "processing_error", None))
                            else f"Paste failed: {detail}"
                        )
                    )
                    return DictationWorkflowResult(
                        raw_text=raw_text,
                        final_text=final_text,
                        pasted=False,
                        error_code="paste_failed",
                        error_message=error_message,
                        warnings=warnings,
                        recording_id=recording_id,
                        billing=billing,
                    )

            if recording_id and self._recording_store is not None:
                self._recording_store.update(
                    recording_id,
                    "success",
                    raw_text,
                    error=None,
                    billing=billing,
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
