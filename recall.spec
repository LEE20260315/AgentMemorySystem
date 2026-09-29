# -*- mode: python ; coding: utf-8 -*-
# onedir build: `dist\recall\recall.exe` + `_internal\*`.
# Rationale (2026-09-16): onefile 每次运行都要在 %TEMP% 新建解压目录，在
# OneDrive 路径 + 计划任务受限环境下实测必挂（"Could not create temporary
# directory" / LastResult=-1）。onedir 零解压、从自身目录直接加载，彻底绕开。


a = Analysis(
    ['recall_launcher.py'],
    pathex=['.'],
    binaries=[],
    datas=[('recall/eval/golden_pairs.yaml', 'recall/eval')],
    hiddenimports=['recall.watch', 'recall.runner', 'recall.ingest', 'recall.logs',
                   'recall.telemetry', 'recall.autopilot', 'recall.eval._common',
                   'recall.eval.recall_eval', 'recall.eval.build_golden'],
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
    name='recall',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
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
    name='recall',
)