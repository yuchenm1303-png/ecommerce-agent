# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).resolve().parent

playwright_datas, playwright_binaries, playwright_hiddenimports = collect_all("playwright")

gui_datas = [
    (str(ROOT / "gui" / "assets"), "gui/assets"),
    (str(ROOT / "THIRD_PARTY_NOTICES.md"), "."),
]

gui_a = Analysis(
    [str(ROOT / "run_local_gui.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=gui_datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest"],
    noarchive=False,
    optimize=0,
)

worker_a = Analysis(
    [str(ROOT / "run_packaged_worker.py")],
    pathex=[str(ROOT)],
    binaries=playwright_binaries,
    datas=playwright_datas,
    hiddenimports=playwright_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6", "pytest"],
    noarchive=False,
    optimize=0,
)

utf8_options = [("X utf8", None, "OPTION")]
worker_options = [("X utf8", None, "OPTION"), ("u", None, "OPTION")]

gui_pyz = PYZ(gui_a.pure)
gui_exe = EXE(
    gui_pyz,
    gui_a.scripts,
    utf8_options,
    [],
    exclude_binaries=True,
    name="EcommerceAgent",
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
    contents_directory="_internal",
)

worker_pyz = PYZ(worker_a.pure)
worker_exe = EXE(
    worker_pyz,
    worker_a.scripts,
    worker_options,
    [],
    exclude_binaries=True,
    name="EcommerceAgentWorker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    contents_directory="_internal",
)

coll = COLLECT(
    gui_exe,
    worker_exe,
    gui_a.binaries,
    gui_a.datas,
    worker_a.binaries,
    worker_a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="EcommerceAgent",
)
