# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from PyInstaller.building.datastruct import TOC
from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).resolve().parent
APP_ICON = ROOT / "packaging" / "app_icon.ico"
APP_ACCESS_SOURCE = ROOT / "gui" / "app_access.py"
APP_ACCESS_MODULE = "gui.app_access"
VELOPACK_RUNTIME_HOOK = ROOT / "packaging" / "velopack_runtime_hook.py"
if not APP_ICON.is_file():
    raise RuntimeError(f"Application icon was not generated: {APP_ICON}")
if not APP_ACCESS_SOURCE.is_file():
    raise RuntimeError(f"Application access source missing: {APP_ACCESS_SOURCE}")
if not VELOPACK_RUNTIME_HOOK.is_file():
    raise RuntimeError(f"Velopack runtime hook missing: {VELOPACK_RUNTIME_HOOK}")

BUILD_VERSION = os.environ.get("ECOMMERCE_AGENT_BUILD_VERSION", "").strip()
if not BUILD_VERSION:
    BUILD_VERSION = (ROOT / "packaging" / "VERSION").read_text(encoding="utf-8").strip()
BUILD_METADATA = ROOT / "build" / "package_metadata"
BUILD_METADATA.mkdir(parents=True, exist_ok=True)
(BUILD_METADATA / "VERSION").write_text(BUILD_VERSION + "\n", encoding="utf-8")

playwright_datas, playwright_binaries, playwright_hiddenimports = collect_all("playwright")
velopack_datas, velopack_binaries, velopack_hiddenimports = collect_all("velopack")

gui_datas = [
    (str(ROOT / "gui" / "assets"), "gui/assets"),
    (str(APP_ACCESS_SOURCE), "gui"),
    (str(ROOT / "THIRD_PARTY_NOTICES.md"), "."),
    (str(BUILD_METADATA / "VERSION"), "packaging"),
    *velopack_datas,
]

gui_a = Analysis(
    [str(ROOT / "run_local_gui.py")],
    pathex=[str(ROOT)],
    binaries=velopack_binaries,
    datas=gui_datas,
    hiddenimports=velopack_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(VELOPACK_RUNTIME_HOOK)],
    excludes=["pytest"],
    noarchive=False,
    optimize=0,
)

app_access_pure = [entry for entry in gui_a.pure if entry[0] == APP_ACCESS_MODULE]
if len(app_access_pure) != 1:
    raise RuntimeError(
        f"Expected exactly one analyzed {APP_ACCESS_MODULE} module, found {len(app_access_pure)}"
    )
gui_a.pure = TOC(entry for entry in gui_a.pure if entry[0] != APP_ACCESS_MODULE)

worker_a = Analysis(
    [str(ROOT / "run_packaged_worker.py")],
    pathex=[str(ROOT)],
    binaries=playwright_binaries,
    datas=playwright_datas,
    hiddenimports=playwright_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6", "pytest", "velopack"],
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
    icon=str(APP_ICON),
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
    icon=str(APP_ICON),
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
