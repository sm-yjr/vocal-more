"""Shared pytest setup for local test imports."""

from pathlib import Path
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _install_sounddevice_stub() -> None:
    if "sounddevice" in sys.modules:
        return

    class _DummyInputStream:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            return None

        def stop(self):
            return None

        def close(self):
            return None

    stub = types.ModuleType("sounddevice")
    stub.InputStream = _DummyInputStream
    stub.PortAudioError = Exception
    stub.CallbackFlags = object
    stub.query_devices = lambda *args, **kwargs: []
    sys.modules["sounddevice"] = stub


def _install_keyboard_stubs() -> None:
    if "pyperclip" not in sys.modules:
        pyperclip = types.ModuleType("pyperclip")
        pyperclip.copy = lambda text: None
        pyperclip.paste = lambda: ""
        sys.modules["pyperclip"] = pyperclip

    if "pynput.keyboard" in sys.modules:
        return

    class _DummyPressed:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _DummyController:
        def type(self, text):
            return None

        def pressed(self, key):
            return _DummyPressed()

        def press(self, key):
            return None

        def release(self, key):
            return None

    class _DummyKey:
        cmd = "cmd"
        backspace = "backspace"
        shift = "shift"
        left = "left"

    keyboard_module = types.ModuleType("pynput.keyboard")
    keyboard_module.Controller = _DummyController
    keyboard_module.Key = _DummyKey

    pynput_module = types.ModuleType("pynput")
    pynput_module.keyboard = keyboard_module

    sys.modules["pynput"] = pynput_module
    sys.modules["pynput.keyboard"] = keyboard_module


def _install_quartz_stub() -> None:
    if "Quartz" in sys.modules:
        return

    quartz = types.ModuleType("Quartz")
    quartz.CFMachPortCreateRunLoopSource = lambda *args, **kwargs: None
    quartz.CFRunLoopAddSource = lambda *args, **kwargs: None
    quartz.CFRunLoopGetCurrent = lambda: None
    quartz.CFRunLoopRun = lambda: None
    quartz.CFRunLoopStop = lambda *args, **kwargs: None
    quartz.CGEventGetFlags = lambda event: 0
    quartz.CGEventGetIntegerValueField = lambda event, field: 0
    quartz.CGEventMaskBit = lambda value: 0
    quartz.CGEventTapCreate = lambda *args, **kwargs: None
    quartz.CGEventTapEnable = lambda *args, **kwargs: None
    quartz.kCFRunLoopCommonModes = 0
    quartz.kCGEventFlagsChanged = 0
    quartz.kCGHIDEventTap = 0
    quartz.kCGEventKeyDown = 0
    quartz.kCGEventKeyUp = 0
    quartz.kCGEventTapOptionDefault = 0
    quartz.kCGHeadInsertEventTap = 0
    quartz.kCGKeyboardEventKeycode = 0
    sys.modules["Quartz"] = quartz


def _install_pyobjc_stubs() -> None:
    if "objc" not in sys.modules:
        objc_module = types.ModuleType("objc")
        objc_module.super = super
        sys.modules["objc"] = objc_module

    if "AppKit" not in sys.modules:
        appkit = types.ModuleType("AppKit")
        appkit.NSApp = None
        appkit.NSApplicationActivationPolicyAccessory = 0
        appkit.NSApplicationActivationPolicyRegular = 1
        appkit.NSBackingStoreBuffered = 0
        appkit.NSColor = type("NSColor", (), {})
        appkit.NSScreen = type("NSScreen", (), {})
        appkit.NSWindow = type("NSWindow", (), {})
        appkit.NSWindowStyleMaskClosable = 0
        appkit.NSWindowStyleMaskMiniaturizable = 0
        appkit.NSWindowStyleMaskResizable = 0
        appkit.NSWindowStyleMaskTitled = 0
        sys.modules["AppKit"] = appkit

    if "Foundation" not in sys.modules:
        foundation = types.ModuleType("Foundation")
        foundation.NSDate = type("NSDate", (), {})
        foundation.NSMakeRect = lambda *args, **kwargs: None
        foundation.NSObject = type("NSObject", (), {})
        foundation.NSRunLoop = type("NSRunLoop", (), {})
        foundation.NSRunLoopCommonModes = 0
        foundation.NSTimer = type("NSTimer", (), {})
        foundation.NSURL = type("NSURL", (), {})
        sys.modules["Foundation"] = foundation

    if "WebKit" not in sys.modules:
        webkit = types.ModuleType("WebKit")
        webkit.WKUserContentController = type("WKUserContentController", (), {})
        webkit.WKUserScript = type("WKUserScript", (), {})
        webkit.WKWebView = type("WKWebView", (), {})
        webkit.WKWebViewConfiguration = type("WKWebViewConfiguration", (), {})
        sys.modules["WebKit"] = webkit


_install_sounddevice_stub()
_install_keyboard_stubs()
_install_quartz_stub()
_install_pyobjc_stubs()


@pytest.fixture(autouse=True)
def _isolate_persisted_config(tmp_path, monkeypatch):
    """Keep tests and compatibility repair away from the user's real config."""
    import vocal_more.config as config_module
    from vocal_more.config import Config

    config_dir = tmp_path / "vocal-more-config"
    monkeypatch.setattr(
        Config,
        "get_config_dir",
        classmethod(lambda cls: config_dir),
    )
    config_module._config = Config()
    yield
    config_module._config = None
