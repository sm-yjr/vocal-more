"""macOS keyboard key definitions used for customizable hotkeys."""

from __future__ import annotations

from dataclasses import dataclass


NX_ALPHASHIFTMASK = 0x010000
NX_SHIFTMASK = 0x020000
NX_CONTROLMASK = 0x040000
NX_ALTERNATEMASK = 0x080000
NX_COMMANDMASK = 0x100000
NX_SECONDARYFNMASK = 0x800000


@dataclass(frozen=True)
class HotkeyKeyDefinition:
    """A physical macOS keyboard key that can be used as a hold/toggle hotkey."""

    key_code: int
    display_name: str
    is_modifier: bool = False
    flag_mask: int = 0
    browser_code: str = ""

    def to_config(self) -> dict:
        return {
            "key_code": self.key_code,
            "display_name": self.display_name,
            "is_modifier": self.is_modifier,
            "flag_mask": self.flag_mask,
        }


CUSTOM_MODIFIER_KEYS: tuple[HotkeyKeyDefinition, ...] = (
    HotkeyKeyDefinition(55, "Left Command", True, NX_COMMANDMASK, "MetaLeft"),
    HotkeyKeyDefinition(54, "Right Command", True, NX_COMMANDMASK, "MetaRight"),
    HotkeyKeyDefinition(56, "Left Shift", True, NX_SHIFTMASK, "ShiftLeft"),
    HotkeyKeyDefinition(60, "Right Shift", True, NX_SHIFTMASK, "ShiftRight"),
    HotkeyKeyDefinition(58, "Left Option", True, NX_ALTERNATEMASK, "AltLeft"),
    HotkeyKeyDefinition(61, "Right Option", True, NX_ALTERNATEMASK, "AltRight"),
    HotkeyKeyDefinition(59, "Left Control", True, NX_CONTROLMASK, "ControlLeft"),
    HotkeyKeyDefinition(62, "Right Control", True, NX_CONTROLMASK, "ControlRight"),
    HotkeyKeyDefinition(57, "Caps Lock", True, NX_ALPHASHIFTMASK, "CapsLock"),
)


