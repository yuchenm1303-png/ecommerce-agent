# -*- mode: python ; coding: utf-8 -*-
#
# Builds the standalone updater.exe as a true single-file windowed executable
# with its Python runtime embedded. build_windows.ps1 verifies the executable
# before copying it into the packaged app at updater/updater.exe.

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent

updater_a = Analysis(
    [str(ROOT / "scripts" / "updater_main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6", "playwright", "PIL", "pytest", "numpy", "pandas"],
    noarchive=False,
    optimize=0,
)

updater_pyz = PYZ(updater_a.pure)

updater_exe = EXE(
    updater_pyz,
    updater_a.scripts,
    updater_a.binaries,
    updater_a.datas,
    [("X utf8", None, "OPTION")],
    [],
    exclude_binaries=False,
    name="updater",
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
