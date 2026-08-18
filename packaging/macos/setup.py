"""py2app build configuration for the local Vocal More macOS app."""

from pathlib import Path
import sys
import os

from setuptools import setup

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"

sys.path.insert(0, str(SRC))

from vocal_more import __version__  # noqa: E402

BUILD_NUMBER = os.environ.get("VOCAL_MORE_BUILD_NUMBER", __version__)

APP = [
    {
        "script": str(ROOT / "packaging" / "macos" / "vocal_more_launcher.py"),
        "plist": {
            "CFBundleName": "Vocal More",
            "CFBundleDisplayName": "Vocal More",
            "CFBundleIdentifier": "com.sm-yjr.vocal-more",
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": BUILD_NUMBER,
            "SUFeedURL": (
                "https://github.com/sm-yjr/vocal-more/releases/download/"
                "sparkle-feed/appcast.xml"
            ),
            "SUPublicEDKey": "rX4Sp1huP0v763afpuPlVkpDuXYoMj/+2fNqnFFMHsk=",
            "SUVerifyUpdateBeforeExtraction": True,
            "SURequireSignedFeed": True,
            "NSHighResolutionCapable": True,
            "LSUIElement": True,
            # The official arm64 bundle's Python runtime uses macOS 14 as its
            # deployment floor. Keep Info.plist honest rather than allowing a
            # partial launch on an older system.
            "LSMinimumSystemVersion": "14.0",
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
        "pynput",
        "pyperclip",
        "rumps",
        "sounddevice",
        "yaml",
    ],
    "includes": [
        "ApplicationServices",
        "AppKit",
        "AVFoundation",
        "CoreAudio",
        "CoreFoundation",
        "CoreMedia",
        "Foundation",
        "Quartz",
        "WebKit",
    ],
    "excludes": [
        "_pytest",
        "pytest",
        "test",
        "_tkinter",
        "tkinter",
        "idlelib",
        "turtle",
        "openai",
        "vocal_more.audio_quality_benchmark",
        "vocal_more.core.macos_voice_capture",
    ],
    "resources": [
        (
            "assets",
            [str(ROOT / "packaging" / "macos" / ".VocalMore.runtime-logo.png")],
        ),
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