CUSTOM_REGULAR_KEYS: tuple[HotkeyKeyDefinition, ...] = (
    HotkeyKeyDefinition(0, "A", browser_code="KeyA"),
    HotkeyKeyDefinition(1, "S", browser_code="KeyS"),
    HotkeyKeyDefinition(2, "D", browser_code="KeyD"),
    HotkeyKeyDefinition(3, "F", browser_code="KeyF"),
    HotkeyKeyDefinition(4, "H", browser_code="KeyH"),
    HotkeyKeyDefinition(5, "G", browser_code="KeyG"),
    HotkeyKeyDefinition(6, "Z", browser_code="KeyZ"),
    HotkeyKeyDefinition(7, "X", browser_code="KeyX"),
    HotkeyKeyDefinition(8, "C", browser_code="KeyC"),
    HotkeyKeyDefinition(9, "V", browser_code="KeyV"),
    HotkeyKeyDefinition(10, "Section", browser_code="IntlBackslash"),
    HotkeyKeyDefinition(11, "B", browser_code="KeyB"),
    HotkeyKeyDefinition(12, "Q", browser_code="KeyQ"),
    HotkeyKeyDefinition(13, "W", browser_code="KeyW"),
    HotkeyKeyDefinition(14, "E", browser_code="KeyE"),
    HotkeyKeyDefinition(15, "R", browser_code="KeyR"),
    HotkeyKeyDefinition(16, "Y", browser_code="KeyY"),
    HotkeyKeyDefinition(17, "T", browser_code="KeyT"),
    HotkeyKeyDefinition(18, "1", browser_code="Digit1"),
    HotkeyKeyDefinition(19, "2", browser_code="Digit2"),
    HotkeyKeyDefinition(20, "3", browser_code="Digit3"),
    HotkeyKeyDefinition(21, "4", browser_code="Digit4"),
    HotkeyKeyDefinition(22, "6", browser_code="Digit6"),
    HotkeyKeyDefinition(23, "5", browser_code="Digit5"),
    HotkeyKeyDefinition(24, "=", browser_code="Equal"),
    HotkeyKeyDefinition(25, "9", browser_code="Digit9"),
    HotkeyKeyDefinition(26, "7", browser_code="Digit7"),
    HotkeyKeyDefinition(27, "-", browser_code="Minus"),
    HotkeyKeyDefinition(28, "8", browser_code="Digit8"),
    HotkeyKeyDefinition(29, "0", browser_code="Digit0"),
    HotkeyKeyDefinition(30, "]", browser_code="BracketRight"),
    HotkeyKeyDefinition(31, "O", browser_code="KeyO"),
    HotkeyKeyDefinition(32, "U", browser_code="KeyU"),
    HotkeyKeyDefinition(33, "[", browser_code="BracketLeft"),
    HotkeyKeyDefinition(34, "I", browser_code="KeyI"),
    HotkeyKeyDefinition(35, "P", browser_code="KeyP"),
    HotkeyKeyDefinition(36, "Return", browser_code="Enter"),
    HotkeyKeyDefinition(37, "L", browser_code="KeyL"),
    HotkeyKeyDefinition(38, "J", browser_code="KeyJ"),
    HotkeyKeyDefinition(39, "'", browser_code="Quote"),
    HotkeyKeyDefinition(40, "K", browser_code="KeyK"),
    HotkeyKeyDefinition(41, ";", browser_code="Semicolon"),
    HotkeyKeyDefinition(42, "\\", browser_code="Backslash"),
    HotkeyKeyDefinition(43, ",", browser_code="Comma"),
    HotkeyKeyDefinition(44, "/", browser_code="Slash"),
    HotkeyKeyDefinition(45, "N", browser_code="KeyN"),
    HotkeyKeyDefinition(46, "M", browser_code="KeyM"),
    HotkeyKeyDefinition(47, ".", browser_code="Period"),
    HotkeyKeyDefinition(48, "Tab", browser_code="Tab"),
    HotkeyKeyDefinition(49, "Space", browser_code="Space"),
    HotkeyKeyDefinition(50, "`", browser_code="Backquote"),
    HotkeyKeyDefinition(51, "Delete", browser_code="Backspace"),
    HotkeyKeyDefinition(53, "Escape", browser_code="Escape"),
    HotkeyKeyDefinition(64, "F17", browser_code="F17"),
    HotkeyKeyDefinition(65, "Numpad .", browser_code="NumpadDecimal"),
    HotkeyKeyDefinition(67, "Numpad *", browser_code="NumpadMultiply"),
    HotkeyKeyDefinition(69, "Numpad +", browser_code="NumpadAdd"),
    HotkeyKeyDefinition(71, "Numpad Clear", browser_code="NumLock"),
    HotkeyKeyDefinition(75, "Numpad /", browser_code="NumpadDivide"),
    HotkeyKeyDefinition(76, "Numpad Enter", browser_code="NumpadEnter"),
    HotkeyKeyDefinition(78, "Numpad -", browser_code="NumpadSubtract"),
    HotkeyKeyDefinition(79, "F18", browser_code="F18"),
    HotkeyKeyDefinition(80, "F19", browser_code="F19"),
    HotkeyKeyDefinition(81, "Numpad =", browser_code="NumpadEqual"),
    HotkeyKeyDefinition(82, "Numpad 0", browser_code="Numpad0"),
    HotkeyKeyDefinition(83, "Numpad 1", browser_code="Numpad1"),
    HotkeyKeyDefinition(84, "Numpad 2", browser_code="Numpad2"),
    HotkeyKeyDefinition(85, "Numpad 3", browser_code="Numpad3"),
    HotkeyKeyDefinition(86, "Numpad 4", browser_code="Numpad4"),
    HotkeyKeyDefinition(87, "Numpad 5", browser_code="Numpad5"),
    HotkeyKeyDefinition(88, "Numpad 6", browser_code="Numpad6"),
    HotkeyKeyDefinition(89, "Numpad 7", browser_code="Numpad7"),
    HotkeyKeyDefinition(90, "F20", browser_code="F20"),
    HotkeyKeyDefinition(91, "Numpad 8", browser_code="Numpad8"),
    HotkeyKeyDefinition(92, "Numpad 9", browser_code="Numpad9"),
    HotkeyKeyDefinition(96, "F5", browser_code="F5"),
    HotkeyKeyDefinition(97, "F6", browser_code="F6"),
    HotkeyKeyDefinition(98, "F7", browser_code="F7"),
    HotkeyKeyDefinition(99, "F3", browser_code="F3"),
    HotkeyKeyDefinition(100, "F8", browser_code="F8"),
    HotkeyKeyDefinition(101, "F9", browser_code="F9"),
    HotkeyKeyDefinition(103, "F11", browser_code="F11"),
    HotkeyKeyDefinition(105, "F13", browser_code="F13"),
    HotkeyKeyDefinition(106, "F16", browser_code="F16"),
    HotkeyKeyDefinition(107, "F14", browser_code="F14"),
    HotkeyKeyDefinition(109, "F10", browser_code="F10"),
    HotkeyKeyDefinition(111, "F12", browser_code="F12"),
    HotkeyKeyDefinition(113, "F15", browser_code="F15"),
    HotkeyKeyDefinition(114, "Help", browser_code="Help"),
    HotkeyKeyDefinition(115, "Home", browser_code="Home"),
    HotkeyKeyDefinition(116, "Page Up", browser_code="PageUp"),
    HotkeyKeyDefinition(117, "Forward Delete", browser_code="Delete"),
    HotkeyKeyDefinition(118, "F4", browser_code="F4"),
    HotkeyKeyDefinition(119, "End", browser_code="End"),
    HotkeyKeyDefinition(120, "F2", browser_code="F2"),
    HotkeyKeyDefinition(121, "Page Down", browser_code="PageDown"),
    HotkeyKeyDefinition(122, "F1", browser_code="F1"),
    HotkeyKeyDefinition(123, "Left Arrow", browser_code="ArrowLeft"),
    HotkeyKeyDefinition(124, "Right Arrow", browser_code="ArrowRight"),
    HotkeyKeyDefinition(125, "Down Arrow", browser_code="ArrowDown"),
    HotkeyKeyDefinition(126, "Up Arrow", browser_code="ArrowUp"),
)


