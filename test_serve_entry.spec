# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['/tmp/test_serve_entry.py'],
    pathex=['src'],
    binaries=[],
    datas=[],
    hiddenimports=['vocal_more', 'vocal_more.serve', 'vocal_more.rpc_handler', 'vocal_more.config', 'vocal_more.dictionary', 'vocal_more.core', 'vocal_more.core.audio_recorder', 'vocal_more.core.asr_engine', 'vocal_more.core.text_polisher', 'vocal_more.core.keyboard_sim', 'vocal_more.core.hotkey_manager', 'vocal_more.modes', 'vocal_more.modes.base_mode', 'vocal_more.modes.walkie_talkie', 'vocal_more.modes.realtime_long', 'sounddevice', '_sounddevice_data', 'Quartz', 'Quartz.CoreGraphics', 'AppKit', 'Foundation', 'objc', 'dashscope', 'dashscope.audio', 'dashscope.audio.asr', 'websocket'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='test_serve_entry',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='test_serve_entry',
)
