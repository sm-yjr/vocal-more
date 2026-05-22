"""Main Menu Bar application for Vocal-More (standalone Python mode)."""

import os
import subprocess
import threading
from typing import Any, Optional

import dashscope
import rumps
from Foundation import NSRunLoop, NSRunLoopCommonModes, NSTimer

from . import __version__
from .application.dictation_command_coordinator import DictationCommandCoordinator
from .application.runtime_facade import RuntimeFacade
from .bootstrap import build_menu_app_dependencies
from .config import (
    ASR_MODEL_CATALOG,
    LLM_MODEL_CATALOG,
)
from .core.audio_recorder import AudioRecorder
from .core.hotkey_manager import HotkeyManager
from .core.recording_store import RecordingStore
from .core.text_polisher import TextPolisher
from .diagnostics import ensure_runtime_debug_dir_env, export_support_bundle
from .dictionary import get_dictionary, reload_dictionary
from .environment_check import run_environment_checks
from .localization import t
from .modes.base_mode import BaseMode, ModeState
from .modes.meeting import MeetingMode
from .modes.realtime_long import RealtimeLongMode
from .modes.walkie_talkie import WalkieTalkieMode
from .paths import bundled_resource_path
from .infrastructure.timestamped_output import install_timestamped_stream
from .ui.floating_capsule import FloatingCapsule
from .ui.settings_window import SettingsWindow

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

        dependencies = dependencies or build_menu_app_dependencies(
            self,
            text_polisher_factory=TextPolisher,
            capsule_factory=FloatingCapsule,
            recording_store_factory=RecordingStore,
            walkie_talkie_factory=WalkieTalkieMode,
            realtime_long_factory=RealtimeLongMode,
            meeting_factory=MeetingMode,
            hotkey_manager_factory=HotkeyManager,
            settings_window_factory=SettingsWindow,
        )
        self._apply_dependencies(dependencies)
        self._apply_interface_language(update_frontend=False)

        # Build menu
        self._build_menu()
        self._refresh_environment_status()

    def _apply_dependencies(self, dependencies) -> None:
        self.config = dependencies.config
        self._hotkey_listener_ready = dependencies.hotkey_listener_ready
        self._environment_checks = dependencies.environment_checks
        self._text_polisher = dependencies.text_polisher
        self._capsule = dependencies.capsule
        self._recording_store = dependencies.recording_store
        self._walkie_talkie = dependencies.walkie_talkie
        self._realtime_long = dependencies.realtime_long
        self._meeting = dependencies.meeting
        self._current_mode = dependencies.current_mode
        self._command_coordinator = dependencies.command_coordinator
        self._hotkey_manager = dependencies.hotkey_manager
        self._runtime = dependencies.runtime
        self._settings_window = dependencies.settings_window
        self._main_thread_timers: set[NSTimer] = set()

    # ── Resource paths ────────────────────────────────────────

    def _get_icon_path(self, icon_name: str) -> Optional[str]:
        """Get icon path."""
        icon_path = bundled_resource_path("resources", "icons", icon_name)
        if icon_path.exists():
            return str(icon_path)
        return None

    def _get_logo_path(self) -> Optional[str]:
        """Get logo path for notifications."""
        logo_path = bundled_resource_path("assets", "logo.png")
        if logo_path.exists():
            return str(logo_path)
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
            self._export_diagnostics_item,
            self._settings_menu_item,
            None,
            self._quit_menu_item,
        ]
        self._refresh_quick_settings_menu()
        self._refresh_environment_menu()

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
        self._populate_microphone_device_menu(item, self._list_devices())
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
        devices = self._list_devices()
        self._clear_missing_configured_microphone(devices)
        self._populate_microphone_device_menu(self._quick_microphone_item, devices)
        dictionary = self._get_dict_entries()
        self._settings_window.show(
            config=self.config.to_dict(),
            asr_models=ASR_MODEL_CATALOG,
            llm_models=LLM_MODEL_CATALOG,
            devices=devices,
            dictionary=dictionary,
            version=__version__,
            initial_tab=initial_tab,
            focus_recording_id=focus_recording_id,
        )

    def _quit_app(self, _) -> None:
        """Quit the application."""
        self._hotkey_manager.stop()
        self._get_command_coordinator().call(
            self._handle_quit_cancel_command,
            command_name="quit_cancel",
        )
        self._capsule.hide()
        self._settings_window.close()
        for mode in self._all_modes():
            close = getattr(mode, "close", None)
            if callable(close):
                close()
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
        self._refresh_quick_settings_menu()
        print(f"[Settings] Config updated: {key} = {value}")

    def _on_settings_set_device(self, device: Optional[str]) -> None:
        """Handle device change from settings window."""
        self._get_runtime().apply_update("audio.input_device", device)
        self.config.save()
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
        dashscope.api_key = self.config.api_key or None
        self._text_polisher = TextPolisher() if self.config.api_key else None
        for mode in self._all_modes():
            if hasattr(mode, "text_polisher"):
                mode.text_polisher = self._text_polisher
            asr = getattr(mode, "_asr", None)
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

    def _on_settings_refresh_devices(self) -> None:
        """Handle device refresh request from settings window."""
        devices = self._list_devices()
        self._clear_missing_configured_microphone(devices)
        self._populate_microphone_device_menu(self._quick_microphone_item, devices)
        self._refresh_quick_settings_menu()
        self._settings_window.update_devices(devices, self.config.audio.input_device)
        self._refresh_environment_status()

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
        self._environment_checks = run_environment_checks(
            self.config,
            hotkey_listener_ready=getattr(self, "_hotkey_listener_ready", None),
        )
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
        self._refresh_environment_status(show_notification=True)

    def _export_diagnostics(self, _) -> None:
        """Export a support bundle with recent traces and environment state."""
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
        if model_id == self.config.asr.model:
            return

        self._get_runtime().apply_update("asr.model", model_id)
        self.config.save()
        self._refresh_quick_settings_menu()
        print(f"[Menu] ASR model set to: {self.config.asr.model}")

    def _on_quick_set_microphone_device(self, device_name: Optional[str]) -> None:
        """Switch the input microphone from the status bar."""
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
        self._get_runtime().apply_update("enable_polish", not self.config.enable_polish)
        self.config.save()
        self._refresh_quick_settings_menu()
        print(f"[Menu] Enable polish: {self.config.enable_polish}")

    def _on_quick_set_polish_level(self, level: str) -> None:
        """Set polish strength from the status bar."""
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
            modes=modes,
            get_current_mode=lambda: getattr(self, "_current_mode", None),
            set_current_mode=lambda mode: self._select_mode(self._mode_name_for_instance(mode, modes)),
            on_refresh_text_polisher=self._refresh_text_polisher,
            on_set_active_hotkeys=getattr(hotkey_manager, "set_active_hotkeys", None),
            on_set_custom_key=getattr(hotkey_manager, "set_custom_key", None),
            on_apply_interface_language=self._apply_interface_language,
            on_refresh_environment_status=self._refresh_environment_status,
        )

    def _get_runtime(self) -> RuntimeFacade:
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
            for e in dictionary.entries
        ]

    # ── Hotkey callbacks ──────────────────────────────────────

    def _on_fn_pressed(self) -> None:
        """Handle Fn key pressed."""
        self._get_command_coordinator().submit(
            self._handle_fn_pressed_command,
            command_name="fn_pressed",
        )

    def _handle_fn_pressed_command(self) -> None:
        if self._current_mode.state == ModeState.IDLE:
            self._capsule.show(self._capsule_mode_for_current_mode())
        self._current_mode.on_hotkey_pressed()

    def _on_fn_released(self) -> None:
        """Handle Fn key released."""
        self._get_command_coordinator().submit(
            self._handle_fn_released_command,
            command_name="fn_released",
        )

    def _handle_fn_released_command(self) -> None:
        self._current_mode.on_hotkey_released()

    def _on_double_cmd(self) -> None:
        """Handle double Cmd key press."""
        self._get_command_coordinator().submit(
            self._handle_double_cmd_command,
            command_name="double_cmd",
        )

    def _handle_double_cmd_command(self) -> None:
        if self._current_mode.state == ModeState.IDLE:
            self._capsule.show(self._capsule_mode_for_current_mode())
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
            self._select_default_mode_when_safe()

    def _on_audio_level(self, rms: float) -> None:
        """Handle real-time audio level from recorder."""
        self._capsule.update_audio_level(rms)

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

    def run(self) -> None:
        """Run the application."""
        self._hotkey_listener_ready = self._hotkey_manager.start()
        self._refresh_environment_status(show_notification=True)
        super().run()


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
