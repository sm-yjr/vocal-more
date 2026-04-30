"""py2app build configuration for the local Vocal More macOS app."""

from pathlib import Path
import sys

from setuptools import setup

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"

sys.path.insert(0, str(SRC))

from vocal_more import __version__  # noqa: E402

APP = [
    {
        "script": str(ROOT / "packaging" / "macos" / "vocal_more_launcher.py"),
        "plist": {
            "CFBundleName": "Vocal More",
            "CFBundleDisplayName": "Vocal More",
            "CFBundleIdentifier": "com.sm-yjr.vocal-more",
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "NSHighResolutionCapable": True,
            "NSMicrophoneUsageDescription": (
                "Vocal More needs microphone access to convert your voice to text."
            ),
            "NSAppleEventsUsageDescription": (
                "Vocal More may use macOS automation to paste recognized text into "
                "the active app."
            ),
        },
    }
]

OPTIONS = {
    "argv_emulation": False,
    "iconfile": str(ROOT / "packaging" / "macos" / "VocalMore.icns"),
    "packages": [
        "vocal_more",
        "dashscope",
        "_sounddevice_data",
        "certifi",
        "charset_normalizer",
        "numpy",
        "openai",
        "pynput",
        "pyperclip",
        "rumps",
        "sounddevice",
        "yaml",
    ],
    "includes": [
        "AppKit",
        "Foundation",
        "Quartz",
        "WebKit",
    ],
    "resources": [
        str(ROOT / "assets"),
        str(ROOT / "resources"),
    ],
}

setup(
    app=APP,
    name="Vocal More",
    version=__version__,
    install_requires=[],
    options={"py2app": OPTIONS},
)
