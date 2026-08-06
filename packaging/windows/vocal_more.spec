# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller folder build for the Windows notification-area host."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all


ROOT = Path(SPECPATH).resolve().parents[1]
SRC = ROOT / "src"
LAUNCHER = ROOT / "packaging" / "windows" / "vocal_more_launcher.py"

binaries = []
datas = []
hiddenimports = [
    "vocal_more.windows_app",
    "vocal_more.windows_rpc_handler",
    "vocal_more.infrastructure.windows_app_context",
    "pynput.keyboard._win32",
]

# These packages use runtime discovery or ship non-Python assets. Keep the
# collection list narrow so the Windows artifact does not pull in macOS UI code.
for package in ("dashscope", "openai", "sounddevice", "_sounddevice_data"):
    try:
        package_datas, package_binaries, package_hidden = collect_all(package)
    except Exception:
        continue
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

analysis = Analysis(
    [str(LAUNCHER)],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "AppKit",
        "AVFoundation",
        "Foundation",
        "objc",
        "Quartz",
        "rumps",
        "WebKit",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [("X utf8", None, "OPTION")],
    exclude_binaries=True,
    name="Vocal More",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Vocal More",
)
