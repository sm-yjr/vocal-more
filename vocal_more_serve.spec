# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for vocal-more backend (onedir mode)."""

import os
from PyInstaller.utils.hooks import collect_all

# Absolute paths based on spec file location
src_dir = os.path.join(SPECPATH, 'src')
venv_sp = os.path.join(SPECPATH, '.venv', 'lib', 'python3.12', 'site-packages')

# Collect ALL of dashscope (it uses heavy dynamic imports)
ds_datas, ds_binaries, ds_hiddenimports = collect_all('dashscope')

a = Analysis(
    [os.path.join(SPECPATH, 'scripts', 'pyinstaller_entry.py')],
    pathex=[src_dir],
    binaries=ds_binaries,
    datas=[
        # Include vocal_more source package explicitly
        (os.path.join(src_dir, 'vocal_more'), 'vocal_more'),
        # sounddevice bundles its own libportaudio
        (os.path.join(venv_sp, '_sounddevice_data'), '_sounddevice_data'),
    ] + ds_datas,
    hiddenimports=[
        # vocal_more package
        'vocal_more',
        'vocal_more.serve',
        'vocal_more.rpc_handler',
        'vocal_more.config',
        'vocal_more.dictionary',
        'vocal_more.core',
        'vocal_more.core.audio_recorder',
        'vocal_more.core.asr_engine',
        'vocal_more.core.text_polisher',
        'vocal_more.core.keyboard_sim',
        'vocal_more.core.hotkey_manager',
        'vocal_more.modes',
        'vocal_more.modes.base_mode',
        'vocal_more.modes.walkie_talkie',
        'vocal_more.modes.realtime_long',
        # Audio
        'sounddevice',
        '_sounddevice_data',
        # PyObjC
        'Quartz',
        'Quartz.CoreGraphics',
        'AppKit',
        'Foundation',
        'objc',
        # Clipboard + keyboard
        'pyperclip',
        'pynput',
        'pynput.keyboard',
        'pynput.keyboard._darwin',
        # WebSocket (dashscope dependency)
        'websocket',
    ] + ds_hiddenimports,
    excludes=[
        # Not needed in serve mode
        'rumps',
        'WebKit',
        'JavaScriptCore',
        'vocal_more.app',
        'vocal_more.ui',
        'vocal_more.ui.floating_capsule',
        # Dev/test
        'pytest',
        'Pygments',
        # Unnecessary stdlib
        'tkinter',
        'unittest',
        'xmlrpc',
        'pydoc',
        'doctest',
        # Heavy unused
        'matplotlib',
        'PIL',
        'IPython',
        'notebook',
        'sphinx',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='vocal-more-backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,  # Don't strip — can break pyobjc .so files
    upx=False,    # UPX causes issues on macOS
    console=True,
    target_arch='arm64',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='vocal-more-backend',
)
