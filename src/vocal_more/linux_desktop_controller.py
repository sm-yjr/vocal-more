"""UI-independent controller for the GNOME/GTK Linux desktop host."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

from .domain.hotkey_gestures import HotkeyGestureAction, HotkeyGestureController
from .linux_desktop_contract import LINUX_ACCELERATORS, DesktopSnapshot

_STAGE_LABELS = {
    "zh": {
        "transcribing": "正在识别",
        "polishing": "正在润色",
        "meeting_transcribing": "正在转写会议",
        "meeting_notes": "正在生成会议纪要",
    },
    "en": {
        "transcribing": "Transcribing",
        "polishing": "Polishing",
        "meeting_transcribing": "Transcribing meeting",
        "meeting_notes": "Generating meeting notes",
    },
}


class LinuxDesktopController:
    """Serialize Shell commands and expose a privacy-safe desktop snapshot."""

    def __init__(
        self,
        *,
        config,
        handler,
        on_snapshot: Callable[[DesktopSnapshot], None],
        on_show_settings: Callable[[], None],
        on_quit: Callable[[], None],
        on_notice: Callable[[str, str], None] | None = None,
    ) -> None:
        self.config = config
        self._handler = handler
        self._on_snapshot = on_snapshot
        self._on_show_settings = on_show_settings
        self._on_quit = on_quit
        self._on_notice = on_notice or (lambda _title, _message: None)
        self._gesture = HotkeyGestureController()
        self._lock = threading.Lock()
        self._state = "idle"
        self._mode = config.default_mode
        self._stage = ""
        self._audio_level = 0.0
        self._closed = False
        self._commands = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="vocal-more-linux-commands",
        )

        initialized = self._handler.dispatch("initialize", {})
        self._state = str(initialized.get("state") or "idle")
        self._mode = str(initialized.get("current_mode") or config.default_mode)
        self.publish()

    def snapshot(self) -> DesktopSnapshot:
        with self._lock:
            state = self._state
            mode = self._mode
            stage = self._stage
            audio_level = self._audio_level
        accelerator = str(
            getattr(getattr(self.config, "hotkey", None), "linux_accelerator", "F8")
        )
        if accelerator not in LINUX_ACCELERATORS:
            accelerator = "F8"
        return DesktopSnapshot(
            state=state,
            mode=mode,
            language=self.config.ui.language,
            stage=stage,
            audio_level=audio_level,
            trigger_label=accelerator,
            can_cancel=state not in {"idle", "failed"},
            auto_paste=bool(self.config.auto_paste),
            backend_ready=not self._closed,
        )

    def publish(self) -> None:
        if not self._closed:
            self._on_snapshot(self.snapshot())

    def submit_trigger_pressed(self) -> Future | None:
        return self._submit(self._handle_trigger_pressed, time.monotonic())

    def submit_trigger_released(self) -> Future | None:
        return self._submit(self._handle_trigger_released, time.monotonic())

    def submit_cancel(self) -> Future | None:
        return self._submit(self._handle_cancel)

    def submit_set_mode(self, mode: str) -> Future | None:
        return self._submit(self._set_mode, mode)

    def submit_set_auto_paste(self, enabled: bool) -> Future | None:
        return self._submit(self._set_auto_paste, bool(enabled))

    def show_settings(self) -> None:
        self._on_show_settings()

    def request_quit(self) -> None:
        self._on_quit()

    def handle_runtime_notification(self, method: str, params: dict) -> None:
        if self._closed:
            return
        if method == "state_changed":
            state = str(params.get("state") or "idle")
            with self._lock:
                self._state = state
                if state == "idle":
                    self._mode = self.config.default_mode
                    self._stage = ""
                    self._audio_level = 0.0
            if state in {"idle", "failed"}:
                self._gesture.reset()
            self.publish()
            return
        if method == "processing_stage":
            stage = str(params.get("stage") or "")
            labels = _STAGE_LABELS.get(self.config.ui.language, _STAGE_LABELS["en"])
            with self._lock:
                self._stage = labels.get(stage, stage)
            self.publish()
            return
        if method == "audio_level":
            try:
                level = float(params.get("rms") or 0.0)
            except (TypeError, ValueError):
                level = 0.0
            with self._lock:
                self._audio_level = max(0.0, min(1.0, level))
            self.publish()
            return
        if method == "final_result":
            self._on_notice(
                self._text("识别完成", "Transcription complete"),
                self._text("听写任务已完成。", "The dictation task is complete."),
            )
            return
        if method == "error":
            self._on_notice(
                self._text("Vocal More 错误", "Vocal More error"),
                str(params.get("message") or "Unknown error"),
            )

    def _handle_trigger_pressed(self, event_time: float) -> None:
        if not self._api_key_ready():
            return
        state, mode = self._runtime_state()
        if mode == "realtime_long":
            action = self._gesture.on_pressed(event_time, state)
            if action in {HotkeyGestureAction.START, HotkeyGestureAction.STOP}:
                self._handler.dispatch("hotkey_pressed", {})
        else:
            self._handler.dispatch("hotkey_pressed", {})

    def _handle_trigger_released(self, event_time: float) -> None:
        state, mode = self._runtime_state()
        if mode == "realtime_long":
            action = self._gesture.on_released(event_time, state)
            if action is HotkeyGestureAction.STOP:
                self._handler.dispatch("hotkey_pressed", {})
        elif mode == "walkie_talkie":
            self._handler.dispatch("hotkey_released", {})

    def _handle_cancel(self) -> None:
        state, _mode = self._runtime_state()
        if state != "idle":
            self._gesture.reset()
            self._handler.dispatch("cancel", {})

    def _set_mode(self, mode: str) -> None:
        if mode not in {"walkie_talkie", "realtime_long", "meeting"}:
            raise ValueError(f"Unknown mode: {mode}")
        state, current = self._runtime_state()
        if state != "idle":
            raise RuntimeError("Finish the current dictation before changing mode")
        if current != mode:
            self._handler.dispatch("set_mode", {"mode": mode})
            with self._lock:
                self._mode = mode
            self._gesture.reset()
            self.publish()

    def _set_auto_paste(self, enabled: bool) -> None:
        self._handler.dispatch(
            "set_config",
            {"key": "auto_paste", "value": enabled},
        )
        self.publish()

    def _runtime_state(self) -> tuple[str, str]:
        with self._lock:
            return self._state, self._mode

    def _api_key_ready(self) -> bool:
        if self.config.api_key:
            return True
        self._on_notice(
            self._text("需要配置 API Key", "API key required"),
            self._text(
                "请在设置窗口中填写 DashScope API Key。",
                "Open Settings and enter a DashScope API key.",
            ),
        )
        self.show_settings()
        return False

    def _submit(self, callback, *args) -> Future | None:
        if self._closed:
            return None
        future = self._commands.submit(callback, *args)
        future.add_done_callback(self._report_failure)
        return future

    def _report_failure(self, future: Future) -> None:
        try:
            future.result()
        except Exception as exc:
            self._on_notice(
                self._text("Vocal More 错误", "Vocal More error"),
                str(exc),
            )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._handler.dispatch("shutdown", {})
        finally:
            self._handler.close()
            self._commands.shutdown(wait=False, cancel_futures=False)

    def _text(self, zh: str, en: str) -> str:
        return zh if self.config.ui.language == "zh" else en


__all__ = ["LinuxDesktopController"]
