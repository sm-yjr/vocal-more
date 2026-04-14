"""Main Menu Bar application for Vocal-More (standalone Python mode)."""

import os
import subprocess
from pathlib import Path
from typing import Any, Optional

import dashscope
import rumps
from AppKit import NSApp, NSApplicationActivationPolicyAccessory

from . import __version__
from .config import (
    ASR_MODEL_CATALOG,
    LLM_MODEL_CATALOG,
    get_config,
)
from .core.audio_recorder import AudioRecorder
from .core.hotkey_manager import HotkeyManager
from .core.recording_store import RecordingStore
from .core.text_polisher import TextPolisher
from .dictionary import get_dictionary, reload_dictionary
from .modes.base_mode import BaseMode, ModeState
from .modes.realtime_long import RealtimeLongMode
from .modes.walkie_talkie import WalkieTalkieMode
from .ui.floating_capsule import FloatingCapsule
from .ui.settings_window import SettingsWindow

MENU_STATE_OFF = 0
MENU_STATE_ON = 1

MODE_MENU_OPTIONS = [
    ("walkie_talkie", "Walkie-Talkie (Hold)"),
    ("realtime_long", "Real-time Long (Toggle)"),
]

POLISH_LEVEL_OPTIONS = [
    ("minimal", "Minimal"),
    ("balanced", "Balanced"),
    ("strong", "Strong"),
]


