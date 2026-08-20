"""Post-recording workflow for one-pass spoken commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from ..infrastructure.pricing import merge_billing


@dataclass
class CommandWorkflowResult:
    final_text: str = ""
    pasted: bool = False
    error_message: str | None = None
    recording_id: str | None = None
    billing: dict | None = None


class CommandWorkflow:
    """Generate and paste an answer without dictation fallback or learning."""

    def __init__(self, *, config, asr_engine, keyboard, recording_store=None) -> None:
        self.config = config
        self._asr_engine = asr_engine
        self._keyboard = keyboard
        self._recording_store = recording_store

    def finish_recording(
        self,
        pcm_data: bytes,
        *,
        asr_model: str,
        empty_message: str,
        error_message: Callable[[str], str],
        on_processing_stage: Optional[Callable[[str], None]] = None,
        should_abort: Optional[Callable[[], bool]] = None,
    ) -> CommandWorkflowResult:
        recording_id = None
        if self._recording_store is not None:
            try:
                recording_id = self._recording_store.save(
                    pcm_data,
                    "command",
                    asr_model,
                    language=self.config.asr.language,
                )
            except Exception as exc:
                print(f"[CommandWorkflow] Failed to save recording: {exc}")

        try:
            if on_processing_stage is not None:
                on_processing_stage("understanding")
            answer = self._asr_engine.stop(pcm_data=pcm_data).strip()
            get_metering = getattr(self._asr_engine, "get_last_metering", None)
            billing = merge_billing(get_metering() if callable(get_metering) else None)
            if not answer:
                if recording_id and self._recording_store is not None:
                    self._recording_store.update(
                        recording_id,
                        "failed",
                        error=empty_message,
                        billing=billing,
                    )
                return CommandWorkflowResult(
                    error_message=empty_message,
                    recording_id=recording_id,
                    billing=billing,
                )

            if recording_id and self._recording_store is not None:
                self._recording_store.update(
                    recording_id,
                    "success",
                    answer,
                    error=None,
                    billing=billing,
                    command={"output": answer},
                )

            pasted = False
            aborted = bool(should_abort and should_abort())
            if self.config.auto_paste and not aborted:
                self._keyboard.paste_text(answer)
                pasted = True
            return CommandWorkflowResult(
                final_text=answer,
                pasted=pasted,
                recording_id=recording_id,
                billing=billing,
            )
        except Exception as exc:
            message = error_message(str(exc))
            if recording_id and self._recording_store is not None:
                self._recording_store.update(
                    recording_id,
                    "failed",
                    error=message,
                )
            return CommandWorkflowResult(
                error_message=message,
                recording_id=recording_id,
            )
