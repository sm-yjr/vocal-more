"""Windows global-hotkey adapter built on pynput.

Most laptop Fn keys never reach ordinary Windows keyboard hooks. The portable
built-in trigger is therefore F8. Persisted custom bindings are translated from
their browser-code identity when the same physical key exists on Windows.
"""

from __future__ import annotations

from enum import Enum
import queue
import threading
import time
from typing import Callable, Optional

from ..domain.hotkey_catalog import CUSTOM_HOTKEY_KEYS_BY_CODE


DEFAULT_WINDOWS_HOTKEY_LABEL = "F8"
DEFAULT_WINDOWS_HOTKEY_TOKEN = "key:f8"


class HotkeyEvent(Enum):
    """Logical events emitted by the Windows listener."""

    TRIGGER_PRESSED = "trigger_pressed"
    TRIGGER_RELEASED = "trigger_released"
    ESC_PRESSED = "esc_pressed"


_BROWSER_CODE_TOKEN_MAP = {
    "MetaLeft": "key:cmd_l",
    "MetaRight": "key:cmd_r",
    "ShiftLeft": "key:shift_l",
    "ShiftRight": "key:shift_r",
    "AltLeft": "key:alt_l",
    "AltRight": "key:alt_r",
    "ControlLeft": "key:ctrl_l",
    "ControlRight": "key:ctrl_r",
    "CapsLock": "key:caps_lock",
    "Enter": "key:enter",
    "Tab": "key:tab",
    "Space": "key:space",
    "Backspace": "key:backspace",
    "Escape": "key:esc",
    "ArrowLeft": "key:left",
    "ArrowRight": "key:right",
    "ArrowDown": "key:down",
    "ArrowUp": "key:up",
    "Home": "key:home",
    "End": "key:end",
    "PageUp": "key:page_up",
    "PageDown": "key:page_down",
    "Delete": "key:delete",
    "Help": "key:help",
    "NumLock": "key:num_lock",
    "Numpad0": "vk:96",
    "Numpad1": "vk:97",
    "Numpad2": "vk:98",
    "Numpad3": "vk:99",
    "Numpad4": "vk:100",
    "Numpad5": "vk:101",
    "Numpad6": "vk:102",
    "Numpad7": "vk:103",
    "Numpad8": "vk:104",
    "Numpad9": "vk:105",
    "NumpadMultiply": "vk:106",
    "NumpadAdd": "vk:107",
    "NumpadSubtract": "vk:109",
    "NumpadDecimal": "vk:110",
    "NumpadDivide": "vk:111",
    "NumpadEnter": "vk:13",
    "NumpadEqual": "vk:187",
    "IntlBackslash": "vk:226",
}


_WINDOWS_DISPLAY_NAMES = {
    "MetaLeft": "Left Windows",
    "MetaRight": "Right Windows",
    "AltLeft": "Left Alt",
    "AltRight": "Right Alt",
    "Enter": "Enter",
    "Backspace": "Backspace",
    "NumLock": "Num Lock",
}

_PUNCTUATION_BROWSER_VKS = {
    "Backquote": 192,
    "Minus": 189,
    "Equal": 187,
    "BracketLeft": 219,
    "BracketRight": 221,
    "Backslash": 220,
    "Semicolon": 186,
    "Quote": 222,
    "Comma": 188,
    "Period": 190,
    "Slash": 191,
}

_KEY_NAME_ALIASES = {
    "escape": "esc",
    "return": "enter",
    "pageup": "page_up",
    "pagedown": "page_down",
    "capslock": "caps_lock",
    "numlock": "num_lock",
    "control": "ctrl",
}

# Pynput exposes printable keys as KeyCode(char=..., vk=...). Prefer virtual-key
# identity for persisted physical bindings so Shift+1 still matches Digit 1 and
# keyboard-layout punctuation does not silently change the configured key.
_VK_IDENTITY_VALUES = {
    13,
    186,
    187,
    188,
    189,
    190,
    191,
    192,
    219,
    220,
    221,
    222,
    226,
    *range(48, 58),
    *range(65, 91),
    *range(96, 112),
}


def token_for_browser_code(browser_code: str) -> str | None:
    """Map a persisted browser key identity to a pynput event token."""
    code = str(browser_code or "").strip()
    if not code:
        return None
    if code in _BROWSER_CODE_TOKEN_MAP:
        return _BROWSER_CODE_TOKEN_MAP[code]
    if code in _PUNCTUATION_BROWSER_VKS:
        return f"vk:{_PUNCTUATION_BROWSER_VKS[code]}"
    if code.startswith("Key") and len(code) == 4:
        return f"vk:{ord(code[-1].upper())}"
    if code.startswith("Digit") and len(code) == 6:
        return f"vk:{ord(code[-1])}"
    if code.startswith("F") and code[1:].isdigit():
        number = int(code[1:])
        if 1 <= number <= 24:
            return f"key:f{number}"
    return None


