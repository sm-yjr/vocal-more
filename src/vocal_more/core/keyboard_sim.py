"""Cross-platform keyboard simulation with an optional native macOS fast path."""

from __future__ import annotations

import sys
import time

import pyperclip
from pynput.keyboard import Controller, Key


class KeyboardSimulator:
    """Type, select, and paste text into the currently focused application."""

    def __init__(
        self,
        *,
        platform_name: str | None = None,
        keyboard: object | None = None,
        clipboard=None,
        native_fast_paste: bool | None = None,
        native_paster=None,
    ) -> None:
        self._keyboard = keyboard if keyboard is not None else Controller()
        self._clipboard = clipboard if clipboard is not None else pyperclip
        current_platform = (platform_name or sys.platform).casefold()
        self._platform_name = current_platform
        self._native_fast_paste = native_fast_paste
        self._native_paster = native_paster
        if current_platform == "darwin":
            self._shortcut_modifier = Key.cmd
        else:
            # Test doubles and unusually old pynput builds may not expose ctrl.
            # Falling back to cmd keeps construction safe; supported Windows
            # builds use Key.ctrl here.
            self._shortcut_modifier = getattr(Key, "ctrl", Key.cmd)

    def type_text(self, text: str, delay: float = 0.01) -> None:
        """Type text character by character."""
        for char in text:
            self._keyboard.type(char)
            if delay > 0:
                time.sleep(delay)

    def paste_text(self, text: str) -> None:
        """Copy text to the clipboard and send the platform paste shortcut."""
        if self._should_use_native_fast_paste():
            try:
                if self._native_paste_text(text):
                    return
            except Exception as exc:  # noqa: BLE001 - compatibility fallback boundary
                print(
                    "[KeyboardSimulator] Native fast paste failed; "
                    f"using compatibility path: {exc}"
                )

        self._paste_text_compatible(text)

    def _should_use_native_fast_paste(self) -> bool:
        if self._platform_name != "darwin":
            return False
        if self._native_fast_paste is not None:
            return bool(self._native_fast_paste)
        try:
            from ..config import get_config

            return bool(get_config().native_fast_paste)
        except (AttributeError, ImportError):
            return False

    def _native_paste_text(self, text: str) -> bool:
        if self._native_paster is None:
            from .macos_native_paste import MacOSNativePaste

            self._native_paster = MacOSNativePaste()
        return bool(self._native_paster.paste_text(text))

    def _paste_text_compatible(self, text: str) -> None:
        try:
            self._clipboard.paste()
        except Exception:  # noqa: BLE001,S110 - optional clipboard probe
            pass

        self._clipboard.copy(text)
        time.sleep(0.05)

        with self._keyboard.pressed(self._shortcut_modifier):
            self._keyboard.press("v")
            self._keyboard.release("v")

        time.sleep(0.05)

    def delete_chars(self, count: int, delay: float = 0.01) -> None:
        """Delete characters using Backspace."""
        for _ in range(count):
            self._keyboard.press(Key.backspace)
            self._keyboard.release(Key.backspace)
            if delay > 0:
                time.sleep(delay)

    def select_all_and_replace(self, text: str) -> None:
        """Select all text in the current field and replace it."""
        with self._keyboard.pressed(self._shortcut_modifier):
            self._keyboard.press("a")
            self._keyboard.release("a")

        time.sleep(0.05)
        self.paste_text(text)

    def replace_last_n_chars(self, n: int, new_text: str) -> None:
        """Replace the last ``n`` characters with new text."""
        for _ in range(n):
            with self._keyboard.pressed(Key.shift):
                self._keyboard.press(Key.left)
                self._keyboard.release(Key.left)

        time.sleep(0.02)
        self.paste_text(new_text)


if __name__ == "__main__":
    print("Testing KeyboardSimulator...")
    print("You have 3 seconds to focus a text field...")
    time.sleep(3)
    KeyboardSimulator().paste_text("Hello, this is a test from Vocal-More!")
    print("Done!")