CUSTOM_HOTKEY_KEYS: tuple[HotkeyKeyDefinition, ...] = (
    *CUSTOM_MODIFIER_KEYS,
    *CUSTOM_REGULAR_KEYS,
)

CUSTOM_HOTKEY_KEYS_BY_CODE = {definition.key_code: definition for definition in CUSTOM_HOTKEY_KEYS}
CUSTOM_HOTKEY_KEYS_BY_BROWSER_CODE = {
    definition.browser_code: definition
    for definition in CUSTOM_HOTKEY_KEYS
    if definition.browser_code
}

BUILT_IN_HOTKEYS: dict[str, HotkeyKeyDefinition] = {
    "fn": HotkeyKeyDefinition(63, "Fn", True, NX_SECONDARYFNMASK),
}


def normalize_custom_key(raw: object) -> dict | None:
    """Validate and canonicalize a persisted custom-key definition."""
    if not isinstance(raw, dict):
        return None

    key_code = raw.get("key_code")
    if not isinstance(key_code, int):
        return None

    definition = CUSTOM_HOTKEY_KEYS_BY_CODE.get(key_code)
    if definition is None:
        return None

    is_modifier = raw.get("is_modifier")
    flag_mask = raw.get("flag_mask")
    if is_modifier != definition.is_modifier or flag_mask != definition.flag_mask:
        return None

    return definition.to_config()
