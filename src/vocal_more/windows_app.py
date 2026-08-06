"""Windows notification-area application for Vocal More."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
import ctypes
import os
from pathlib import Path
import sys
import threading
import time

from . import __version__
from .config import get_config
from .core.windows_hotkey_manager import WindowsHotkeyManager
from .domain.hotkey_gestures import HotkeyGestureAction, HotkeyGestureController
from .infrastructure.timestamped_output import install_timestamped_stream
from .windows_rpc_handler import WindowsRPCHandler
from .windows_tray import TraySnapshot, WindowsTray


_ERROR_ALREADY_EXISTS = 183
_LOG_STREAM = None

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


class _SingleInstance:
    """Keep one tray host per interactive Windows session."""

    def __init__(self, name: str = "Local\\VocalMoreDesktop") -> None:
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = [
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.SetLastError.argtypes = [wintypes.DWORD]
        kernel32.SetLastError.restype = None
        kernel32.GetLastError.argtypes = []
        kernel32.GetLastError.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        kernel32.SetLastError(0)
        self._kernel32 = kernel32
        self._handle = kernel32.CreateMutexW(None, False, name)
        if not self._handle:
            raise ctypes.WinError(kernel32.GetLastError())
        self.already_running = kernel32.GetLastError() == _ERROR_ALREADY_EXISTS

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


class WindowsVocalMoreApp:
    """Bridge raw Windows input and tray actions to the shared RPC runtime."""

    def __init__(self) -> None:
        self.config = get_config()
        self._state_lock = threading.Lock()
        self._state = "idle"
        self._mode = self.config.default_mode
        self._processing_stage = ""
        self._closing = False
        self._shutdown_lock = threading.Lock()
        self._gesture = HotkeyGestureController()
        self._commands = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="vocal-more-windows-commands",
        )

        self._handler = WindowsRPCHandler(send_notification=self._on_notification)
        initialized = self._handler.dispatch("initialize", {})
        self._state = str(initialized.get("state") or "idle")
        self._mode = str(initialized.get("current_mode") or self.config.default_mode)

        self._hotkeys = WindowsHotkeyManager(
            on_fn_pressed=self._on_trigger_pressed,
            on_fn_released=self._on_trigger_released,
            on_escape_pressed=self._on_escape_pressed,
            config=self.config,
        )
        self._tray = WindowsTray(
            on_action=self._on_tray_action,
            get_snapshot=self._tray_snapshot,
        )

    def run(self) -> None:
        hotkey_ready = self._hotkeys.start()
        if not hotkey_ready:
            self._notify(
                self._text("热键不可用", "Global hotkey unavailable"),
                self._text(
                    "无法启动全局热键监听；仍可通过托盘菜单开始听写。",
                    "The global listener failed; dictation is still available from the tray menu.",
                ),
                level="warning",
            )

        if not self.config.api_key:
            self.config.save()
            self._notify(
                self._text("需要配置 API Key", "API key required"),
                self._text(
                    "请右键托盘图标打开配置文件，填写 DashScope API Key。",
                    "Right-click the tray icon, open the config file, and set the DashScope API key.",
                ),
                level="warning",
            )
        else:
            self._notify(
                "Vocal More",
                self._text(
                    f"Windows 版本已启动；按 {self._hotkeys.trigger_label} 开始。",
                    f"Windows host is running. Press {self._hotkeys.trigger_label} to start.",
                ),
            )

        try:
            self._tray.run()
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        with self._shutdown_lock:
            if self._closing:
                return
            self._closing = True

        self._hotkeys.stop()
        try:
            future = self._commands.submit(self._shutdown_runtime)
            future.result(timeout=3.0)
        except FutureTimeout:
            print("[WindowsApp] Runtime shutdown exceeded 3 seconds")
        except Exception as exc:
            print(f"[WindowsApp] Runtime shutdown failed: {exc}")
        finally:
            # Do not cancel a queued runtime shutdown if another serialized
            # command is still unwinding. ThreadPoolExecutor workers are joined
            # at interpreter exit, so the cleanup will still run.
            self._commands.shutdown(wait=False, cancel_futures=False)

    def _shutdown_runtime(self) -> None:
        try:
            self._handler.dispatch("shutdown", {})
        finally:
            self._handler.close()

    # -- Raw hotkey handling -------------------------------------------------

    def _on_trigger_pressed(self) -> None:
        event_time = time.monotonic()
        self._submit(self._handle_trigger_pressed, event_time)

    def _on_trigger_released(self) -> None:
        event_time = time.monotonic()
        self._submit(self._handle_trigger_released, event_time)

    def _on_escape_pressed(self) -> None:
        self._submit(self._handle_escape)

    def _handle_trigger_pressed(self, event_time: float) -> None:
        if not self._api_key_ready():
            return
        state, mode = self._runtime_state()
        if mode == "realtime_long":
            action = self._gesture.on_pressed(event_time, state)
            if action in (HotkeyGestureAction.START, HotkeyGestureAction.STOP):
                self._handler.dispatch("hotkey_pressed", {})
            return
        if mode == "walkie_talkie":
            self._handler.dispatch("hotkey_pressed", {})
            return
        # Meeting mode is toggle-based.
        self._handler.dispatch("hotkey_pressed", {})

    def _handle_trigger_released(self, event_time: float) -> None:
        state, mode = self._runtime_state()
        if mode == "realtime_long":
            action = self._gesture.on_released(event_time, state)
            if action is HotkeyGestureAction.STOP:
                # RealtimeLongMode toggles on key-down; synthesize the second
                # logical key-down after a hold gesture.
                self._handler.dispatch("hotkey_pressed", {})
            return
        if mode == "walkie_talkie":
            self._handler.dispatch("hotkey_released", {})

    def _handle_escape(self) -> None:
        state, _ = self._runtime_state()
        if state != "idle":
            self._gesture.reset()
            self._handler.dispatch("cancel", {})

    # -- Tray actions --------------------------------------------------------

    def _on_tray_action(self, action: str) -> None:
        if action == "quit":
            self.shutdown()
            return
        self._submit(self._handle_tray_action, action)

    def _handle_tray_action(self, action: str) -> None:
        if action == "toggle":
            self._handle_manual_toggle()
        elif action.startswith("mode:"):
            self._set_mode(action.split(":", 1)[1])
        elif action == "toggle_auto_paste":
            self._handler.dispatch(
                "set_config",
                {"key": "auto_paste", "value": not self.config.auto_paste},
            )
            self._tray.refresh()
        elif action == "open_config":
            self._open_config()
        elif action == "open_data":
            self._open_data_dir()

    def _handle_manual_toggle(self) -> None:
        if not self._api_key_ready():
            return
        self._gesture.reset()
        state, mode = self._runtime_state()
        if state in {"stopping", "processing", "cancelling"}:
            self._handler.dispatch("cancel", {})
            return
        if mode == "walkie_talkie" and state in {"starting", "recording"}:
            self._handler.dispatch("hotkey_released", {})
            return
        self._handler.dispatch("hotkey_pressed", {})

    def _set_mode(self, mode: str) -> None:
        state, current_mode = self._runtime_state()
        if mode == current_mode:
            return
        if state != "idle":
            self._notify(
                self._text("正在听写", "Dictation in progress"),
                self._text(
                    "请结束当前任务后再切换录音模式。",
                    "Finish the current task before changing recording mode.",
                ),
                level="warning",
            )
            return
        result = self._handler.dispatch("set_mode", {"mode": mode})
        with self._state_lock:
            self._mode = str(result.get("mode") or mode)
        self._gesture.reset()
        self._tray.refresh()

    def _open_config(self) -> None:
        path = self.config.get_config_path()
        if not path.exists():
            self.config.save()
        self._open_path(path)

    def _open_data_dir(self) -> None:
        path = self.config.get_config_dir()
        path.mkdir(parents=True, exist_ok=True)
        self._open_path(path)

    @staticmethod
    def _open_path(path: Path) -> None:
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except Exception as exc:
            raise RuntimeError(f"Cannot open {path}: {exc}") from exc

    # -- Runtime notifications ----------------------------------------------

    def _on_notification(self, method: str, params: dict) -> None:
        if self._closing:
            return
        if method == "state_changed":
            state = str(params.get("state") or "idle")
            with self._state_lock:
                self._state = state
                if state == "idle":
                    self._mode = self.config.default_mode
                    self._processing_stage = ""
            if state in {"idle", "failed"}:
                self._gesture.reset()
            self._tray.refresh()
            return
        if method == "processing_stage":
            stage = str(params.get("stage") or "")
            labels = _STAGE_LABELS.get(self.config.ui.language, _STAGE_LABELS["en"])
            with self._state_lock:
                self._processing_stage = labels.get(stage, stage)
            self._tray.refresh()
            return
        if method == "final_result":
            # Notification-area balloons can be retained by Windows. Avoid
            # placing dictated content in notification history.
            self._notify(
                self._text("识别完成", "Transcription complete"),
                self._text("听写任务已完成。", "The dictation task is complete."),
            )
            return
        if method == "error":
            self._notify(
                self._text("Vocal More 错误", "Vocal More error"),
                str(params.get("message") or "Unknown error"),
                level="error",
            )
            return
        if method == "retry_completed":
            self._notify(
                self._text("重新识别完成", "Retry complete"),
                self._text("重新识别任务已完成。", "The retry task is complete."),
            )
        elif method in {"retry_failed", "meeting_notes_failed"}:
            self._notify(
                self._text("任务失败", "Task failed"),
                str(params.get("error") or "Unknown error"),
                level="error",
            )

    # -- Helpers -------------------------------------------------------------

    def _submit(self, function, *args) -> Future | None:
        if self._closing:
            return None
        try:
            future = self._commands.submit(function, *args)
        except RuntimeError:
            return None
        future.add_done_callback(self._report_command_failure)
        return future

    def _report_command_failure(self, future: Future) -> None:
        try:
            future.result()
        except Exception as exc:
            print(f"[WindowsApp] Command failed: {exc}")
            self._notify(
                self._text("Vocal More 错误", "Vocal More error"),
                str(exc),
                level="error",
            )

    def _runtime_state(self) -> tuple[str, str]:
        with self._state_lock:
            return self._state, self._mode

    def _tray_snapshot(self) -> TraySnapshot:
        with self._state_lock:
            state = self._state
            mode = self._mode
            processing_stage = self._processing_stage
        return TraySnapshot(
            state=state,
            mode=mode,
            auto_paste=bool(self.config.auto_paste),
            trigger_label=self._hotkeys.trigger_label,
            api_configured=bool(self.config.api_key),
            language=self.config.ui.language,
            processing_stage=processing_stage,
        )

    def _api_key_ready(self) -> bool:
        if self.config.api_key:
            return True
        self._notify(
            self._text("需要配置 API Key", "API key required"),
            self._text(
                "请从托盘菜单打开配置文件并填写 api_key。",
                "Open the config file from the tray menu and set api_key.",
            ),
            level="warning",
        )
        return False

    def _notify(self, title: str, message: str, *, level: str = "info") -> None:
        tray = getattr(self, "_tray", None)
        if tray is not None:
            tray.notify(title, message, level=level)
        else:
            print(f"[{level}] {title}: {message}")

    def _text(self, zh: str, en: str) -> str:
        return zh if self.config.ui.language == "zh" else en


def _ensure_no_proxy(*hosts: str) -> None:
    for variable in ("no_proxy", "NO_PROXY"):
        entries = [
            item.strip()
            for item in os.environ.get(variable, "").split(",")
            if item.strip()
        ]
        for host in hosts:
            if host not in entries:
                entries.append(host)
        os.environ[variable] = ",".join(entries)


def _install_windows_output() -> None:
    """Give a windowed PyInstaller build a persistent target for print logs."""
    global _LOG_STREAM
    if sys.stdout is None or sys.stderr is None:
        data_dir = get_config().get_config_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        try:
            _LOG_STREAM = open(
                data_dir / "vocal-more.log",
                "a",
                encoding="utf-8",
                buffering=1,
            )
        except OSError:
            _LOG_STREAM = open(os.devnull, "w", encoding="utf-8")
        if sys.stdout is None:
            sys.stdout = _LOG_STREAM
        if sys.stderr is None:
            sys.stderr = _LOG_STREAM

    install_timestamped_stream("stdout")
    install_timestamped_stream("stderr")


def _show_fatal_error(message: str) -> None:
    try:
        from ctypes import wintypes

        message_box = ctypes.windll.user32.MessageBoxW
        message_box.argtypes = [
            wintypes.HWND,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.UINT,
        ]
        message_box.restype = ctypes.c_int
        message_box(None, str(message), "Vocal More", 0x10)
    except Exception:
        print(f"[Fatal] {message}")


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("The Windows host can only run on Windows.")

    _install_windows_output()
    _ensure_no_proxy("dashscope.aliyuncs.com")
    print(f"[Startup] Vocal More {__version__} Windows host")

    instance = _SingleInstance()
    if instance.already_running:
        _show_fatal_error("Vocal More is already running in the notification area.")
        instance.close()
        return

    try:
        WindowsVocalMoreApp().run()
    except Exception as exc:
        print(f"[Fatal] Windows host failed: {exc}")
        _show_fatal_error(str(exc))
        raise
    finally:
        instance.close()


if __name__ == "__main__":
    main()