def token_for_pynput_key(key: object) -> str | None:
    """Normalize a pynput Key/KeyCode without importing pynput at module load."""
    name = getattr(key, "name", None)
    if isinstance(name, str) and name:
        observed = name.strip().casefold()
        normalized = _KEY_NAME_ALIASES.get(observed, observed)
        return f"key:{normalized}"

    vk = getattr(key, "vk", None)
    if isinstance(vk, int) and vk in _VK_IDENTITY_VALUES:
        return f"vk:{vk}"

    char = getattr(key, "char", None)
    if isinstance(char, str) and char:
        return f"char:{char.casefold()}"

    if isinstance(vk, int):
        return f"vk:{vk}"
    return None


def _custom_key_definition(custom_key: object):
    if not isinstance(custom_key, dict):
        return None
    key_code = custom_key.get("key_code")
    if isinstance(key_code, bool):
        return None
    try:
        return CUSTOM_HOTKEY_KEYS_BY_CODE.get(int(key_code))
    except (TypeError, ValueError):
        return None


def _custom_key_token(custom_key: object) -> str | None:
    definition = _custom_key_definition(custom_key)
    if definition is None:
        return None
    return token_for_browser_code(definition.browser_code)


def _custom_key_label(custom_key: object) -> str:
    definition = _custom_key_definition(custom_key)
    if definition is None:
        return ""
    return _WINDOWS_DISPLAY_NAMES.get(
        definition.browser_code,
        definition.display_name,
    )


