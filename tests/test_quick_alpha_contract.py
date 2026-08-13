from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_quick_alpha_buffer_is_enabled_before_first_quick_window() -> None:
    assert "from PySide6.QtQuick import QQuickWindow" in RUNNER
    assert "QQuickWindow.setDefaultAlphaBuffer(True)" in RUNNER
    assert RUNNER.index("QQuickWindow.setDefaultAlphaBuffer(True)") < RUNNER.index(
        "install_native_visual_style(window)"
    )