class VocalMoreApp(rumps.App):
    """Menu bar application for Vocal-More."""

    def __init__(self):
        """Initialize the application."""
        super().__init__(
            "Vocal-More",
            icon=self._get_icon_path("icon_idle.png"),
            template=True,
            quit_button=None,
        )

        self.config = get_config()

        # Check API key
        error = self.config.ensure_api_key()
        if error:
            rumps.notification(
                "Vocal-More",
                "Configuration Error",
                error,
                icon=self._get_logo_path(),
            )

        # Initialize components
        self._text_polisher: Optional[TextPolisher] = None
        if self.config.api_key:
            self._text_polisher = TextPolisher()

        # Initialize floating capsule UI
        self._capsule = FloatingCapsule(
            on_cancel=self._on_capsule_cancel,
            on_finish=self._on_capsule_finish,
        )

        # Initialize recording store
        self._recording_store = RecordingStore()

        # Initialize modes
        self._walkie_talkie = WalkieTalkieMode(
            on_state_change=self._on_state_change,
            on_result=self._on_result,
            on_partial_result=self._on_partial_result,
            on_error=self._on_error,
            text_polisher=self._text_polisher,
            on_audio_level=self._on_audio_level,
            recording_store=self._recording_store,
        )

        self._realtime_long = RealtimeLongMode(
            on_state_change=self._on_state_change,
            on_result=self._on_result,
            on_partial_result=self._on_partial_result,
            on_error=self._on_error,
            text_polisher=self._text_polisher,
            on_audio_level=self._on_audio_level,
            recording_store=self._recording_store,
        )

        self._current_mode: BaseMode = (
            self._realtime_long if self.config.default_mode == "realtime_long"
            else self._walkie_talkie
        )

        # Initialize hotkey manager
        self._hotkey_manager = HotkeyManager(
            on_fn_pressed=self._on_fn_pressed,
            on_fn_released=self._on_fn_released,
            on_double_cmd=self._on_double_cmd,
        )

        # Initialize settings window
        self._settings_window = SettingsWindow(
            on_set_config=self._on_settings_config_change,
            on_set_asr_model=self._on_settings_set_asr_model,
            on_sync_form_state=self._on_settings_sync_form_state,
            on_set_device=self._on_settings_set_device,
            on_set_active_hotkeys=self._on_settings_set_hotkeys,
            on_add_dict_entry=self._on_settings_add_dict,
            on_remove_dict_entry=self._on_settings_remove_dict,
            on_refresh_devices=self._on_settings_refresh_devices,
            on_open_config_file=self._on_settings_open_config,
            on_open_dict_file=self._on_settings_open_dict,
            on_open_external=self._on_settings_open_external,
            recording_store=self._recording_store,
        )

        # Build menu
        self._build_menu()

    # ── Resource paths ────────────────────────────────────────

    def _get_icon_path(self, icon_name: str) -> Optional[str]:
        """Get icon path."""
        package_dir = Path(__file__).parent.parent.parent
        icon_path = package_dir / "resources" / "icons" / icon_name
        if icon_path.exists():
            return str(icon_path)
        return None

    def _get_logo_path(self) -> Optional[str]:
        """Get logo path for notifications."""
        package_dir = Path(__file__).parent.parent.parent
        logo_path = package_dir / "assets" / "logo.png"
        if logo_path.exists():
            return str(logo_path)
        return None

    # ── Menu ──────────────────────────────────────────────────

    def _build_menu(self) -> None:
        """Build a simplified menu bar menu."""
        self._status_item = rumps.MenuItem(f"Vocal-More {__version__}")
        self._status_item.set_callback(None)

        self._state_item = rumps.MenuItem("Status: Idle")
        self._state_item.set_callback(None)

        self._quick_settings_item = self._build_quick_settings_item()

        self.menu = [
            self._status_item,
            self._state_item,
            None,
            self._quick_settings_item,
            rumps.MenuItem("Settings...", callback=self._open_settings),
            None,
            rumps.MenuItem("Quit Vocal-More", callback=self._quit_app),
        ]
        self._refresh_quick_settings_menu()

    def _build_quick_settings_item(self) -> rumps.MenuItem:
        """Build the status-bar quick settings submenu."""
        quick_settings = rumps.MenuItem("Quick Settings")

        self._quick_mode_item = rumps.MenuItem("Recording Mode")
        self._mode_menu_items: dict[str, rumps.MenuItem] = {}
        for mode_name, title in MODE_MENU_OPTIONS:
            item = rumps.MenuItem(
                title,
                callback=lambda _, value=mode_name: self._on_quick_set_mode(value),
            )
            self._mode_menu_items[mode_name] = item
            self._quick_mode_item.add(item)
        quick_settings.add(self._quick_mode_item)

        self._quick_asr_model_item = rumps.MenuItem("ASR Model")
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
        quick_settings.add(self._quick_asr_model_item)

        self._quick_enable_polish_item = rumps.MenuItem(
            "Enable Polishing",
            callback=self._on_quick_toggle_polish,
        )
        quick_settings.add(self._quick_enable_polish_item)

        self._quick_polish_level_item = rumps.MenuItem("Polish Strength")
        self._polish_level_menu_items: dict[str, rumps.MenuItem] = {}
        for level, title in POLISH_LEVEL_OPTIONS:
            item = rumps.MenuItem(
                title,
                callback=lambda _, value=level: self._on_quick_set_polish_level(value),
            )
            self._polish_level_menu_items[level] = item
            self._quick_polish_level_item.add(item)
        quick_settings.add(self._quick_polish_level_item)

        return quick_settings

    def _open_settings(self, _) -> None:
        """Open the settings window."""
        devices = self._list_devices()
        dictionary = self._get_dict_entries()
        self._settings_window.show(
            config=self.config.to_dict(),
            asr_models=ASR_MODEL_CATALOG,
            llm_models=LLM_MODEL_CATALOG,
            devices=devices,
            dictionary=dictionary,
            version=__version__,
        )

    def _quit_app(self, _) -> None:
        """Quit the application."""
        self._current_mode.cancel()
        self._capsule.hide()
        self._settings_window.hide()
        self._hotkey_manager.stop()
        rumps.quit_application()

    # ── Settings window callbacks ─────────────────────────────

    def _on_settings_config_change(self, key: str, value: Any) -> None:
        """Handle config change from settings window."""
        try:
            self.config.apply_update(key, value)
        except ValueError as exc:
            print(f"[Settings] {exc}")
            return

        if key == "api_key":
            self._refresh_text_polisher()
        elif key == "default_mode":
            self._select_mode(self.config.default_mode)
        elif key.startswith("audio."):
            self._sync_audio_recorders()
        elif key == "hotkey.custom_key":
            self._hotkey_manager.set_custom_key(self.config.hotkey.custom_key)

        self.config.save()
        self._refresh_quick_settings_menu()
        print(f"[Settings] Config updated: {key} = {value}")

    def _on_settings_set_device(self, device: Optional[str]) -> None:
        """Handle device change from settings window."""
        self.config.apply_update("audio.input_device", device)
        self.config.save()
        self._sync_audio_recorders()
        print(f"[Settings] Device set to: {device or 'System Default'}")

    def _on_settings_set_asr_model(self, model: str, backend: str) -> None:
        """Handle ASR model changes atomically so reopen shows the saved model."""
        self.config.apply_update("asr.model", model)
        self.config.save()
        self._refresh_quick_settings_menu()
        print(f"[Settings] ASR model set to: {self.config.asr.model} ({self.config.asr.backend})")

    def _on_settings_sync_form_state(self, form_state: dict) -> None:
        """Persist the full form state when the settings window closes."""
        self.config.apply_form_state(form_state)

        self._refresh_text_polisher()
        self._select_mode(self.config.default_mode)
        self._hotkey_manager.set_active_hotkeys(self.config.hotkey.active_hotkeys)
        self._hotkey_manager.set_custom_key(self.config.hotkey.custom_key)
        self._sync_audio_recorders()

        self.config.save()
        self._refresh_quick_settings_menu()

    def _on_settings_set_hotkeys(self, hotkeys: list[str]) -> None:
        """Handle active hotkeys change from settings window."""
        if not hotkeys:
            return
        self.config.apply_update("hotkey.active_hotkeys", hotkeys)
        self.config.save()
        self._hotkey_manager.set_active_hotkeys(self.config.hotkey.active_hotkeys)
        print(f"[Settings] Active hotkeys: {self.config.hotkey.active_hotkeys}")

    def _refresh_text_polisher(self) -> None:
        """Recreate text polisher after API key changes and update all modes."""
        dashscope.api_key = self.config.api_key or None
        self._text_polisher = TextPolisher() if self.config.api_key else None
        for mode in (self._walkie_talkie, self._realtime_long):
            mode.text_polisher = self._text_polisher

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
        self._settings_window.update_devices(devices)

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

    def _list_devices(self) -> list[dict]:
        """List available audio input devices."""
        try:
            return AudioRecorder.list_input_devices()
        except Exception as e:
            print(f"[Settings] Error listing devices: {e}")
            return []

    def _refresh_quick_settings_menu(self) -> None:
        """Sync quick-menu checkmarks and labels with current config."""
        if not hasattr(self, "_quick_mode_item"):
            return

        mode_label = self._mode_display_name(self.config.default_mode)
        self._quick_mode_item.title = f"Recording Mode: {mode_label}"
        for mode_name, item in self._mode_menu_items.items():
            item.state = MENU_STATE_ON if mode_name == self.config.default_mode else MENU_STATE_OFF

        asr_label = self._asr_model_display_name(self.config.asr.model)
        self._quick_asr_model_item.title = f"ASR Model: {asr_label}"
        for model_id, item in self._asr_model_menu_items.items():
            item.state = MENU_STATE_ON if model_id == self.config.asr.model else MENU_STATE_OFF

        self._quick_enable_polish_item.state = (
            MENU_STATE_ON if self.config.enable_polish else MENU_STATE_OFF
        )

        level_label = self._polish_level_display_name(self.config.llm.level)
        self._quick_polish_level_item.title = f"Polish Strength: {level_label}"
        for level, item in self._polish_level_menu_items.items():
            item.state = MENU_STATE_ON if level == self.config.llm.level else MENU_STATE_OFF

    def _on_quick_set_mode(self, mode_name: str) -> None:
        """Switch the default recording mode from the status bar."""
        if mode_name == self.config.default_mode:
            return

        if self._current_mode.state != ModeState.IDLE:
            rumps.notification(
                "Vocal-More",
                "Recording In Progress",
                "Stop the current session before switching recording modes.",
                icon=self._get_logo_path(),
            )
            return

        self.config.apply_update("default_mode", mode_name)
        self._select_mode(self.config.default_mode)
        self.config.save()
        self._refresh_quick_settings_menu()
        print(f"[Menu] Recording mode set to: {self.config.default_mode}")

    def _on_quick_set_asr_model(self, model_id: str) -> None:
        """Switch the ASR model from the status bar."""
        if model_id == self.config.asr.model:
            return

        self.config.apply_update("asr.model", model_id)
        self.config.save()
        self._refresh_quick_settings_menu()
        print(f"[Menu] ASR model set to: {self.config.asr.model}")

    def _on_quick_toggle_polish(self, _) -> None:
        """Toggle second-stage polishing from the status bar."""
        self.config.apply_update("enable_polish", not self.config.enable_polish)
        self.config.save()
        self._refresh_quick_settings_menu()
        print(f"[Menu] Enable polish: {self.config.enable_polish}")

    def _on_quick_set_polish_level(self, level: str) -> None:
        """Set polish strength from the status bar."""
        if level == self.config.llm.level:
            return

        self.config.apply_update("llm.level", level)
        self.config.save()
        self._refresh_quick_settings_menu()
        print(f"[Menu] Polish level set to: {self.config.llm.level}")

    def _mode_display_name(self, mode_name: str) -> str:
        return next(
            (title for value, title in MODE_MENU_OPTIONS if value == mode_name),
            mode_name,
        )

    def _polish_level_display_name(self, level: str) -> str:
        return next(
            (title for value, title in POLISH_LEVEL_OPTIONS if value == level),
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

    def _sync_audio_recorders(self) -> None:
        """Push the latest audio config into existing recorders."""
        for mode in (self._walkie_talkie, self._realtime_long):
            recorder = getattr(mode, "_recorder", None)
            if recorder is None:
                continue
            recorder.set_device(self.config.audio.input_device)
            recorder.set_gain(self.config.audio.gain)
            recorder.set_noise_gate(self.config.audio.noise_gate)
            recorder.set_highpass_filter(self.config.audio.highpass_filter)
            recorder.set_highpass_freq(self.config.audio.highpass_freq)
            recorder.set_soft_limiter(self.config.audio.soft_limiter)

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
        if self._current_mode.state == ModeState.IDLE:
            if self._current_mode is self._walkie_talkie:
                self._capsule.show("pushToTalk")
            else:
                self._capsule.show("handsFree")
        self._current_mode.on_hotkey_pressed()

    def _on_fn_released(self) -> None:
        """Handle Fn key released."""
        self._current_mode.on_hotkey_released()

    def _on_double_cmd(self) -> None:
        """Handle double Cmd key press."""
        if self._current_mode.state == ModeState.IDLE:
            if self._current_mode is self._walkie_talkie:
                self._capsule.show("pushToTalk")
            else:
                self._capsule.show("handsFree")
        self._current_mode.on_hotkey_pressed()

    # ── Mode state callbacks ──────────────────────────────────

    def _on_state_change(self, state: ModeState) -> None:
        """Handle mode state change."""
        status_text = {
            ModeState.IDLE: "Status: Idle",
            ModeState.RECORDING: "Status: Recording...",
            ModeState.PROCESSING: "Status: Processing...",
        }.get(state, "Status: Unknown")

        self._state_item.title = status_text

        # Update icon
        icon_name = {
            ModeState.IDLE: "icon_idle.png",
            ModeState.RECORDING: "icon_recording.png",
            ModeState.PROCESSING: "icon_processing.png",
        }.get(state, "icon_idle.png")

        icon_path = self._get_icon_path(icon_name)
        if icon_path:
            self.icon = icon_path

        # Update floating capsule
        capsule_state = {
            ModeState.IDLE: "hidden",
            ModeState.RECORDING: "recording",
            ModeState.PROCESSING: "processing",
        }.get(state, "hidden")
        self._capsule.update_state(capsule_state)

    def _on_audio_level(self, rms: float) -> None:
        """Handle real-time audio level from recorder."""
        self._capsule.update_audio_level(rms)

    def _on_capsule_cancel(self) -> None:
        """Handle cancel button from floating capsule."""
        self._current_mode.cancel()

    def _on_capsule_finish(self) -> None:
        """Handle finish button from floating capsule (hands-free mode)."""
        if self._current_mode is self._realtime_long:
            self._current_mode.on_hotkey_pressed()

    # ── Result callbacks ──────────────────────────────────────

    def _on_result(self, text: str) -> None:
        """Handle final result."""
        display_text = text[:50] + "..." if len(text) > 50 else text
        try:
            rumps.notification(
                "Vocal-More",
                "Transcription Complete",
                display_text,
                icon=self._get_logo_path(),
            )
        except RuntimeError:
            print(f"[Result] {display_text}")

    def _on_partial_result(self, text: str) -> None:
        """Handle partial result — show streaming text in capsule."""
        if text and self._capsule:
            self._capsule.update_streaming_text(text)

    def _on_error(self, error: str) -> None:
        """Handle error."""
        try:
            rumps.notification(
                "Vocal-More",
                "Error",
                error,
                icon=self._get_logo_path(),
            )
        except RuntimeError:
            print(f"[Error] {error}")

    # ── Run ───────────────────────────────────────────────────

    def run(self) -> None:
        """Run the application."""
        if not self._hotkey_manager.start():
            rumps.notification(
                "Vocal-More",
                "Permissions Required",
                "Please grant Accessibility permissions in System Settings → "
                "Privacy & Security → Accessibility",
                icon=self._get_logo_path(),
            )
        super().run()


def main() -> None:
    """Main entry point."""
    _ensure_no_proxy("dashscope.aliyuncs.com")
    app = VocalMoreApp()
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
