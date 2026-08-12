from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
EMBEDDED = (ROOT / "gui" / "embedded_quick_background.py").read_text(encoding="utf-8")


def test_qquickwidget_uses_widget_composition_without_native_alpha_owner() -> None:
    assert "from PySide6.QtQuickWidgets import QQuickWidget" in EMBEDDED
    assert "QQuickWidget.ResizeMode.SizeRootObjectToView" in EMBEDDED
    assert "QQuickWindow.setDefaultAlphaBuffer" not in RUNNER
    assert "from PySide6.QtQuick import QQuickWindow" not in RUNNER
    assert ".winId()" not in EMBEDDED
