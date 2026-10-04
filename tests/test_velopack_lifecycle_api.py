from __future__ import annotations

import py_compile
from pathlib import Path

import velopack

ROOT = Path(__file__).resolve().parents[1]


def test_pinned_velopack_exposes_required_fast_lifecycle_callbacks() -> None:
    app = velopack.App()
    assert callable(getattr(app, "on_before_update_fast_callback", None))
    assert callable(getattr(app, "on_before_uninstall_fast_callback", None))


def test_early_update_hook_modules_compile_without_qt_startup() -> None:
    py_compile.compile(str(ROOT / "app" / "update_browser_gate.py"), doraise=True)
    py_compile.compile(str(ROOT / "packaging" / "velopack_runtime_hook.py"), doraise=True)
