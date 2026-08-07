"""Windows desktop application for Vocal More."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
import ctypes
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any, Mapping

from . import __version__
from .config import ASR_MODEL_CATALOG, LLM_MODEL_CATALOG, get_config
from .core.windows_hotkey_manager import WindowsHotkeyManager
from .windows_trigger import (
    WINDOWS_TRIGGER_OPTIONS,
    current_trigger_browser_code,
    custom_key_config_for_browser_code,
)
from .domain.hotkey_gestures import HotkeyGestureAction, HotkeyGestureController
from .infrastructure.timestamped_output import install_timestamped_stream
from .windows_desktop_ui import CapsuleSnapshot, SettingsSnapshot, WindowsDesktopUI
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
    """Keep one desktop host per interactive Windows session."""

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
    """Bridge Windows hotkeys, tray UI, capsule, and settings to shared runtime."""

    def __init__(self) -> None:
        self.config = get_config()
        self._state_lock = threading.Lock()
        self._state = "idle"
        self._mode = self.config.default_mode
        self._processing_stage = ""
        self._audio_level = 0.0
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
        self._desktop_ui = WindowsDesktopUI(
            on_capsule_cancel=self._on_capsule_cancel,
            on_save_settings=self._on_save_settings,
            on_refresh_devices=self._on_refresh_devices,
            on_open_path=self._on_settings_open_path,
        )
        self._desktop_ui_ready = False

    def run(self) -> None:
        self._desktop_ui_ready = self._desktop_ui.start()
        if not self._desktop_ui_ready:
            error = self._desktop_ui.startup_error
            print(f"[WindowsApp] Desktop UI unavailable: {error or 'startup timeout'}")
            self._notify(
                self._text("界面组件不可用", "Desktop UI unavailable"),
                self._text(
                    "悬浮胶囊和设置窗口未能启动；托盘听写仍可使用。",
                    "The capsule and settings window could not start; tray dictation remains available.",
                ),
                level="warning",
            )

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
                    "设置窗口已打开，请填写 DashScope API Key。",
                    "The settings window is open. Enter a DashScope API key.",
                ),
                level="warning",
            )
            self._submit(self._show_settings)
        else:
            self._notify(
                "Vocal More",
                self._text(
                    f"Windows 版本已启动；按 {self._hotkeys.trigger_label} 开始。",
                    f"Windows host is running. Press {self._hotkeys.trigger_label} to start.",
                ),
            )

        self._publish_capsule()
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
        self._desktop_ui.stop()
        try:
            future = self._commands.submit(self._shutdown_runtime)
            future.result(timeout=3.0)
        except FutureTimeout:
            print("[WindowsApp] Runtime shutdown exceeded 3 seconds")
        except Exception as exc:
            print(f"[WindowsApp] Runtime shutdown failed: {exc}")
        finally:
            # Do not cancel a queued runtime shutdown if another serialized
            # command is still unwinding. The worker is joined at interpreter exit.
            self._commands.shutdown(wait=False, cancel_futures=False)

    def _shutdown_runtime(self) -> None:
        try:
            self._handler.dispatch("shutdown", {})
        finally:
            self._handler.close()

    # -- Raw hotkey and capsule handling ------------------------------------

    def _on_trigger_pressed(self) -> None:
        self._submit(self._handle_trigger_pressed, time.monotonic())

    def _on_trigger_released(self) -> None:
        self._submit(self._handle_trigger_released, time.monotonic())

    def _on_escape_pressed(self) -> None:
        self._submit(self._handle_escape)

    def _on_capsule_cancel(self) -> None:
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

    # -- Tray and settings actions ------------------------------------------

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
        elif action == "settings":
            self._show_settings()
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
        self._publish_capsule()

    def _show_settings(self) -> dict[str, Any]:
        if not self._desktop_ui_ready:
            self._open_config()
            return {"ok": False, "message": "Settings UI is unavailable; opened config.yaml"}
        try:
            devices = self._handler.dispatch("list_devices", {})
        except Exception as exc:
            print(f"[WindowsApp] Could not enumerate microphones for settings: {exc}")
            devices = []
        hotkey = self.config.hotkey
        trigger = current_trigger_browser_code(
            hotkey.active_hotkeys,
            hotkey.custom_keys,
        )
        data_dir = self.config.get_config_dir()
        snapshot = SettingsSnapshot(
            version=__version__,
            config=self.config.to_dict(),
            asr_models=tuple(ASR_MODEL_CATALOG),
            llm_models=tuple(LLM_MODEL_CATALOG),
            devices=tuple(devices),
            trigger_browser_code=trigger,
            trigger_options=WINDOWS_TRIGGER_OPTIONS,
            data_dir=str(data_dir),
            config_path=str(self.config.get_config_path()),
            log_path=str(data_dir / "vocal-more.log"),
        )
        self._desktop_ui.show_settings(snapshot)
        return {"ok": True}

    def _on_save_settings(self, payload: dict[str, Any]) -> Future | None:
        return self._submit(self._apply_settings, payload)

    def _on_refresh_devices(self) -> Future | None:
        return self._submit(self._refresh_devices)

    def _on_settings_open_path(self, target: str) -> Future | None:
        return self._submit(self._open_named_path, target)

    def _refresh_devices(self) -> list:
        # The RPC method intentionally performs ordinary enumeration. Resetting
        # PortAudio while an idle warm ASR session exists is unnecessary here.
        return self._handler.dispatch("list_devices", {})

    def _apply_settings(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        state, _ = self._runtime_state()
        if state != "idle":
            return {
                "ok": False,
                "message": self._text(
                    "请先结束当前听写任务，再保存设置。",
                    "Finish the current dictation task before saving settings.",
                ),
            }
        updates = payload.get("updates")
        if not isinstance(updates, Mapping):
            return {"ok": False, "message": "Invalid settings payload"}
        updates = dict(updates)
        trigger_code = str(payload.get("trigger_browser_code") or "F8")

        input_device = updates.pop("audio.input_device", None)
        default_mode = str(updates.pop("default_mode", self.config.default_mode))
        before = self.config.to_dict()

        # Apply the API key first so subsequent model refreshes use the new
        # credential, then save only values whose normalized snapshot changed.
        ordered_keys = ["api_key"] + [key for key in updates if key != "api_key"]
        for key in ordered_keys:
            value = updates[key]
            if self._snapshot_value(before, key) == value:
                continue
            self._handler.dispatch("set_config", {"key": key, "value": value})
            before = self.config.to_dict()

        if self.config.audio.input_device != input_device:
            self._handler.dispatch("set_device", {"device": input_device})
        if default_mode != self.config.default_mode:
            result = self._handler.dispatch("set_mode", {"mode": default_mode})
            with self._state_lock:
                self._mode = str(result.get("mode") or default_mode)

        custom_key = custom_key_config_for_browser_code(trigger_code)
        if trigger_code == "F8":
            active_hotkeys = ["fn"]
            custom_keys: list[dict] = []
        elif custom_key is not None:
            active_hotkeys = []
            custom_keys = [custom_key]
        else:
            return {"ok": False, "message": f"Unsupported Windows trigger: {trigger_code}"}

        if self.config.hotkey.active_hotkeys != active_hotkeys:
            self._handler.dispatch(
                "set_config",
                {"key": "hotkey.active_hotkeys", "value": active_hotkeys},
            )
        if self.config.hotkey.custom_keys != custom_keys:
            self._handler.dispatch(
                "set_config",
                {"key": "hotkey.custom_keys", "value": custom_keys},
            )
        self._hotkeys.set_active_hotkeys(active_hotkeys)
        self._hotkeys.set_custom_keys(custom_keys)

        with self._state_lock:
            self._mode = self.config.default_mode
        self._gesture.reset()
        self._tray.refresh()
        self._publish_capsule()
        return {
            "ok": True,
            "message": self._text("设置已保存并应用", "Settings saved and applied"),
        }

    @staticmethod
    def _snapshot_value(snapshot: Mapping[str, Any], key: str) -> Any:
        value: Any = snapshot
        for part in key.split("."):
            if not isinstance(value, Mapping):
                return None
            value = value.get(part)
        return value

    def _open_named_path(self, target: str) -> None:
        if target == "data":
            self._open_data_dir()
        elif target == "config":
            self._open_config()
        elif target == "log":
            log_path = self.config.get_config_dir() / "vocal-more.log"
            if not log_path.exists():
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.touch()
            self._open_path(log_path)
        else:
            raise ValueError(f"Unknown settings path target: {target}")

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
                    self._audio_level = 0.0
            if state in {"idle", "failed"}:
                self._gesture.reset()
            self._tray.refresh()
            self._publish_capsule()
            return
        if method == "processing_stage":
            stage = str(params.get("stage") or "")
            labels = _STAGE_LABELS.get(self.config.ui.language, _STAGE_LABELS["en"])
            with self._state_lock:
                self._processing_stage = labels.get(stage, stage)
            self._tray.refresh()
            self._publish_capsule()
            return
        if method == "audio_level":
            try:
                level = float(params.get("rms") or 0.0)
            except (TypeError, ValueError):
                level = 0.0
            with self._state_lock:
                self._audio_level = max(0.0, min(1.0, level))
            self._publish_capsule()
            return
        if method == "final_result":
            # Notifications and capsule intentionally omit dictated content.
            if self._desktop_ui_ready:
                self._desktop_ui.flash_success(language=self.config.ui.language)
            self._notify(
                self._text("识别完成", "Transcription complete"),
                self._text("听写任务已完成。", "The dictation task is complete."),
            )
            return
        if method == "error":
            message = str(params.get("message") or "Unknown error")
            if self._desktop_ui_ready:
                self._desktop_ui.flash_error(message, language=self.config.ui.language)
            self._notify(
                self._text("Vocal More 错误", "Vocal More error"),
                message,
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

    def _capsule_snapshot(self) -> CapsuleSnapshot:
        with self._state_lock:
            state = self._state
            mode = self._mode
            processing_stage = self._processing_stage
            audio_level = self._audio_level
        return CapsuleSnapshot(
            state=state,
            mode=mode,
            language=self.config.ui.language,
            stage=processing_stage,
            audio_level=audio_level,
            trigger_label=self._hotkeys.trigger_label,
            can_cancel=state not in {"idle", "failed"},
        )

    def _publish_capsule(self) -> None:
        if self._desktop_ui_ready:
            self._desktop_ui.update_capsule(self._capsule_snapshot())

    def _api_key_ready(self) -> bool:
        if self.config.api_key:
            return True
        self._notify(
            self._text("需要配置 API Key", "API key required"),
            self._text(
                "请在设置窗口中填写 api_key。",
                "Open Settings and enter api_key.",
            ),
            level="warning",
        )
        self._show_settings()
        return False

    def _notify(self, title: str, message: str, *, level: str = "info") -> None:
        tray = getattr(self, "_tray", None)
        if tray is not None:
            tray.notify(title, message, level=level)
        else:
            print(f"[{level}] {title}: {message}")

    def _text(self, zh: str, en: str) -> str:
        return zh if self.config.ui.language == "zh" else en


def _enable_windows_dpi_awareness() -> None:
    """Prefer per-monitor DPI awareness, with a legacy fallback."""
    if sys.platform != "win32":
        return
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 is the signed handle -4.
        set_context = ctypes.windll.user32.SetProcessDpiAwarenessContext
        set_context.argtypes = [ctypes.c_void_p]
        set_context.restype = ctypes.c_bool
        if set_context(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


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

    _enable_windows_dpi_awareness()
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
