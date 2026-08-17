"""Linux composition root for the shared dictation RPC runtime."""

from __future__ import annotations

from collections.abc import Callable

from . import __version__
from .bootstrap import build_rpc_handler_dependencies
from .core.text_polisher import TextPolisher
from .infrastructure.linux_recording_store import build_linux_recording_store
from .modes.meeting import MeetingMode
from .modes.realtime_long import RealtimeLongMode
from .modes.walkie_talkie import WalkieTalkieMode
from .rpc_handler import RPCHandler


class LinuxRPCHandler(RPCHandler):
    """Shared RPC behavior with Linux-owned output and recording adapters."""

    def __init__(
        self,
        send_notification: Callable[[str, dict], None],
        *,
        text_output,
    ) -> None:
        self._send_notification = send_notification
        dependencies = build_rpc_handler_dependencies(
            self,
            send_notification=send_notification,
            text_polisher_factory=TextPolisher,
            recording_store_factory=build_linux_recording_store,
            walkie_talkie_factory=WalkieTalkieMode,
            realtime_long_factory=RealtimeLongMode,
            meeting_factory=MeetingMode,
            text_output_factory=lambda: text_output,
        )
        self._apply_dependencies(dependencies)

    def _handle_list_dictionary_learning(self, params: dict) -> list:
        if self._dictionary_learning is None:
            return []
        return self._dictionary_learning.list_recent(
            limit=max(1, min(200, int(params.get("limit", 100))))
        )

    def _handle_approve_dictionary_learning(self, params: dict) -> dict:
        return self._dictionary_learning_action("approve", params)

    def _handle_reject_dictionary_learning(self, params: dict) -> dict:
        return self._dictionary_learning_action("reject", params)

    def _handle_undo_dictionary_learning(self, params: dict) -> dict:
        return self._dictionary_learning_action("undo", params)

    def _dictionary_learning_action(self, action: str, params: dict) -> dict:
        job_id = str(params.get("id") or "").strip()
        if not job_id:
            raise ValueError("dictionary-learning id is required")
        runtime = self._dictionary_learning
        callback = getattr(runtime, action, None) if runtime is not None else None
        return {"ok": bool(callable(callback) and callback(job_id))}

    def _handle_get_context_summary(self, params: dict) -> dict:
        if self._context_personalization is None:
            return {"counts": {}, "total": 0}
        return self._context_personalization.summary()

    def _handle_reset_context(self, params: dict) -> dict:
        if self._context_personalization is not None:
            self._context_personalization.reset()
        return {"ok": True}

    def _handle_export_support_bundle(self, params: dict) -> dict:
        from .diagnostics import export_support_bundle

        path = export_support_bundle(
            config=self.config,
            recording_store=self._recording_store,
            environment_checks=(),
            app_version=__version__,
        )
        return {"ok": True, "path": str(path)}


__all__ = ["LinuxRPCHandler"]