class WindowsHotkeyManager:
    """Detect one aggregate dictation trigger and Escape globally on Windows."""

    def __init__(
        self,
        on_fn_pressed: Optional[Callable[[], None]] = None,
        on_fn_released: Optional[Callable[[], None]] = None,
        on_double_cmd: Optional[Callable[[], None]] = None,
        on_escape_pressed: Optional[Callable[[], None]] = None,
        *,
        config=None,
        keyboard_module=None,
    ) -> None:
        if config is None:
            from ..config import get_config

            config = get_config()

        self.config = config
        self.on_fn_pressed = on_fn_pressed
        self.on_fn_released = on_fn_released
        # Retain the common adapter signature. Windows has no double-Cmd path.
        self.on_double_cmd = on_double_cmd
        self.on_escape_pressed = on_escape_pressed
        self._keyboard_module = keyboard_module

        self._active_hotkeys = list(self.config.hotkey.active_hotkeys)
        self._custom_keys = list(
            self.config.hotkey.custom_keys
            or (
                [self.config.hotkey.custom_key]
                if self.config.hotkey.custom_key is not None
                else []
            )
        )
        self._trigger_tokens: set[str] = set()
        self._pressed_tokens: set[str] = set()
        self._escape_down = False
        self._state_lock = threading.Lock()
        self._rebuild_trigger_tokens()

        self._listener = None
        self._callback_thread: threading.Thread | None = None
        self._callback_queue: queue.Queue[HotkeyEvent | None] = queue.Queue()
        self._worker_lock = threading.Lock()
        self._running = False

    @property
    def trigger_label(self) -> str:
        labels: list[str] = []
        if "fn" in self._active_hotkeys:
            labels.append(DEFAULT_WINDOWS_HOTKEY_LABEL)
        for custom_key in self._custom_keys:
            if not isinstance(custom_key, dict):
                continue
            label = _custom_key_label(custom_key)
            if label and label not in labels and _custom_key_token(custom_key):
                labels.append(label)
        return " / ".join(labels) or DEFAULT_WINDOWS_HOTKEY_LABEL

    def _rebuild_trigger_tokens(self) -> None:
        tokens: set[str] = set()
        if "fn" in self._active_hotkeys:
            tokens.add(DEFAULT_WINDOWS_HOTKEY_TOKEN)
        for custom_key in self._custom_keys:
            token = _custom_key_token(custom_key)
            if token:
                tokens.add(token)
        with self._state_lock:
            self._trigger_tokens = tokens
            self._pressed_tokens.intersection_update(tokens)

    def _on_press(self, key: object) -> None:
        token = token_for_pynput_key(key)
        if token is None:
            return

        emit_escape = False
        with self._state_lock:
            if token in self._trigger_tokens:
                if token in self._pressed_tokens:
                    return
                was_pressed = bool(self._pressed_tokens)
                self._pressed_tokens.add(token)
                emit_trigger = not was_pressed
            else:
                emit_trigger = False
                if token != "key:esc" or self._escape_down:
                    return
                self._escape_down = True
                emit_escape = True

        if emit_trigger:
            if self.on_fn_pressed is not None:
                self._enqueue(HotkeyEvent.TRIGGER_PRESSED)
            return
        if emit_escape and self.on_escape_pressed is not None:
            self._enqueue(HotkeyEvent.ESC_PRESSED)

    def _on_release(self, key: object) -> None:
        token = token_for_pynput_key(key)
        if token is None:
            return

        with self._state_lock:
            if token in self._pressed_tokens:
                self._pressed_tokens.discard(token)
                emit_release = not self._pressed_tokens
            else:
                emit_release = False
                if token == "key:esc":
                    self._escape_down = False

        if emit_release and self.on_fn_released is not None:
            self._enqueue(HotkeyEvent.TRIGGER_RELEASED)

    def _enqueue(self, event: HotkeyEvent) -> None:
        self._start_callback_worker()
        self._callback_queue.put(event)

    def _dispatch(self, event: HotkeyEvent) -> None:
        callback = None
        if event is HotkeyEvent.TRIGGER_PRESSED:
            callback = self.on_fn_pressed
        elif event is HotkeyEvent.TRIGGER_RELEASED:
            callback = self.on_fn_released
        elif event is HotkeyEvent.ESC_PRESSED:
            callback = self.on_escape_pressed
        if callback is None:
            return
        try:
            callback()
        except Exception as exc:
            print(f"[WindowsHotkey] Callback failed for {event.value}: {exc}")

    def _callback_loop(self) -> None:
        while True:
            event = self._callback_queue.get()
            if event is None:
                return
            self._dispatch(event)

    def _start_callback_worker(self) -> None:
        with self._worker_lock:
            if self._callback_thread is not None and self._callback_thread.is_alive():
                return
            self._callback_thread = threading.Thread(
                target=self._callback_loop,
                name="vocal-more-windows-hotkey-callbacks",
                daemon=True,
            )
            self._callback_thread.start()

    def _stop_callback_worker(self) -> None:
        with self._worker_lock:
            thread = self._callback_thread
            if thread is None:
                return
            if thread.is_alive():
                self._callback_queue.put(None)
                thread.join(timeout=1.0)
            self._callback_thread = None

    def _load_keyboard_module(self):
        if self._keyboard_module is None:
            from pynput import keyboard

            self._keyboard_module = keyboard
        return self._keyboard_module

    def start(self) -> bool:
        """Start the pynput listener and return whether its worker is alive."""
        listener = self._listener
        if listener is not None and listener.is_alive():
            return True

        try:
            keyboard = self._load_keyboard_module()
            listener = keyboard.Listener(
                on_press=self._on_press,
                on_release=self._on_release,
                suppress=False,
            )
            self._listener = listener
            self._start_callback_worker()
            listener.start()
        except Exception as exc:
            self._listener = None
            self._running = False
            self._stop_callback_worker()
            print(f"[WindowsHotkey] Failed to start listener: {exc}")
            return False

        # Listener startup is normally immediate, but allow one scheduling turn
        # so an initialization failure is visible to the caller.
        time.sleep(0.05)
        self._running = bool(listener.is_alive())
        if not self._running:
            self._stop_callback_worker()
        return self._running

    def stop(self) -> None:
        listener = self._listener
        self._listener = None
        self._running = False
        if listener is not None:
            try:
                listener.stop()
            except Exception as exc:
                print(f"[WindowsHotkey] Listener stop failed: {exc}")
            if listener is not threading.current_thread():
                try:
                    listener.join(timeout=1.0)
                except Exception:
                    pass
        with self._state_lock:
            self._pressed_tokens.clear()
            self._escape_down = False
        self._stop_callback_worker()

    def set_active_hotkeys(self, hotkeys: list[str]) -> None:
        self._active_hotkeys = list(hotkeys)
        self._rebuild_trigger_tokens()

    def set_custom_key(self, custom_key: Optional[dict]) -> None:
        self._custom_keys = [custom_key] if custom_key is not None else []
        self._rebuild_trigger_tokens()

    def set_custom_keys(self, custom_keys: list[dict]) -> None:
        self._custom_keys = list(custom_keys)
        self._rebuild_trigger_tokens()

    def is_fn_pressed(self) -> bool:
        with self._state_lock:
            return bool(self._pressed_tokens)


__all__ = [
    "DEFAULT_WINDOWS_HOTKEY_LABEL",
    "DEFAULT_WINDOWS_HOTKEY_TOKEN",
    "HotkeyEvent",
    "WindowsHotkeyManager",
    "token_for_browser_code",
    "token_for_pynput_key",
]
