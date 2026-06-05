"""Hotkey manager using CGEventTap for Fn key detection."""

import queue
import threading
import time
from enum import Enum
from typing import Callable, Optional

from Quartz import (
    CFMachPortCreateRunLoopSource,
    CFRunLoopAddSource,
    CFRunLoopGetCurrent,
    CFRunLoopRun,
    CFRunLoopStop,
    CGEventGetFlags,
    CGEventGetIntegerValueField,
    CGEventMaskBit,
    CGEventTapCreate,
    CGEventTapEnable,
    kCFRunLoopCommonModes,
    kCGEventFlagsChanged,
    kCGEventKeyDown,
    kCGEventKeyUp,
    kCGEventTapOptionDefault,
    kCGHeadInsertEventTap,
    kCGKeyboardEventKeycode,
    kCGSessionEventTap,
)

from ..config import get_config
from ..domain.hotkey_catalog import BUILT_IN_HOTKEYS, NX_COMMANDMASK, NX_SECONDARYFNMASK


# Fn key constants
FN_KEYCODE = 63

# Command key constants for double-tap
CMD_LEFT_KEYCODE = 55
CMD_RIGHT_KEYCODE = 54
ESC_KEYCODE = 53

# Key registry: name → (keycode, is_modifier, flag_mask)
# Modifier keys are detected via kCGEventFlagsChanged (press state from flags).
# Regular keys are detected via kCGEventKeyDown / kCGEventKeyUp.
KEY_REGISTRY: dict[str, tuple[int, bool, int]] = {
    name: (definition.key_code, definition.is_modifier, definition.flag_mask)
    for name, definition in BUILT_IN_HOTKEYS.items()
}


class HotkeyEvent(Enum):
    """Hotkey event types."""

    FN_PRESSED = "fn_pressed"
    FN_RELEASED = "fn_released"
    DOUBLE_CMD = "double_cmd"
    ESC_PRESSED = "esc_pressed"


