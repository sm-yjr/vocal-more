"""Main Menu Bar application for Vocal-More (standalone Python mode)."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from typing import Any, Optional

_MODULE_IMPORT_STARTED_AT = time.perf_counter()

import objc
import rumps
from rumps import events as rumps_events
from Foundation import NSObject, NSRunLoop, NSRunLoopCommonModes, NSTimer

from . import __version__
from .application.background_executor import BackgroundExecutor
from .application.lazy_resource import initialized_resource
from .config import (
    ASR_MODEL_CATALOG,
    LLM_MODEL_CATALOG,
    get_config,
)
from .dictionary import get_dictionary, reload_dictionary
from .domain.hotkey_gestures import HotkeyGestureAction, HotkeyGestureController
from .domain.input_intent import InputIntent
from .domain.waveform_calibration import waveform_level_from_rms
from .localization import t
from .modes.base_mode import ModeState
from .paths import bundled_resource_path
from .infrastructure.timestamped_output import install_timestamped_stream


def AudioRecorder(*args, **kwargs):
    from .core.audio_recorder import AudioRecorder as Implementation

    return Implementation(*args, **kwargs)


def _list_input_devices(*, refresh: bool = True):
    from .core.audio_recorder import AudioRecorder as Implementation

    return Implementation.list_input_devices(refresh=refresh)


AudioRecorder.list_input_devices = _list_input_devices


def HotkeyManager(*args, **kwargs):
    from .core.hotkey_manager import HotkeyManager as Implementation

    return Implementation(*args, **kwargs)


def RecordingStore(*args, **kwargs):
    from .core.recording_store import RecordingStore as Implementation

    return Implementation(*args, **kwargs)


def TextPolisher(*args, **kwargs):
    from .core.text_polisher import TextPolisher as Implementation

    return Implementation(*args, **kwargs)


def build_polish_prompt_presets():
    from .core.text_polisher import build_polish_prompt_presets as implementation

    return implementation()


def FloatingCapsule(*args, **kwargs):
    from .ui.floating_capsule import FloatingCapsule as Implementation

    return Implementation(*args, **kwargs)


def WalkieTalkieMode(*args, **kwargs):
    from .modes.walkie_talkie import WalkieTalkieMode as Implementation

    return Implementation(*args, **kwargs)


def RealtimeLongMode(*args, **kwargs):
    from .modes.realtime_long import RealtimeLongMode as Implementation

    return Implementation(*args, **kwargs)


def MeetingMode(*args, **kwargs):
    from .modes.meeting import MeetingMode as Implementation

    return Implementation(*args, **kwargs)


def run_environment_checks(*args, **kwargs):
    from .environment_check import run_environment_checks as implementation

    return implementation(*args, **kwargs)


def is_accessibility_trusted():
    from .environment_check import is_accessibility_trusted as implementation

    return implementation()


def export_support_bundle(*args, **kwargs):
    from .diagnostics import export_support_bundle as implementation

    return implementation(*args, **kwargs)


def ensure_runtime_debug_dir_env():
    from .diagnostics import ensure_runtime_debug_dir_env as implementation

    return implementation()


def live_trace_recorder_from_env():
    from .benchmarking import live_trace_recorder_from_env as implementation

    return implementation()


def SettingsWindow(*args, **kwargs):
    """Import and create the heavyweight settings WebView on first use."""
    from .ui.settings_window import SettingsWindow as SettingsWindowImplementation

    return SettingsWindowImplementation(*args, **kwargs)

MENU_STATE_OFF = 0
MENU_STATE_ON = 1

MODE_MENU_OPTIONS = [
    ("walkie_talkie", "mode_walkie_talkie"),
    ("realtime_long", "mode_realtime_long"),
    ("meeting", "mode_meeting"),
]

POLISH_LEVEL_OPTIONS = [
    ("minimal", "polish_level_minimal"),
    ("balanced", "polish_level_balanced"),
    ("strong", "polish_level_strong"),
]

HOTKEY_PERMISSION_RETRY_INTERVAL_SECONDS = 2.0


class _StartingMode:
    state = ModeState.IDLE


class StatusMenuRefreshDelegate(NSObject):
    """Refresh volatile status-bar menu content before the menu opens."""

    def initWithApp_(self, app):
        self = objc.super(StatusMenuRefreshDelegate, self).init()
        if self is None:
            return None
        self._app = app
        return self

    def menuWillOpen_(self, _menu) -> None:
        self._app._on_status_menu_will_open()


class VocalMoreApp(rumps.App):
    """Menu bar application for Vocal-More."""

    def __init__(self, dependencies=None):
        """Initialize the application."""
        super().__init__(
            "Vocal-More",
            icon=self._get_icon_path("icon_idle.png"),
            template=True,
            quit_button=None,
        )

        self._dependencies_lock = threading.Lock()
        self._dependencies_ready = dependencies is not None
        self._is_quitting = False
        self._pending_settings_request: Optional[dict[str, str]] = None
        if dependencies is None:
            self._apply_starting_dependencies()
        else:
            self._apply_dependencies(dependencies)
        self._sparkle_updater = None
        self._startup_executor = BackgroundExecutor(
            max_workers=1,
            thread_name_prefix="vocal-more-startup",
        )
        self._startup_task = None
        self._apply_interface_language(update_frontend=False)

        # Build menu
        self._build_menu()

    def _apply_starting_dependencies(self) -> None:
        """Install the minimal state needed to render the status item."""
        starting_mode = _StartingMode()
        self.config = get_config()
        self._hotkey_listener_ready = None
        self._environment_checks = []
        self._text_polisher = None
        self._capsule = None
        self._recording_store = None
        self._recording_retry = None
        self._walkie_talkie = starting_mode
        self._realtime_long = starting_mode
        self._meeting = None
        self._current_mode = starting_mode
        self._command_coordinator = None
        self._hotkey_manager = None
        self._hotkey_gesture_controller = HotkeyGestureController()
        self._benchmark_trace = None
        self._runtime = None
        self._settings_window = None
        self._dictionary_learning = None
        self._context_personalization = None
        self._main_thread_timers: set[NSTimer] = set()
        self._hotkey_permission_retry_timer = None
        self._status_menu_delegate = None

    def _ensure_dependencies(self) -> bool:
        """Build runtime services once, after the status item is visible."""
        if getattr(self, "_dependencies_ready", True):
            return True
        with self._dependencies_lock:
            if self._dependencies_ready:
                return True
            if self._is_quitting:
                return False

            from .bootstrap import build_menu_app_dependencies

            dependencies = build_menu_app_dependencies(
                self,
                config=self.config,
                text_polisher_factory=TextPolisher,
                capsule_factory=FloatingCapsule,
                recording_store_factory=RecordingStore,
                walkie_talkie_factory=WalkieTalkieMode,
                realtime_long_factory=RealtimeLongMode,
                meeting_factory=MeetingMode,
                hotkey_manager_factory=HotkeyManager,
                settings_window_factory=SettingsWindow,
            )
            if self._is_quitting:
                self._close_unapplied_dependencies(dependencies)
                return False
            self._apply_dependencies(dependencies)
            self._dependencies_ready = True
            return True

    @staticmethod
    def _close_unapplied_dependencies(dependencies) -> None:
        """Close services built while the user was already quitting."""
        hotkey_manager = getattr(dependencies, "hotkey_manager", None)
        stop = getattr(hotkey_manager, "stop", None)
        if callable(stop):
            stop()
        retry_runtime = getattr(dependencies, "recording_retry", None)
        close_retry = getattr(retry_runtime, "close", None)
        retry_drained = True
        if callable(close_retry):
            shutdown = close_retry(timeout=0.5)
            retry_drained = getattr(shutdown, "drained", True)
        for name in ("walkie_talkie", "realtime_long", "meeting"):
            close = getattr(getattr(dependencies, name, None), "close", None)
            if callable(close):
                close()
        recording_store = getattr(dependencies, "recording_store", None)
        close_recordings = getattr(recording_store, "close", None)
        if retry_drained and callable(close_recordings):
            close_recordings()
        elif not retry_drained:
            print("[App] Recording retry did not drain; leaving its store open")
        for name in ("dictionary_learning", "context_personalization"):
            close = getattr(getattr(dependencies, name, None), "close", None)
            if callable(close):
                close()
        coordinator = getattr(dependencies, "command_coordinator", None)
        close = getattr(coordinator, "close", None)
        if callable(close):
            close()

    def _apply_dependencies(self, dependencies) -> None:
        self.config = dependencies.config
        self._hotkey_listener_ready = dependencies.hotkey_listener_ready
        self._environment_checks = dependencies.environment_checks
        self._text_polisher = dependencies.text_polisher
        self._capsule = dependencies.capsule
        self._recording_store = dependencies.recording_store
        self._recording_retry = getattr(dependencies, "recording_retry", None)
        self._walkie_talkie = dependencies.walkie_talkie
        self._realtime_long = dependencies.realtime_long
        self._meeting = dependencies.meeting
        self._current_mode = dependencies.current_mode
        self._command_coordinator = dependencies.command_coordinator
        self._hotkey_manager = dependencies.hotkey_manager
        self._hotkey_gesture_controller = HotkeyGestureController()
        self._benchmark_trace = live_trace_recorder_from_env()
        self._runtime = dependencies.runtime
        self._settings_window = dependencies.settings_window
        self._dictionary_learning = getattr(
            dependencies,
            "dictionary_learning",
            None,
        )
        self._context_personalization = getattr(
            dependencies,
            "context_personalization",
            None,
        )
        self._main_thread_timers: set[NSTimer] = set()
        self._hotkey_permission_retry_timer: Optional[NSTimer] = None
        self._status_menu_delegate = None

    # ── Resource paths ────────────────────────────────────────

    def _get_icon_path(self, icon_name: str) -> Optional[str]:
        """Get icon path."""
        icon_path = bundled_resource_path("resources", "icons", icon_name)
        if icon_path.exists():
            return str(icon_path)
        return None

    def _get_logo_path(self) -> Optional[str]:
        """Get logo path for notifications."""
        runtime_logo = bundled_resource_path("assets", ".VocalMore.runtime-logo.png")
        if runtime_logo.exists():
            return str(runtime_logo)
        source_logo = bundled_resource_path("assets", "logo.png")
        if source_logo.exists():
            return str(source_logo)
        return None

    # ── Menu ──────────────────────────────────────────────────

    def _t(self, key: str, **kwargs) -> str:
        return t(self.config.ui.language, key, **kwargs)

    def _apply_interface_language(self, update_frontend: bool = True) -> None:
        """Push the current UI language into all visible app surfaces."""
        capsule = getattr(self, "_capsule", None)
        if capsule is not None:
            capsule.set_interface_language(self.config.ui.language)

        settings_window = getattr(self, "_settings_window", None)
        if settings_window is not None:
            settings_window.set_interface_language(
                self.config.ui.language,
                update_frontend=update_frontend,
            )

        if hasattr(self, "_quick_mode_item"):
            self._refresh_menu_localization()

    def _refresh_menu_localization(self) -> None:
        """Refresh localized menu strings without rebuilding menu objects."""
        self._state_item.title = self._state_title_for_state(self._current_mode.state)
        self._environment_item.title = self._t("menu_environment")
        self._check_for_updates_item.title = self._t("menu_check_for_updates")
        self._export_diagnostics_item.title = self._t("menu_export_diagnostics")
        self._quick_mode_item.title = self._t("menu_recording_mode")
        self._quick_microphone_item.title = self._t("menu_microphone")
        self._quick_asr_model_item.title = self._t("menu_asr_model")
        self._quick_enable_polish_item.title = self._t("menu_enable_polishing")
        self._quick_polish_level_item.title = self._t("menu_polish_strength")
        self._settings_menu_item.title = self._t("menu_more_settings")
        self._quit_menu_item.title = self._t("menu_quit")

        for mode_name, item in self._mode_menu_items.items():
            item.title = self._mode_display_name(mode_name)
        for level, item in self._polish_level_menu_items.items():
            item.title = self._polish_level_display_name(level)

        self._refresh_quick_settings_menu()
        self._refresh_environment_menu()

    def _state_title_for_state(self, state: ModeState) -> str:
        return {
            ModeState.IDLE: self._t("menu_status_idle"),
            ModeState.STARTING: self._t("menu_status_starting"),
            ModeState.RECORDING: self._t("menu_status_recording"),
            ModeState.STOPPING: self._t("menu_status_stopping"),
            ModeState.PROCESSING: self._t("menu_status_processing"),
            ModeState.CANCELLING: self._t("menu_status_cancelling"),
            ModeState.FAILED: self._t("menu_status_failed"),
        }.get(state, self._t("menu_status_unknown"))

    def _build_menu(self) -> None:
        """Build a simplified menu bar menu."""
        self._status_item = rumps.MenuItem(f"Vocal-More {__version__}")
        self._status_item.set_callback(None)

        self._state_item = rumps.MenuItem(self._state_title_for_state(ModeState.IDLE))
        self._state_item.set_callback(None)

        self._environment_item = self._build_environment_item()
        quick_items = self._build_quick_settings_items()
        self._export_diagnostics_item = rumps.MenuItem(
            self._t("menu_export_diagnostics"),
            callback=self._export_diagnostics,
        )
        self._check_for_updates_item = rumps.MenuItem(
            self._t("menu_check_for_updates"),
            callback=self._check_for_updates,
        )
        self._settings_menu_item = rumps.MenuItem(
            self._t("menu_more_settings"),
            callback=self._open_settings,
        )
        self._quit_menu_item = rumps.MenuItem(
            self._t("menu_quit"),
            callback=self._quit_app,
        )

        self.menu = [
            self._status_item,
            self._state_item,
            self._environment_item,
            None,
            *quick_items,
            None,
            self._check_for_updates_item,
            self._export_diagnostics_item,
            self._settings_menu_item,
            None,
            self._quit_menu_item,
        ]
        self._install_status_menu_delegate()
        self._refresh_quick_settings_menu()
        self._refresh_environment_menu()

    def _install_status_menu_delegate(self) -> None:
        """Attach an AppKit menu delegate so hardware-dependent items stay fresh."""
        menu = getattr(getattr(self, "_menu", None), "_menu", None)
        set_delegate = getattr(menu, "setDelegate_", None)
        if not callable(set_delegate):
            return

        alloc = getattr(StatusMenuRefreshDelegate, "alloc", None)
        if callable(alloc):
            delegate = StatusMenuRefreshDelegate.alloc().initWithApp_(self)
        else:
            delegate = StatusMenuRefreshDelegate()
            delegate._app = self
        self._status_menu_delegate = delegate
        set_delegate(delegate)

    def _on_status_menu_will_open(self) -> None:
        """Refresh status-bar-only dynamic menus before users inspect them."""
        if getattr(self, "_dependencies_ready", True) is False:
            return
        self._refresh_microphone_status_menu()

    def _build_quick_settings_items(self) -> list[rumps.MenuItem]:
        """Build the primary menu items for common settings."""
        self._quick_mode_item = rumps.MenuItem(self._t("menu_recording_mode"))
        self._mode_menu_items: dict[str, rumps.MenuItem] = {}
        for mode_name, _ in MODE_MENU_OPTIONS:
            item = rumps.MenuItem(
                self._mode_display_name(mode_name),
                callback=lambda _, value=mode_name: self._on_quick_set_mode(value),
            )
            self._mode_menu_items[mode_name] = item
            self._quick_mode_item.add(item)

        self._quick_asr_model_item = rumps.MenuItem(self._t("menu_asr_model"))
        self._asr_model_menu_items: dict[str, rumps.MenuItem] = {}
        for entry in ASR_MODEL_CATALOG:
            if entry.get("separator"):
                continue
            item = rumps.MenuItem(
                entry["display_name"],
                callback=lambda _, model_id=entry["id"]: self._on_quick_set_asr_model(
                    model_id
                ),
            )
            self._asr_model_menu_items[entry["id"]] = item
            self._quick_asr_model_item.add(item)

        self._quick_enable_polish_item = rumps.MenuItem(
            self._t("menu_enable_polishing"),
            callback=self._on_quick_toggle_polish,
        )

        self._quick_microphone_item = self._build_microphone_item()

        self._quick_polish_level_item = rumps.MenuItem(self._t("menu_polish_strength"))
        self._polish_level_menu_items: dict[str, rumps.MenuItem] = {}
        for level, _ in POLISH_LEVEL_OPTIONS:
            item = rumps.MenuItem(
                self._polish_level_display_name(level),
                callback=lambda _, value=level: self._on_quick_set_polish_level(value),
            )
            self._polish_level_menu_items[level] = item
            self._quick_polish_level_item.add(item)
        return [
            self._quick_mode_item,
            self._quick_microphone_item,
            self._quick_asr_model_item,
            self._quick_enable_polish_item,
            self._quick_polish_level_item,
        ]

    def _build_microphone_item(self) -> rumps.MenuItem:
        """Build status-bar microphone settings."""
        item = rumps.MenuItem(self._t("menu_microphone"))
        self._microphone_default_item = rumps.MenuItem(
            self._t("menu_microphone_system_default"),
            callback=lambda _: self._on_quick_set_microphone_device(None),
        )
        self._microphone_device_menu_items: dict[str, rumps.MenuItem] = {}
        self._microphone_settings_item = rumps.MenuItem(
            self._t("menu_microphone_settings"),
            callback=self._open_microphone_settings,
        )
        self._populate_microphone_device_menu(item, [])
        return item

    def _build_environment_item(self) -> rumps.MenuItem:
        """Build the environment check submenu."""
        environment = rumps.MenuItem(self._t("menu_environment"))
        self._environment_check_items: dict[str, rumps.MenuItem] = {}
        for key in ("api_key", "accessibility", "input_device", "hotkey_listener"):
            item = rumps.MenuItem("")
            item.set_callback(None)
            self._environment_check_items[key] = item
            environment.add(item)
        self._environment_refresh_item = rumps.MenuItem(
            self._t("menu_run_environment_check"),
            callback=self._rerun_environment_check,
        )
        environment.add(self._environment_refresh_item)
        return environment

    def _open_settings(self, _=None) -> None:
        """Open the settings window."""
        self._show_settings()

    def _check_for_updates(self, sender=None) -> None:
        """Ask Sparkle to check the signed update feed for a newer release."""
        updater = self._get_sparkle_updater()
        if updater is not None and updater.check_for_updates(sender):
            return

        error = getattr(updater, "startup_error", None)
        if error is not None:
            print(f"[Updater] Sparkle is unavailable: {error}")
        subprocess.run(
            ["open", "https://github.com/sm-yjr/vocal-more/releases/latest"],
            check=False,
        )

    def _get_sparkle_updater(self):
        """Load Sparkle only after the status item is visible or on explicit use."""
        updater = getattr(self, "_sparkle_updater", None)
        if updater is not None:
            return updater

        from .infrastructure.sparkle_updater import SparkleUpdater

        updater = SparkleUpdater()
        self._sparkle_updater = updater
        return updater

    def _open_microphone_settings(self, _=None) -> None:
        """Open settings directly to microphone controls."""
        self._show_settings(initial_tab="audio")

    def _show_settings(
        self,
        *,
        initial_tab: str = "",
        focus_recording_id: str = "",
    ) -> None:
        """Open settings, optionally navigating to a specific record."""
        if getattr(self, "_dependencies_ready", True) is False:
            self._pending_settings_request = {
                "initial_tab": initial_tab,
                "focus_recording_id": focus_recording_id,
            }
            return
        self._refresh_environment_status()
        devices = self._list_devices()
        self._clear_missing_configured_microphone(devices)
        self._populate_microphone_device_menu(self._quick_microphone_item, devices)
        dictionary = self._get_dict_entries()
        current_mode = getattr(self, "_current_mode", None)
        audio_input_status = getattr(current_mode, "audio_input_status", None)
        if (
            isinstance(audio_input_status, dict)
            and audio_input_status.get("phase") == "planned"
            and audio_input_status.get("processing_mode") == "pending"
            and audio_input_status.get("last_session") is None
        ):
            # Recorder construction is deliberately I/O-free. Let the settings
            # window perform its explicit live capability inspection instead of
            # displaying that constructor-only placeholder as observed fact.
            audio_input_status = None
        self._settings_window.show(
            config=self.config.to_dict(),
            asr_models=ASR_MODEL_CATALOG,
            llm_models=LLM_MODEL_CATALOG,
            devices=devices,
            dictionary=dictionary,
            polish_prompt_presets=build_polish_prompt_presets(),
            version=__version__,
            initial_tab=initial_tab,
            focus_recording_id=focus_recording_id,
            dictionary_learning_records=(
                self._dictionary_learning.list_recent()
                if self._dictionary_learning is not None
                else []
            ),
            environment_checks=[
                check.to_dict()
                for check in getattr(self, "_environment_checks", [])
            ],
            audio_input_status=(
                audio_input_status
                if isinstance(audio_input_status, dict)
                else None
            ),
        )

    def _quit_app(self, _) -> None:
        """Quit the application."""
        self._is_quitting = True
        self._stop_hotkey_permission_retry_timer()
        startup_executor = getattr(self, "_startup_executor", None)
        if startup_executor is not None:
            startup_executor.close(wait=False, cancel_futures=True)
        if getattr(self, "_dependencies_ready", True) is False:
            rumps.quit_application()
            return
        self._hotkey_manager.stop()
        try:
            self._get_command_coordinator().call(
                self._handle_quit_cancel_command,
                command_name="quit_cancel",
                timeout=1.0,
            )
        except TimeoutError:
            print(
                "[App] Dictation cancellation timed out during quit; "
                "continuing bounded shutdown"
            )
        self._capsule.hide()
        recording_retry = getattr(self, "_recording_retry", None)
        close_retry = getattr(recording_retry, "close", None)
        retry_drained = True
        if callable(close_retry):
            shutdown = close_retry(timeout=0.5)
            retry_drained = getattr(shutdown, "drained", True)
        dictionary_learning = getattr(self, "_dictionary_learning", None)
        if dictionary_learning is not None:
            set_on_change = getattr(dictionary_learning, "set_on_change", None)
            if callable(set_on_change):
                set_on_change(None)
            close_learning = getattr(dictionary_learning, "close", None)
            if callable(close_learning):
                close_learning()
        self._settings_window.close()
        for mode in self._all_modes():
            close = getattr(mode, "close", None)
            if callable(close):
                close()
        close_recordings = getattr(self._recording_store, "close", None)
        if retry_drained and callable(close_recordings):
            close_recordings()
        elif not retry_drained:
            print("[App] Recording retry did not drain; leaving its store open")
        self._close_command_coordinator()
        rumps.quit_application()

    # ── Settings window callbacks ─────────────────────────────

    def _on_settings_config_change(self, key: str, value: Any) -> None:
        """Handle config change from settings window."""
        try:
            self._get_runtime().apply_update(key, value)
        except ValueError as exc:
            print(f"[Settings] {exc}")
            return

        self.config.save()
        if key == "audio.gain_mode":
            self._refresh_microphone_status_menu(update_settings_window=True)
        else:
            self._refresh_quick_settings_menu()
        print(f"[Settings] Config updated: {key} = {value}")

    def _on_settings_preview_config(self, key: str, value: Any) -> None:
        """Apply an audio slider preview without writing config to disk."""
        try:
            self._get_runtime().apply_update(key, value)
        except ValueError as exc:
            print(f"[Settings] {exc}")

    def _on_settings_set_device(self, device: Optional[str]) -> None:
        """Handle device change from settings window."""
        self._get_runtime().apply_update("audio.input_device", device)
        self.config.save()
        self._refresh_microphone_status_menu(update_settings_window=True)
        print(f"[Settings] Device set to: {device or 'System Default'}")

    def _on_settings_set_asr_model(self, model: str, backend: str) -> None:
        """Handle ASR model changes atomically so reopen shows the saved model."""
        self._get_runtime().apply_update("asr.model", model)
        self.config.save()
        self._refresh_quick_settings_menu()
        print(f"[Settings] ASR model set to: {self.config.asr.model} ({self.config.asr.backend})")

    def _on_settings_sync_form_state(self, form_state: dict) -> None:
        """Persist the full form state when the settings window closes."""
        self._get_runtime().apply_form_state(form_state)
        self.config.save()
        self._refresh_quick_settings_menu()

    def _on_settings_set_hotkeys(self, hotkeys: list[str]) -> None:
        """Handle active hotkeys change from settings window."""
        self._get_runtime().apply_update("hotkey.active_hotkeys", hotkeys)
        self.config.save()
        print(f"[Settings] Active hotkeys: {self.config.hotkey.active_hotkeys}")

    def _refresh_text_polisher(self) -> None:
        """Recreate text polisher after API key changes and update all modes."""
        import dashscope

        dashscope.api_key = self.config.api_key or None
        self._text_polisher = TextPolisher() if self.config.api_key else None
        for mode in self._all_modes():
            if hasattr(mode, "text_polisher"):
                mode.text_polisher = self._text_polisher
            asr = initialized_resource(getattr(mode, "_asr", None))
            if asr is not None and hasattr(asr, "refresh_api_key"):
                asr.refresh_api_key()

    def _apply_runtime_config_keys(self, changed_keys: set[str]) -> None:
        """Apply runtime side effects for config changes without restarting the app."""
        self._get_runtime()._apply_runtime_config_keys(changed_keys)

    def _on_settings_add_dict(self, term: str, aliases: list[str]) -> None:
        """Handle dictionary entry addition from settings window."""
        get_dictionary().add_entry(term, aliases)
        # Refresh the dictionary in the settings UI
        self._settings_window.update_dictionary(self._get_dict_entries())
        print(f"[Settings] Added dict entry: {term}")

    def _on_settings_remove_dict(self, term: str) -> None:
        """Handle dictionary entry removal from settings window."""
        get_dictionary().remove_entry(term)
        self._settings_window.update_dictionary(self._get_dict_entries())
        print(f"[Settings] Removed dict entry: {term}")

    def _refresh_dictionary_learning_settings(self) -> None:
        learning = getattr(self, "_dictionary_learning", None)
        if learning is None:
            return
        self._settings_window.update_dictionary(self._get_dict_entries())
        self._settings_window.update_dictionary_learning(learning.list_recent())

    def _on_settings_approve_dictionary_learning(self, job_id: str) -> None:
        if self._dictionary_learning and self._dictionary_learning.approve(job_id):
            self._refresh_dictionary_learning_settings()

    def _on_settings_reject_dictionary_learning(self, job_id: str) -> None:
        if self._dictionary_learning and self._dictionary_learning.reject(job_id):
            self._refresh_dictionary_learning_settings()

    def _on_settings_undo_dictionary_learning(self, job_id: str) -> None:
        if self._dictionary_learning and self._dictionary_learning.undo(job_id):
            self._refresh_dictionary_learning_settings()

    def _on_dictionary_learning_change(self, change: dict) -> None:
        """Marshal background learning results onto the AppKit main thread."""
        self._run_on_main_thread(
            lambda: self._handle_dictionary_learning_change(change)
        )

    def _handle_dictionary_learning_change(self, change: dict) -> None:
        settings = getattr(self, "_settings_window", None)
        if settings is not None and settings.is_visible():
            self._refresh_dictionary_learning_settings()

        status = change.get("status")
        if status not in ("applied", "review", "applied_group"):
            return
        if status == "applied" and change.get("suppress_notification"):
            return
        if status == "applied_group":
            terms = [
                str(term).strip()
                for term in change.get("terms", [])
                if str(term).strip()
            ]
            if not terms:
                return
            separator = "”、“" if self.config.ui.language == "zh" else "”, “"
            try:
                rumps.notification(
                    "Vocal-More",
                    self._t(
                        "notification_dictionary_learning_applied_group_title",
                        count=len(terms),
                    ),
                    self._t(
                        "notification_dictionary_learning_applied_group_body",
                        terms=separator.join(terms),
                    ),
                    icon=self._get_logo_path(),
                )
            except RuntimeError:
                print(f"[DictionaryLearning] applied: {', '.join(terms)}")
            return
        if status == "applied" and (
            change.get("source") != "automatic"
            or not change.get("dictionary_changed")
        ):
            return
        term = str(change.get("term", ""))
        try:
            rumps.notification(
                "Vocal-More",
                self._t(
                    "notification_dictionary_learning_applied_title"
                    if status == "applied"
                    else "notification_dictionary_learning_review_title"
                ),
                self._t(
                    "notification_dictionary_learning_applied_body"
                    if status == "applied"
                    else "notification_dictionary_learning_review_body",
                    term=term,
                ),
                icon=self._get_logo_path(),
            )
        except RuntimeError:
            print(f"[DictionaryLearning] {status}: {term}")

    def _on_settings_refresh_devices(self) -> None:
        """Handle device refresh request from settings window."""
        self._refresh_microphone_status_menu(update_settings_window=True)
        self._refresh_environment_status()

    def _on_settings_refresh_environment(self) -> None:
        """Re-check onboarding prerequisites and refresh the visible checklist."""
        self._refresh_environment_status()
        self._settings_window.update_environment_checks(
            [
                check.to_dict()
                for check in getattr(self, "_environment_checks", [])
            ]
        )

    def _on_settings_open_accessibility_settings(self) -> None:
        """Open the exact macOS privacy pane needed by global hotkeys."""
        subprocess.run(
            [
                "open",
                "x-apple.systempreferences:com.apple.preference.security"
                "?Privacy_Accessibility",
            ],
            check=False,
        )

    def _on_settings_open_config(self) -> None:
        """Open config file in default editor."""
        config_path = self.config.get_config_path()
        if not config_path.exists():
            self.config.save()
        subprocess.run(["open", str(config_path)], check=False)

    def _on_settings_open_dict(self) -> None:
        """Open dictionary file in default editor."""
        path = get_dictionary().get_path()
        if not path.exists():
            get_dictionary().save()
        subprocess.run(["open", str(path)], check=False)
        reload_dictionary()
        self._settings_window.update_dictionary(self._get_dict_entries())

    def _on_settings_open_external(self, url: str) -> None:
        """Open external URL in default browser."""
        subprocess.run(["open", url], check=False)

    # ── Helpers ───────────────────────────────────────────────

    def _select_mode(self, mode_name: str) -> None:
        """Select recording mode."""
        if mode_name == "walkie_talkie":
            self._current_mode = self._walkie_talkie
        elif mode_name == "realtime_long":
            self._current_mode = self._realtime_long
        elif mode_name == "meeting":
            self._current_mode = self._meeting

    def _all_modes(self) -> tuple[object, ...]:
        modes = [self._walkie_talkie, self._realtime_long]
        meeting = getattr(self, "_meeting", None)
        if meeting is not None:
            modes.append(meeting)
        return tuple(modes)

    def _select_default_mode_when_safe(self) -> None:
        """Apply the configured default mode without interrupting active recording."""
        self._get_runtime()._select_default_mode_when_safe()

    def _refresh_mode_asr_runtime(self) -> None:
        """Invalidate any idle ASR runtime state affected by config changes."""
        self._get_runtime()._refresh_mode_asr_runtime()

    def _list_devices(self, *, refresh_audio: Optional[bool] = None) -> list[dict]:
        """List available audio input devices."""
        if refresh_audio is None:
            refresh_audio = not self._has_active_audio_capture()
        try:
            return AudioRecorder.list_input_devices(refresh=refresh_audio)
        except Exception as e:
            print(f"[Settings] Error listing devices: {e}")
            return []

    def _has_active_audio_capture(self) -> bool:
        settings_window = getattr(self, "_settings_window", None)
        mic_test_controller = getattr(settings_window, "_mic_test_controller", None)
        if getattr(mic_test_controller, "is_running", False) is True:
            return True

        active_states = {
            ModeState.STARTING,
            ModeState.RECORDING,
            ModeState.STOPPING,
            ModeState.CANCELLING,
        }
        return any(
            getattr(mode, "state", None) in active_states
            for mode in self._all_modes()
        )

    def _clear_missing_configured_microphone(self, devices: list[dict]) -> None:
        selected = self.config.audio.input_device
        if not selected or not devices:
            return

        available_names = {
            str(device.get("name") or "").strip()
            for device in devices
            if str(device.get("name") or "").strip()
        }
        if selected in available_names:
            return

        self._get_runtime().apply_update("audio.input_device", None)
        self.config.save()
        print(
            "[Settings] Cleared unavailable input device after device list refresh: "
            f"{selected}"
        )

    def _refresh_microphone_status_menu(
        self,
        *,
        update_settings_window: bool = False,
    ) -> list[dict]:
        """Refresh the status-bar microphone choices from current hardware state."""
        devices = self._list_devices()
        self._clear_missing_configured_microphone(devices)
        if hasattr(self, "_quick_microphone_item"):
            self._populate_microphone_device_menu(self._quick_microphone_item, devices)
        self._refresh_quick_settings_menu()

        if update_settings_window:
            settings_window = getattr(self, "_settings_window", None)
            update_devices = getattr(settings_window, "update_devices", None)
            if callable(update_devices):
                update_devices(devices, self.config.audio.input_device)
            # update_devices can derive a fresh static plan, but the active
            # mode owns last-session facts and deferred-session semantics.
            self._sync_audio_input_status()

        return devices

    def _populate_microphone_device_menu(
        self,
        item: rumps.MenuItem,
        devices: list[dict],
    ) -> None:
        """Rebuild status-bar microphone choices from the latest device list."""
        self._microphone_device_menu_items = {}
        clear = getattr(item, "clear", None)
        if callable(clear) and getattr(item, "_menu", None) is not None:
            clear()
        elif hasattr(item, "children"):
            item.children.clear()

        item.add(self._microphone_default_item)
        for device in devices:
            name = str(device.get("name") or "").strip()
            if not name or name in self._microphone_device_menu_items:
                continue
            device_item = rumps.MenuItem(
                name,
                callback=lambda _, value=name: self._on_quick_set_microphone_device(value),
            )
            self._microphone_device_menu_items[name] = device_item
            item.add(device_item)
        item.add(self._microphone_settings_item)

    def _refresh_quick_settings_menu(self) -> None:
        """Sync quick-menu checkmarks and labels with current config."""
        if not hasattr(self, "_quick_mode_item"):
            return

        mode_label = self._mode_display_name(self.config.default_mode)
        self._quick_mode_item.title = self._t("menu_recording_mode_title", value=mode_label)
        for mode_name, item in self._mode_menu_items.items():
            item.state = MENU_STATE_ON if mode_name == self.config.default_mode else MENU_STATE_OFF

        microphone_label = self._microphone_display_name()
        self._quick_microphone_item.title = self._t(
            "menu_microphone_title",
            value=microphone_label,
        )
        self._microphone_default_item.title = self._t("menu_microphone_system_default")
        self._microphone_default_item.state = (
            MENU_STATE_ON if not self.config.audio.input_device else MENU_STATE_OFF
        )
        for device_name, item in self._microphone_device_menu_items.items():
            item.state = (
                MENU_STATE_ON
                if device_name == self.config.audio.input_device
                else MENU_STATE_OFF
            )
        self._microphone_settings_item.title = self._t("menu_microphone_settings")

        asr_label = self._asr_model_display_name(self.config.asr.model)
        self._quick_asr_model_item.title = self._t("menu_asr_model_title", value=asr_label)
        for model_id, item in self._asr_model_menu_items.items():
            item.state = MENU_STATE_ON if model_id == self.config.asr.model else MENU_STATE_OFF

        self._quick_enable_polish_item.state = (
            MENU_STATE_ON if self.config.enable_polish else MENU_STATE_OFF
        )

        level_label = self._polish_level_display_name(self.config.llm.level)
        self._quick_polish_level_item.title = self._t(
            "menu_polish_strength_title",
            value=level_label,
        )
        for level, item in self._polish_level_menu_items.items():
            item.state = MENU_STATE_ON if level == self.config.llm.level else MENU_STATE_OFF

    def _environment_check_label(self, key: str) -> str:
        return self._t(f"environment_check_{key}")

    def _environment_check_value(self, key: str, status: str) -> str:
        if status == "unknown":
            return self._t("environment_status_unknown")
        return self._t(f"environment_value_{key}_{status}")

    def _refresh_environment_status(self, show_notification: bool = False) -> None:
        """Re-run environment checks and update the menu."""
        checks = run_environment_checks(
            self.config,
            hotkey_listener_ready=getattr(self, "_hotkey_listener_ready", None),
        )
        self._apply_environment_checks(checks, show_notification=show_notification)

    def _apply_environment_checks(
        self,
        checks: list,
        *,
        show_notification: bool = False,
    ) -> None:
        """Apply completed checks on the AppKit main thread."""
        self._environment_checks = checks
        self._refresh_environment_menu()

        if not show_notification:
            return

        problems = [
            self._environment_check_label(check.key)
            for check in self._environment_checks
            if check.status == "error"
        ]
        if not problems:
            return
        try:
            rumps.notification(
                "Vocal-More",
                self._t("notification_environment_attention_title"),
                self._t(
                    "notification_environment_attention_body",
                    items="、".join(problems),
                ),
                icon=self._get_logo_path(),
            )
        except RuntimeError:
            print(f"[Environment] Issues: {', '.join(problems)}")

    def _refresh_environment_menu(self) -> None:
        """Sync the environment submenu with the latest check results."""
        if not hasattr(self, "_environment_item"):
            return

        checks = list(getattr(self, "_environment_checks", []))

        if any(check.status == "error" for check in checks):
            summary = self._t("environment_status_error")
        elif checks and all(check.status == "ok" for check in checks):
            summary = self._t("environment_status_ok")
        else:
            summary = self._t("environment_status_unknown")

        self._environment_item.title = self._t("menu_environment_title", value=summary)
        self._environment_refresh_item.title = self._t("menu_run_environment_check")

        for check in checks:
            item = self._environment_check_items.get(check.key)
            if item is None:
                continue
            item.title = self._t(
                "environment_check_title",
                name=self._environment_check_label(check.key),
                value=self._environment_check_value(check.key, check.status),
            )
            item.state = MENU_STATE_ON if check.status == "ok" else MENU_STATE_OFF

    def _rerun_environment_check(self, _) -> None:
        """Refresh menu-visible environment checks on demand."""
        if getattr(self, "_dependencies_ready", True) is False:
            return
        was_hotkey_listener_ready = getattr(self, "_hotkey_listener_ready", None) is True
        recovered_hotkey_listener = self._retry_hotkey_listener_if_accessibility_ready(
            show_success_notification=True,
        )
        if recovered_hotkey_listener and not was_hotkey_listener_ready:
            return
        if getattr(self, "_hotkey_listener_ready", None) is False:
            self._start_hotkey_permission_retry_timer()
        self._refresh_environment_status(show_notification=True)

    def _start_hotkey_permission_retry_timer(self) -> None:
        """Retry the hotkey listener after the user grants Accessibility access."""
        if getattr(self, "_hotkey_permission_retry_timer", None) is not None:
            return

        def _fire(_timer) -> None:
            if getattr(self, "_hotkey_listener_ready", None) is True:
                self._stop_hotkey_permission_retry_timer()
                return
            self._retry_hotkey_listener_if_accessibility_ready(
                show_success_notification=True,
            )

        timer = NSTimer.timerWithTimeInterval_repeats_block_(
            HOTKEY_PERMISSION_RETRY_INTERVAL_SECONDS,
            True,
            _fire,
        )
        self._hotkey_permission_retry_timer = timer
        NSRunLoop.mainRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)

    def _stop_hotkey_permission_retry_timer(self) -> None:
        timer = getattr(self, "_hotkey_permission_retry_timer", None)
        if timer is None:
            return

        invalidate = getattr(timer, "invalidate", None)
        if callable(invalidate):
            invalidate()
        self._hotkey_permission_retry_timer = None

    def _retry_hotkey_listener_if_accessibility_ready(
        self,
        *,
        show_success_notification: bool = False,
    ) -> bool:
        """Start hotkeys once macOS reports Accessibility permission is active."""
        if getattr(self, "_hotkey_listener_ready", None) is True:
            self._stop_hotkey_permission_retry_timer()
            return True

        if is_accessibility_trusted() is not True:
            return False

        self._hotkey_listener_ready = bool(self._hotkey_manager.start())
        self._refresh_environment_status()

        if self._hotkey_listener_ready is not True:
            return False

        self._stop_hotkey_permission_retry_timer()
        if show_success_notification:
            self._show_hotkeys_ready_notification()
        return True

    def _show_hotkeys_ready_notification(self) -> None:
        try:
            rumps.notification(
                "Vocal-More",
                self._t("notification_hotkeys_ready_title"),
                self._t("notification_hotkeys_ready_body"),
                icon=self._get_logo_path(),
            )
        except RuntimeError:
            print("[Hotkey] Accessibility permission active; hotkeys are ready.")

    def _show_app_started_notification(self) -> None:
        try:
            rumps.notification(
                "Vocal-More",
                self._t("notification_app_started_title"),
                self._t("notification_app_started_body"),
                icon=self._get_logo_path(),
            )
        except RuntimeError:
            print("[Startup] Vocal-More is running in the menu bar.")

    def _export_diagnostics(self, _) -> None:
        """Export a support bundle with recent traces and environment state."""
        if getattr(self, "_dependencies_ready", True) is False:
            return
        try:
            bundle_path = export_support_bundle(
                config=self.config,
                recording_store=self._recording_store,
                environment_checks=self._environment_checks,
                app_version=__version__,
            )
            subprocess.run(["open", "-R", str(bundle_path)], check=False)
            rumps.notification(
                "Vocal-More",
                self._t("notification_diagnostics_exported_title"),
                self._t(
                    "notification_diagnostics_exported_body",
                    path=str(bundle_path),
                ),
                icon=self._get_logo_path(),
            )
        except Exception as exc:
            try:
                rumps.notification(
                    "Vocal-More",
                    self._t("notification_diagnostics_export_failed_title"),
                    str(exc),
                    icon=self._get_logo_path(),
                )
            except RuntimeError:
                print(f"[Diagnostics] Export failed: {exc}")

    def _on_quick_set_mode(self, mode_name: str) -> None:
        """Switch the default recording mode from the status bar."""
        if getattr(self, "_dependencies_ready", True) is False:
            return
        if mode_name == self.config.default_mode:
            return

        if self._current_mode.state != ModeState.IDLE:
            rumps.notification(
                "Vocal-More",
                self._t("notification_recording_in_progress_title"),
                self._t("notification_recording_in_progress_body"),
                icon=self._get_logo_path(),
            )
            return

        self._get_runtime().apply_update("default_mode", mode_name)
        self.config.save()
        self._refresh_quick_settings_menu()
        print(f"[Menu] Recording mode set to: {self.config.default_mode}")

    def _on_quick_set_asr_model(self, model_id: str) -> None:
        """Switch the ASR model from the status bar."""
        if getattr(self, "_dependencies_ready", True) is False:
            return
        if model_id == self.config.asr.model:
            return

        self._get_runtime().apply_update("asr.model", model_id)
        self.config.save()
        self._refresh_quick_settings_menu()
        print(f"[Menu] ASR model set to: {self.config.asr.model}")

    def _on_quick_set_microphone_device(self, device_name: Optional[str]) -> None:
        """Switch the input microphone from the status bar."""
        if getattr(self, "_dependencies_ready", True) is False:
            return
        current = self.config.audio.input_device
        if (device_name or None) == current:
            return

        self._get_runtime().apply_update("audio.input_device", device_name)
        self.config.save()
        self._refresh_quick_settings_menu()
        self._refresh_environment_status()
        print(f"[Menu] Microphone set to: {device_name or 'System Default'}")

    def _on_quick_toggle_polish(self, _) -> None:
        """Toggle second-stage polishing from the status bar."""
        if getattr(self, "_dependencies_ready", True) is False:
            return
        self._get_runtime().apply_update("enable_polish", not self.config.enable_polish)
        self.config.save()
        self._refresh_quick_settings_menu()
        print(f"[Menu] Enable polish: {self.config.enable_polish}")

    def _on_quick_set_polish_level(self, level: str) -> None:
        """Set polish strength from the status bar."""
        if getattr(self, "_dependencies_ready", True) is False:
            return
        if level == self.config.llm.level:
            return

        self._get_runtime().apply_update("llm.level", level)
        self.config.save()
        self._refresh_quick_settings_menu()
        print(f"[Menu] Polish level set to: {self.config.llm.level}")

    def _mode_display_name(self, mode_name: str) -> str:
        return next(
            (
                self._t(title_key)
                for value, title_key in MODE_MENU_OPTIONS
                if value == mode_name
            ),
            mode_name,
        )

    def _polish_level_display_name(self, level: str) -> str:
        return next(
            (
                self._t(title_key)
                for value, title_key in POLISH_LEVEL_OPTIONS
                if value == level
            ),
            level,
        )

    def _asr_model_display_name(self, model_id: str) -> str:
        return next(
            (
                entry["display_name"]
                for entry in ASR_MODEL_CATALOG
                if entry.get("id") == model_id
            ),
            model_id,
        )

    def _microphone_display_name(self) -> str:
        return self.config.audio.input_device or self._t("menu_microphone_system_default")

    def _sync_audio_recorders(self) -> None:
        """Push the latest audio config into existing recorders."""
        self._get_runtime()._sync_audio_recorders()

    def _build_runtime_facade(self) -> RuntimeFacade:
        from .application.mode_runtime import ModeRuntimeService
        from .application.runtime_facade import RuntimeFacade

        hotkey_manager = getattr(self, "_hotkey_manager", None)
        modes = {
            "walkie_talkie": self._walkie_talkie,
            "realtime_long": self._realtime_long,
        }
        meeting = getattr(self, "_meeting", None)
        if meeting is not None:
            modes["meeting"] = meeting

        return RuntimeFacade(
            config=self.config,
            mode_runtime=ModeRuntimeService(
                modes=modes,
                get_current_mode=lambda: getattr(self, "_current_mode", None),
                set_current_mode=lambda mode: self._select_mode(
                    self._mode_name_for_instance(mode, modes)
                ),
            ),
            on_refresh_text_polisher=self._refresh_text_polisher,
            on_set_active_hotkeys=getattr(hotkey_manager, "set_active_hotkeys", None),
            on_set_custom_key=getattr(hotkey_manager, "set_custom_key", None),
            on_set_custom_keys=getattr(hotkey_manager, "set_custom_keys", None),
            on_apply_interface_language=self._apply_interface_language,
            on_refresh_environment_status=self._refresh_environment_status,
        )

    def _get_runtime(self) -> RuntimeFacade:
        if getattr(self, "_dependencies_ready", True) is False:
            self._ensure_dependencies()
        runtime = getattr(self, "_runtime", None)
        if runtime is None:
            self._runtime = self._build_runtime_facade()
        return self._runtime

    def _mode_name_for_instance(self, mode: object, modes: dict[str, object]) -> str:
        for name, candidate in modes.items():
            if mode is candidate:
                return name
        return self.config.default_mode

    def _build_command_coordinator(self) -> DictationCommandCoordinator:
        from .application.dictation_command_coordinator import (
            DictationCommandCoordinator,
        )

        return DictationCommandCoordinator(thread_name="vocal-more-menu-commands")

    def _get_command_coordinator(self) -> DictationCommandCoordinator:
        coordinator = getattr(self, "_command_coordinator", None)
        if coordinator is None:
            self._command_coordinator = self._build_command_coordinator()
        return self._command_coordinator

    def _close_command_coordinator(self) -> None:
        coordinator = getattr(self, "_command_coordinator", None)
        if coordinator is None:
            return
        coordinator.close()
        self._command_coordinator = None

    @property
    def runtime(self) -> RuntimeFacade:
        return self._get_runtime()

    def _get_dict_entries(self) -> list[dict]:
        """Get dictionary entries as dicts for the settings UI."""
        dictionary = get_dictionary()
        return [
            {"term": e.term, "aliases": e.aliases}
            for e in dictionary.snapshot_entries()
        ]

    # ── Hotkey callbacks ──────────────────────────────────────

    def _on_fn_pressed(self) -> None:
        """Handle Fn key pressed."""
        event_time = time.monotonic()
        self._get_command_coordinator().submit(
            lambda: self._handle_fn_pressed_command(event_time),
            command_name="fn_pressed",
        )

    def _get_hotkey_gesture_controller(self) -> HotkeyGestureController:
        controller = getattr(self, "_hotkey_gesture_controller", None)
        if controller is None:
            controller = HotkeyGestureController()
            self._hotkey_gesture_controller = controller
        return controller

    def _get_command_gesture_controller(self) -> HotkeyGestureController:
        controller = getattr(self, "_command_gesture_controller", None)
        if controller is None:
            controller = HotkeyGestureController()
            self._command_gesture_controller = controller
        return controller

    def _uses_unified_dictation_gesture(self) -> bool:
        return self._current_mode is getattr(self, "_realtime_long", None)

    def _handle_fn_pressed_command(
        self,
        event_time: float | None = None,
    ) -> None:
        if getattr(self, "_command_session_active", False):
            return
        command_time = time.monotonic() if event_time is None else event_time
        if self._uses_unified_dictation_gesture():
            action = self._get_hotkey_gesture_controller().on_pressed(
                command_time,
                self._current_mode.state,
            )
            if action == HotkeyGestureAction.START:
                self._begin_live_benchmark_trace(command_time)
                self._capsule.show(self._capsule_mode_for_current_mode())
                self._current_mode.on_hotkey_pressed()
            elif action == HotkeyGestureAction.STOP:
                self._mark_live_benchmark_trace("speech_end", at=command_time)
                self._current_mode.on_hotkey_pressed()
            return

        if self._current_mode.state == ModeState.IDLE:
            self._begin_live_benchmark_trace(command_time)
            self._capsule.show(self._capsule_mode_for_current_mode())
        elif self._current_mode.state == ModeState.RECORDING:
            self._mark_live_benchmark_trace("speech_end", at=command_time)
        self._current_mode.on_hotkey_pressed()

    def _on_fn_released(self) -> None:
        """Handle Fn key released."""
        event_time = time.monotonic()
        self._get_command_coordinator().submit(
            lambda: self._handle_fn_released_command(event_time),
            command_name="fn_released",
        )

    def _handle_fn_released_command(
        self,
        event_time: float | None = None,
    ) -> None:
        if getattr(self, "_command_session_active", False):
            return
        command_time = time.monotonic() if event_time is None else event_time
        if self._uses_unified_dictation_gesture():
            action = self._get_hotkey_gesture_controller().on_released(
                command_time,
                self._current_mode.state,
            )
            if action == HotkeyGestureAction.STOP:
                self._mark_live_benchmark_trace("speech_end", at=command_time)
                self._current_mode.on_hotkey_pressed()
            return

        if self._current_mode.state == ModeState.RECORDING:
            self._mark_live_benchmark_trace("speech_end", at=command_time)
        self._current_mode.on_hotkey_released()

    def _on_command_pressed(self) -> None:
        event_time = time.monotonic()
        self._get_command_coordinator().submit(
            lambda: self._handle_command_pressed(event_time),
            command_name="command_pressed",
        )

    def _handle_command_pressed(self, event_time: float | None = None) -> None:
        command_time = time.monotonic() if event_time is None else event_time
        active_mode = getattr(self, "_current_mode", None)
        command_active = bool(getattr(self, "_command_session_active", False))
        if (
            active_mode is not None
            and active_mode.state != ModeState.IDLE
            and not command_active
        ):
            return
        if not command_active:
            self._select_mode("realtime_long")
        action = self._get_command_gesture_controller().on_pressed(
            command_time,
            self._current_mode.state,
        )
        if action == HotkeyGestureAction.START:
            self._command_session_active = True
            self._begin_live_benchmark_trace(command_time)
            self._capsule.show("command")
            self._current_mode.on_hotkey_pressed(InputIntent.COMMAND)
        elif action == HotkeyGestureAction.STOP:
            self._mark_live_benchmark_trace("speech_end", at=command_time)
            self._current_mode.on_hotkey_pressed(InputIntent.COMMAND)

    def _on_command_released(self) -> None:
        event_time = time.monotonic()
        self._get_command_coordinator().submit(
            lambda: self._handle_command_released(event_time),
            command_name="command_released",
        )

    def _handle_command_released(self, event_time: float | None = None) -> None:
        command_time = time.monotonic() if event_time is None else event_time
        if self._current_mode is not getattr(self, "_realtime_long", None):
            return
        action = self._get_command_gesture_controller().on_released(
            command_time,
            self._current_mode.state,
        )
        if action == HotkeyGestureAction.STOP:
            self._mark_live_benchmark_trace("speech_end", at=command_time)
            self._current_mode.on_hotkey_pressed(InputIntent.COMMAND)

    def _on_double_cmd(self) -> None:
        """Handle double Cmd key press."""
        self._get_command_coordinator().submit(
            self._handle_double_cmd_command,
            command_name="double_cmd",
        )

    def _handle_double_cmd_command(self) -> None:
        if self._current_mode.state == ModeState.IDLE:
            self._begin_live_benchmark_trace(time.monotonic())
            self._capsule.show(self._capsule_mode_for_current_mode())
        elif self._current_mode.state == ModeState.RECORDING:
            self._mark_live_benchmark_trace("speech_end")
        self._current_mode.on_hotkey_pressed()

    def _on_escape_pressed(self) -> None:
        """Handle Escape key pressed."""
        self._get_command_coordinator().submit(
            self._handle_escape_pressed_command,
            command_name="escape_pressed",
        )

    def _handle_escape_pressed_command(self) -> None:
        if self._current_mode.state in (
            ModeState.STARTING,
            ModeState.STOPPING,
            ModeState.PROCESSING,
            ModeState.CANCELLING,
        ):
            self._handle_cancel_command(reason="escape_cancel")

    # ── Mode state callbacks ──────────────────────────────────

    def _on_state_change(self, state: ModeState) -> None:
        """Handle mode state change."""
        self._run_on_main_thread(lambda: self._apply_state_change(state))

    def _apply_state_change(self, state: ModeState) -> None:
        self._state_item.title = self._state_title_for_state(state)
        self._sync_audio_input_status()

        # Update icon
        icon_name = {
            ModeState.IDLE: "icon_idle.png",
            ModeState.STARTING: "icon_recording.png",
            ModeState.RECORDING: "icon_recording.png",
            ModeState.STOPPING: "icon_processing.png",
            ModeState.PROCESSING: "icon_processing.png",
            ModeState.CANCELLING: "icon_processing.png",
            ModeState.FAILED: "icon_idle.png",
        }.get(state, "icon_idle.png")

        icon_path = self._get_icon_path(icon_name)
        if icon_path:
            self.icon = icon_path

        # Update floating capsule
        capsule_state = {
            ModeState.IDLE: "hidden",
            ModeState.STARTING: "recording",
            ModeState.RECORDING: "recording",
            ModeState.STOPPING: "processing",
            ModeState.PROCESSING: "processing",
            ModeState.CANCELLING: "processing",
            ModeState.FAILED: "hidden",
        }.get(state, "hidden")
        self._capsule.update_state(capsule_state)
        if state in (ModeState.STARTING, ModeState.RECORDING):
            self._mark_live_benchmark_trace("first_feedback")
        elif state == ModeState.FAILED:
            self._finish_live_benchmark_trace(status="failed")
        elif state == ModeState.IDLE:
            trace = getattr(self, "_benchmark_trace", None)
            if trace is not None and trace.active:
                self._finish_live_benchmark_trace(status="failed")
        if state in (ModeState.STOPPING, ModeState.PROCESSING, ModeState.CANCELLING):
            current_mode = getattr(self, "_current_mode", None)
            stage = (
                "meeting_transcribing"
                if current_mode is not None
                and current_mode is getattr(self, "_meeting", None)
                else "transcribing"
            )
            self._capsule.set_processing_stage(stage)
        elif state == ModeState.IDLE:
            self._command_session_active = False
            self._select_default_mode_when_safe()

    def _sync_audio_input_status(self) -> None:
        """Forward the active mode's real capture path to an open settings UI."""
        # Reading AudioRecorder.input_status may cross the native ABI to fetch
        # stream diagnostics. Do not pay that cost for an uninitialized or
        # hidden settings window; in particular, this keeps ordinary mode
        # transitions away from a CoreAudio stop/drain lock.
        settings_window = getattr(self, "_settings_window", None)
        is_visible = getattr(settings_window, "is_visible", None)
        if not callable(is_visible) or not is_visible():
            return
        current_mode = getattr(self, "_current_mode", None)
        status = getattr(current_mode, "audio_input_status", None)
        if not isinstance(status, dict):
            return
        update = getattr(settings_window, "update_audio_input_status", None)
        if callable(update):
            update(status)

    def _on_audio_level(self, rms: float) -> None:
        """Handle real-time audio level from recorder."""
        display_level = waveform_level_from_rms(
            rms,
            ceiling_dbfs=self.config.audio.waveform_ceiling_dbfs,
        )
        self._capsule.update_audio_level(display_level)

    def _on_processing_stage(self, stage: str) -> None:
        """Handle processing stage updates for the floating capsule."""
        if self._capsule:
            self._capsule.set_processing_stage(stage)

    def _on_capsule_cancel(self) -> None:
        """Handle cancel button from floating capsule."""
        self._get_command_coordinator().submit(
            self._handle_cancel_command,
            command_name="capsule_cancel",
        )

    def _handle_cancel_command(self, reason: str = "user_cancel") -> None:
        self._current_mode.cancel(reason=reason)

    def _handle_quit_cancel_command(self) -> None:
        self._handle_cancel_command(reason="app_quit")

    def _on_capsule_finish(self) -> None:
        """Handle finish button from floating capsule (hands-free mode)."""
        self._get_command_coordinator().submit(
            self._handle_capsule_finish_command,
            command_name="capsule_finish",
        )

    def _handle_capsule_finish_command(self) -> None:
        if self._current_mode in (self._realtime_long, getattr(self, "_meeting", None)):
            self._mark_live_benchmark_trace("speech_end")
            self._current_mode.on_hotkey_pressed()

    def _capsule_mode_for_current_mode(self) -> str:
        if self._current_mode is self._walkie_talkie:
            return "pushToTalk"
        if self._current_mode is getattr(self, "_meeting", None):
            return "meeting"
        return "handsFree"

    # ── Result callbacks ──────────────────────────────────────

    def _on_result(self, text: str) -> None:
        """Handle final result."""
        self._finish_live_benchmark_trace(
            status="success",
            insert_completed=bool(self.config.auto_paste),
        )
        self._run_on_main_thread(lambda: self._show_result_notification(text))

    def _show_result_notification(self, text: str) -> None:
        display_text = text[:50] + "..." if len(text) > 50 else text
        try:
            rumps.notification(
                "Vocal-More",
                self._t("notification_transcription_complete_title"),
                display_text,
                icon=self._get_logo_path(),
            )
        except RuntimeError:
            print(f"[Result] {display_text}")

    def _on_partial_result(self, text: str) -> None:
        """Handle partial result — show streaming text in capsule."""
        if text and self._capsule:
            self._capsule.update_streaming_text(text)
            self._mark_live_benchmark_trace("first_partial")

    def _begin_live_benchmark_trace(self, started_at: float) -> None:
        trace = getattr(self, "_benchmark_trace", None)
        current_mode = getattr(self, "_current_mode", None)
        if (
            trace is None
            or current_mode is getattr(self, "_meeting", None)
            or trace.active
        ):
            return
        try:
            trace.begin(
                started_at=started_at,
                metadata={
                    "app_version": __version__,
                    "model": self.config.asr.model,
                    "mode": getattr(current_mode, "name", "unknown"),
                    "auto_paste": bool(self.config.auto_paste),
                    "audio_delivery": getattr(
                        getattr(current_mode, "_recorder", None),
                        "benchmark_audio_delivery",
                        "physical_microphone",
                    ),
                    "sample_id": os.environ.get(
                        "VOCAL_MORE_BENCHMARK_SAMPLE_ID",
                        "",
                    ),
                },
            )
        except Exception as exc:
            print(f"[Benchmark] Failed to begin live trace: {exc}")

    def _mark_live_benchmark_trace(
        self,
        event: str,
        *,
        at: float | None = None,
    ) -> None:
        trace = getattr(self, "_benchmark_trace", None)
        if trace is not None and trace.active:
            try:
                trace.mark(event, at=at) if at is not None else trace.mark(event)
            except Exception as exc:
                print(f"[Benchmark] Failed to mark live trace event: {exc}")

    def _finish_live_benchmark_trace(
        self,
        *,
        status: str,
        insert_completed: bool = False,
    ) -> None:
        trace = getattr(self, "_benchmark_trace", None)
        if trace is not None and trace.active:
            try:
                trace.finish(
                    status=status,
                    insert_completed=insert_completed,
                )
            except Exception as exc:
                print(f"[Benchmark] Failed to finish live trace: {exc}")

    def _on_meeting_result(self, recording_id: str) -> None:
        """Open the history view focused on a completed meeting recording."""
        self._run_on_main_thread(
            lambda: self._show_settings(
                initial_tab="history",
                focus_recording_id=recording_id,
            )
        )

    def _on_error(self, error: str) -> None:
        """Handle error."""
        self._run_on_main_thread(lambda: self._show_error_notification(error))

    def _show_error_notification(self, error: str) -> None:
        try:
            rumps.notification(
                "Vocal-More",
                self._t("notification_error_title"),
                error,
                icon=self._get_logo_path(),
            )
        except RuntimeError:
            print(f"[Error] {error}")

    def _run_on_main_thread(self, callback) -> None:
        """Marshal AppKit updates onto the main run loop."""
        if threading.current_thread() is threading.main_thread():
            callback()
            return

        timer_ref: dict[str, Optional[NSTimer]] = {"timer": None}

        def _fire(_timer) -> None:
            timer = timer_ref["timer"]
            if timer is not None:
                self._main_thread_timers.discard(timer)
            callback()

        timer = NSTimer.timerWithTimeInterval_repeats_block_(0, False, _fire)
        timer_ref["timer"] = timer
        self._main_thread_timers.add(timer)
        NSRunLoop.mainRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)

    # ── Run ───────────────────────────────────────────────────

    def _begin_post_launch_initialization(self) -> None:
        """Start non-visual initialization after the status item exists."""
        elapsed_ms = (time.perf_counter() - _MODULE_IMPORT_STARTED_AT) * 1000
        print(f"[Startup] Status item ready in {elapsed_ms:.0f} ms")
        executor = getattr(self, "_startup_executor", None)
        if executor is None:
            executor = BackgroundExecutor(
                max_workers=1,
                thread_name_prefix="vocal-more-startup",
            )
            self._startup_executor = executor
        self._startup_task = executor.submit(self._finish_post_launch_initialization)

    def _finish_post_launch_initialization(self) -> None:
        """Initialize hotkeys and hardware checks away from the UI thread."""
        if not self._ensure_dependencies():
            return
        hotkey_listener_ready = bool(self._hotkey_manager.start())
        checks = run_environment_checks(
            self.config,
            hotkey_listener_ready=hotkey_listener_ready,
        )

        def _apply() -> None:
            if getattr(self, "_is_quitting", False):
                return
            self._apply_interface_language(update_frontend=False)
            capsule = getattr(self, "_capsule", None)
            warm_up = getattr(capsule, "warm_up", None)
            if callable(warm_up):
                warm_up()
            self._get_sparkle_updater()
            self._hotkey_listener_ready = hotkey_listener_ready
            if not hotkey_listener_ready:
                self._start_hotkey_permission_retry_timer()
            self._apply_environment_checks(checks, show_notification=True)
            self._show_app_started_notification()
            pending_settings = getattr(self, "_pending_settings_request", None)
            self._pending_settings_request = None
            if pending_settings is not None:
                self._show_settings(**pending_settings)
            elif not self.config.ui.onboarding_completed:
                self._show_settings()

        self._run_on_main_thread(_apply)

    def run(self) -> None:
        """Show the status item before starting optional runtime services."""
        rumps_events.before_start.register(self._begin_post_launch_initialization)
        try:
            super().run()
        finally:
            rumps_events.before_start.unregister(
                self._begin_post_launch_initialization
            )


def main() -> None:
    """Main entry point."""
    from .bootstrap import build_menu_app

    install_timestamped_stream("stdout")
    install_timestamped_stream("stderr")
    _ensure_no_proxy("dashscope.aliyuncs.com")
    app = build_menu_app(app_factory=VocalMoreApp)
    app.run()


def _ensure_no_proxy(*hosts: str) -> None:
    """Add hosts to no_proxy/NO_PROXY so WebSocket/HTTPS bypasses local proxies."""
    for var in ("no_proxy", "NO_PROXY"):
        existing = os.environ.get(var, "")
        entries = [e.strip() for e in existing.split(",") if e.strip()]
        for host in hosts:
            if host not in entries:
                entries.append(host)
        os.environ[var] = ",".join(entries)


if __name__ == "__main__":
    main()
