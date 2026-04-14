"""Keyboard simulation module using pynput and pyperclip."""

import time
from typing import Optional

import pyperclip
from pynput.keyboard import Controller, Key


class KeyboardSimulator:
    """Keyboard simulator for typing text and pasting from clipboard."""

    def __init__(self):
        """Initialize the keyboard simulator."""
        self._keyboard = Controller()

    def type_text(self, text: str, delay: float = 0.01) -> None:
        """Type text character by character.

        Args:
            text: Text to type
            delay: Delay between characters in seconds
        """
        for char in text:
            self._keyboard.type(char)
            if delay > 0:
                time.sleep(delay)

    def paste_text(self, text: str) -> None:
        """Paste text using clipboard and Cmd+V.

        Args:
            text: Text to paste
        """
        # Save current clipboard content
        try:
            original_clipboard = pyperclip.paste()
        except Exception:
            original_clipboard = None

        # Copy text to clipboard
        pyperclip.copy(text)

        # Small delay to ensure clipboard is updated
        time.sleep(0.05)

        # Paste using Cmd+V
        with self._keyboard.pressed(Key.cmd):
            self._keyboard.press("v")
            self._keyboard.release("v")

        # Small delay after paste
        time.sleep(0.05)

        # Restore original clipboard content (optional)
        # Commented out to avoid interfering with user's next paste
        # if original_clipboard is not None:
        #     pyperclip.copy(original_clipboard)

    def delete_chars(self, count: int, delay: float = 0.01) -> None:
        """Delete characters using backspace.

        Args:
            count: Number of characters to delete
            delay: Delay between deletions in seconds
        """
        for _ in range(count):
            self._keyboard.press(Key.backspace)
            self._keyboard.release(Key.backspace)
            if delay > 0:
                time.sleep(delay)

    def select_all_and_replace(self, text: str) -> None:
        """Select all text in current field and replace with new text.

        Args:
            text: Text to replace with
        """
        # Select all with Cmd+A
        with self._keyboard.pressed(Key.cmd):
            self._keyboard.press("a")
            self._keyboard.release("a")

        time.sleep(0.05)

        # Paste new text
        self.paste_text(text)

    def replace_last_n_chars(self, n: int, new_text: str) -> None:
        """Replace the last n characters with new text.

        Args:
            n: Number of characters to replace
            new_text: Text to replace with
        """
        # Select last n characters using Shift+Left
        for _ in range(n):
            with self._keyboard.pressed(Key.shift):
                self._keyboard.press(Key.left)
                self._keyboard.release(Key.left)

        time.sleep(0.02)

        # Paste new text (replaces selection)
        self.paste_text(new_text)


if __name__ == "__main__":
    import time

    print("Testing KeyboardSimulator...")
    print("You have 3 seconds to focus a text field...")
    time.sleep(3)

    sim = KeyboardSimulator()

    print("Pasting text...")
    sim.paste_text("Hello, this is a test from Vocal-More!")

    print("Done!")