class HotkeyManager:
    """Hotkey manager using CGEventTap for global hotkey detection."""

    def __init__(
        self,
        on_fn_pressed: Optional[Callable[[], None]] = None,
        on_fn_released: Optional[Callable[[], None]] = None,
        on_double_cmd: Optional[Callable[[], None]] = None,
        on_escape_pressed: Optional[Callable[[], None]] = None,
    ):
        """Initialize the hotkey manager.

        Args:
            on_fn_pressed: Callback when Fn key is pressed
            on_fn_released: Callback when Fn key is released
            on_double_cmd: Callback when Cmd key is double-tapped
            on_escape_pressed: Callback when Escape is pressed
        """
        self.config = get_config()
        self.on_fn_pressed = on_fn_pressed
        self.on_fn_released = on_fn_released
        self.on_double_cmd = on_double_cmd
        self.on_escape_pressed = on_escape_pressed

        self._active_hotkeys: list[str] = list(self.config.hotkey.active_hotkeys)
        self._custom_key: Optional[dict] = self.config.hotkey.custom_key

        self._tap = None
        self._run_loop_source = None
        self._thread: Optional[threading.Thread] = None
        self._callback_thread: Optional[threading.Thread] = None
        self._callback_queue: queue.Queue[Optional[HotkeyEvent]] = queue.Queue()
        self._running = False
        self._run_loop = None

        # State tracking
        self._key_states: dict[int, bool] = {}  # keycode → pressed
        self._held_keys: set[int] = set()  # regular keys currently held
        self._last_cmd_time = 0.0
        self._cmd_tap_count = 0

        # Pre-compute lookup tables
        self._modifier_lookup: dict[int, int] = {}  # keycode → flag_mask
        self._regular_lookup: set[int] = set()  # keycodes
        self._double_cmd_active = False
        self._update_lookup_tables()

    def _update_lookup_tables(self) -> None:
        """Rebuild fast-lookup tables from active_hotkeys."""
        self._modifier_lookup = {}
        self._regular_lookup = set()
        self._double_cmd_active = False

        for name in self._active_hotkeys:
            if name == "double_cmd":
                self._double_cmd_active = True
            elif name in KEY_REGISTRY:
                keycode, is_mod, flag = KEY_REGISTRY[name]
                if is_mod:
                    self._modifier_lookup[keycode] = flag
                else:
                    self._regular_lookup.add(keycode)

        if self._custom_key:
            keycode = self._custom_key["key_code"]
            if self._custom_key["is_modifier"]:
                self._modifier_lookup[keycode] = self._custom_key["flag_mask"]
            else:
                self._regular_lookup.add(keycode)

        self._key_states = {
            keycode: pressed
            for keycode, pressed in self._key_states.items()
            if keycode in self._modifier_lookup
        }
        self._held_keys.intersection_update(self._regular_lookup)

    def _event_callback(self, proxy, event_type, event, refcon):
        """Callback for CGEventTap events.

        Returns None to consume the event (prevent propagation to focused app),
        or returns the event to let it pass through.
        """
        keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)

        if event_type == kCGEventFlagsChanged:
            flags = CGEventGetFlags(event)

            # Handle modifier keys (fn, etc.)
            if keycode in self._modifier_lookup:
                flag_mask = self._modifier_lookup[keycode]
                pressed = bool(flags & flag_mask)
                prev = self._key_states.get(keycode, False)

                if pressed and not prev:
                    self._key_states[keycode] = True
                    if self.on_fn_pressed:
                        self._enqueue_event(HotkeyEvent.FN_PRESSED)
                elif not pressed and prev:
                    self._key_states[keycode] = False
                    if self.on_fn_released:
                        self._enqueue_event(HotkeyEvent.FN_RELEASED)

                # Consume modifier hotkey events so they don't reach the focused app
                return None

            # Handle Cmd double-tap (do NOT consume — Cmd is needed for normal use)
            if self._double_cmd_active and keycode in (CMD_LEFT_KEYCODE, CMD_RIGHT_KEYCODE):
                cmd_pressed = bool(flags & NX_COMMANDMASK)

                if not cmd_pressed:  # Key released
                    current_time = time.time()
                    threshold = self.config.hotkey.double_tap_threshold

                    if current_time - self._last_cmd_time < threshold:
                        self._cmd_tap_count += 1
                        if self._cmd_tap_count >= 2:
                            self._cmd_tap_count = 0
                            if self.on_double_cmd:
                                self._enqueue_event(HotkeyEvent.DOUBLE_CMD)
                    else:
                        self._cmd_tap_count = 1

                    self._last_cmd_time = current_time

        elif event_type == kCGEventKeyDown:
            # Handle regular keys (F13-F20, etc.) — ignore key repeat
            if keycode in self._regular_lookup:
                if keycode not in self._held_keys:
                    self._held_keys.add(keycode)
                    if self.on_fn_pressed:
                        self._enqueue_event(HotkeyEvent.FN_PRESSED)
                # Consume both initial press and repeats
                return None

            if keycode == ESC_KEYCODE and self.on_escape_pressed:
                self._enqueue_event(HotkeyEvent.ESC_PRESSED)
                return event

        elif event_type == kCGEventKeyUp:
            if keycode in self._regular_lookup:
                if keycode in self._held_keys:
                    self._held_keys.discard(keycode)
                    if self.on_fn_released:
                        self._enqueue_event(HotkeyEvent.FN_RELEASED)
                # Consume key-up too
                return None

        return event

    def _enqueue_event(self, event: HotkeyEvent) -> None:
        """Queue hotkey events onto a single serial worker."""
        self._start_callback_worker()
        self._callback_queue.put(event)

    def _dispatch_event(self, event: HotkeyEvent) -> None:
        """Invoke hotkey callbacks in enqueue order on the callback worker."""
        callback: Optional[Callable[[], None]] = None
        if event == HotkeyEvent.FN_PRESSED:
            callback = self.on_fn_pressed
        elif event == HotkeyEvent.FN_RELEASED:
            callback = self.on_fn_released
        elif event == HotkeyEvent.DOUBLE_CMD:
            callback = self.on_double_cmd
        elif event == HotkeyEvent.ESC_PRESSED:
            callback = self.on_escape_pressed

        if callback is None:
            return

        try:
            callback()
        except Exception as exc:
            print(f"[HotkeyManager] Callback failed for {event.value}: {exc}")

    def _run_callback_loop(self) -> None:
        """Process queued hotkey events serially."""
        while True:
            event = self._callback_queue.get()
            if event is None:
                break
            self._dispatch_event(event)

    def _start_callback_worker(self) -> None:
        """Start the serial hotkey callback worker if needed."""
        if self._callback_thread and self._callback_thread.is_alive():
            return

        self._callback_thread = threading.Thread(
            target=self._run_callback_loop,
            daemon=True,
        )
        self._callback_thread.start()

    def _stop_callback_worker(self) -> None:
        """Stop the serial hotkey callback worker."""
        if not self._callback_thread:
            return

        if self._callback_thread.is_alive():
            self._callback_queue.put(None)
            self._callback_thread.join(timeout=1.0)

        self._callback_thread = None

    def _run_event_loop(self) -> None:
        """Run the event tap run loop."""
        # Create callback wrapper that can be called from C
        def callback_wrapper(proxy, event_type, event, refcon):
            return self._event_callback(proxy, event_type, event, refcon)

        # Create event tap
        event_mask = (
            CGEventMaskBit(kCGEventFlagsChanged)
            | CGEventMaskBit(kCGEventKeyDown)
            | CGEventMaskBit(kCGEventKeyUp)
        )
        self._tap = CGEventTapCreate(
            kCGSessionEventTap,
            kCGHeadInsertEventTap,
            kCGEventTapOptionDefault,
            event_mask,
            callback_wrapper,
            None,
        )

        if self._tap is None:
            self._running = False
            self._run_loop = None
            self._run_loop_source = None
            print(
                "Failed to create event tap. "
                "Make sure Accessibility permissions are granted."
            )
            return

        # Create run loop source
        self._run_loop_source = CFMachPortCreateRunLoopSource(None, self._tap, 0)

        # Add to run loop
        self._run_loop = CFRunLoopGetCurrent()
        CFRunLoopAddSource(self._run_loop, self._run_loop_source, kCFRunLoopCommonModes)

        # Enable the tap
        CGEventTapEnable(self._tap, True)

        # Run the loop
        self._running = True
        CFRunLoopRun()

    def start(self) -> bool:
        """Start listening for hotkeys.

        Returns:
            True if started successfully
        """
        if self._thread and self._thread.is_alive():
            return True

        self._start_callback_worker()
        self._thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self._thread.start()

        # Give it a moment to start
        time.sleep(0.1)

        return self._running

    def stop(self) -> None:
        """Stop listening for hotkeys."""
        self._running = False

        if self._run_loop:
            CFRunLoopStop(self._run_loop)

        if self._tap:
            CGEventTapEnable(self._tap, False)

        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

        self._stop_callback_worker()
        self._tap = None
        self._run_loop = None
        self._run_loop_source = None

    def set_active_hotkeys(self, hotkeys: list[str]) -> None:
        """Update which hotkeys are active at runtime. No restart needed."""
        self._active_hotkeys = list(hotkeys)
        self._update_lookup_tables()

    def set_custom_key(self, custom_key: Optional[dict]) -> None:
        """Update the custom hotkey at runtime. No restart needed."""
        self._custom_key = custom_key
        self._update_lookup_tables()

    def is_fn_pressed(self) -> bool:
        """Check if any hold-type hotkey is currently pressed."""
        if self._key_states.get(FN_KEYCODE, False):
            return True
        return bool(self._held_keys)


if __name__ == "__main__":
    print("Testing HotkeyManager...")
    print("Press Fn key or double-tap Cmd key...")
    print("Press Ctrl+C to exit")

    def on_fn_pressed():
        print("Fn PRESSED")

    def on_fn_released():
        print("Fn RELEASED")

    def on_double_cmd():
        print("Double CMD detected!")

    manager = HotkeyManager(
        on_fn_pressed=on_fn_pressed,
        on_fn_released=on_fn_released,
        on_double_cmd=on_double_cmd,
    )

    if manager.start():
        print("Hotkey manager started successfully")
        try:
            while True:
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("\nStopping...")
            manager.stop()
    else:
        print("Failed to start hotkey manager")
        print("Please grant Accessibility permissions in System Settings")
