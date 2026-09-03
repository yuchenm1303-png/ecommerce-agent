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
WORKER_RUNTIME_PACKAGE = "app.makro"
WORKER_RUNTIME_ROOT = ROOT / "app" / "makro"
if not APP_ICON.is_file():
    raise RuntimeError(f"Application icon was not generated: {APP_ICON}")
if not APP_ACCESS_SOURCE.is_file():
    raise RuntimeError(f"Application access source missing: {APP_ACCESS_SOURCE}")
if not VELOPACK_RUNTIME_HOOK.is_file():
    raise RuntimeError(f"Velopack runtime hook missing: {VELOPACK_RUNTIME_HOOK}")
if not WORKER_RUNTIME_ROOT.is_dir():
    raise RuntimeError(f"Worker runtime package missing: {WORKER_RUNTIME_ROOT}")

BUILD_VERSION = os.environ.get("ECOMMERCE_AGENT_BUILD_VERSION", "").strip()
if not BUILD_VERSION:
    BUILD_VERSION = (ROOT / "packaging" / "VERSION").read_text(encoding="utf-8").strip()
BUILD_METADATA = ROOT / "build" / "package_metadata"
BUILD_METADATA.mkdir(parents=True, exist_ok=True)
(BUILD_METADATA / "VERSION").write_text(BUILD_VERSION + "\n", encoding="utf-8")


def _source_module_map(package_root: Path, package_name: str) -> dict[str, Path]:
    """Return the source path for every module owned by one frozen runtime package."""

    modules: dict[str, Path] = {}
    for source in package_root.rglob("*.py"):
        relative = source.relative_to(package_root).with_suffix("")
        parts = list(relative.parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        suffix = ".".join(parts)
        module_name = package_name if not suffix else f"{package_name}.{suffix}"
        existing = modules.get(module_name)
        if existing is not None and existing != source:
            raise RuntimeError(
                f"Worker runtime source collision for {module_name}: {existing} vs {source}"
            )
        modules[module_name] = source
    if package_name not in modules:
        raise RuntimeError(f"Worker runtime package has no __init__.py: {package_root}")
    return modules


playwright_datas, playwright_binaries, playwright_hiddenimports = collect_all("playwright")
velopack_datas, velopack_binaries, velopack_hiddenimports = collect_all("velopack")
worker_runtime_sources = _source_module_map(
    WORKER_RUNTIME_ROOT,
    WORKER_RUNTIME_PACKAGE,
)
expected_worker_runtime_modules = set(worker_runtime_sources)
for module_name, module_source in worker_runtime_sources.items():
    source_text = module_source.read_text(encoding="utf-8")
    compile(source_text, str(module_source), "exec")
worker_hiddenimports = sorted(
    set(playwright_hiddenimports).union(expected_worker_runtime_modules)
)

gui_datas = [
    (str(ROOT / "gui" / "assets"), "gui/assets"),
    (str(ROOT / "native" / "sakana-helper" / "assets"), "native/sakana/assets"),
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
    hiddenimports=worker_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6", "pytest", "velopack"],
    noarchive=False,
    optimize=0,
)

# PyInstaller's hidden-import pass does not retry a module name when modulegraph
# already contains a non-runnable node for that name. First-party runtime source
# is authoritative, so reconcile any graph omission directly into the existing
# pure-module TOC. Mutating this TOC in place preserves Analysis' code-cache
# association; PYZ compiles a reconciled PYMODULE from its canonical source path.
analyzed_worker_runtime_modules = {entry[0] for entry in worker_a.pure}
omitted_worker_runtime_modules = sorted(
    expected_worker_runtime_modules - analyzed_worker_runtime_modules
)
for module_name in omitted_worker_runtime_modules:
    module_source = worker_runtime_sources[module_name]
    graph_node = worker_a.graph.find_node(module_name)
    graph_state = type(graph_node).__name__ if graph_node is not None else "absent"
    print(
        "PYINSTALLER_WORKER_RECONCILE "
        f"module={module_name} graph_state={graph_state} source={module_source}",
        flush=True,
    )
    worker_a.pure.append((module_name, str(module_source), "PYMODULE"))

worker_pure_modules = {entry[0] for entry in worker_a.pure}
missing_worker_runtime_modules = sorted(
    expected_worker_runtime_modules - worker_pure_modules
)
if missing_worker_runtime_modules:
    raise RuntimeError(
        "PyInstaller Worker freeze reconciliation failed for Makro runtime modules: "
        + ", ".join(missing_worker_runtime_modules)
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
