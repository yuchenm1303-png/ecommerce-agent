from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
EMBEDDED = (ROOT / "gui" / "embedded_quick_background.py").read_text(encoding="utf-8")


def test_formal_runner_uses_one_qwidget_top_level_instead_of_native_quick_owner() -> None:
    assert "window.showMaximized()" in RUN
    assert "install_native_window_shell" not in RUN
    assert "shell.show()" not in RUN
    assert "QQuickWindow.setDefaultAlphaBuffer" not in RUN
    assert "QSG_RENDER_LOOP" not in RUN


def test_embedded_quick_background_never_creates_a_native_child_handle() -> None:
    assert "from PySide6.QtQuickWidgets import QQuickWidget" in EMBEDDED
    assert 'composition_domain = "widget"' in EMBEDDED
    assert "host.lower()" in EMBEDDED
    assert ".winId()" not in EMBEDDED
    assert "SetParent" not in EMBEDDED
    assert "createWindowContainer" not in EMBEDDED


def test_active_sources_compile_without_importing_pyside() -> None:
    compile(RUN, "run_local_gui.py", "exec")
    compile(EMBEDDED, "gui/embedded_quick_background.py", "exec")
