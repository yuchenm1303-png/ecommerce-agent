from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
LOCAL = (ROOT / "gui" / "scroll_local_glass.py").read_text(encoding="utf-8")


def test_formal_runner_no_longer_installs_legacy_widget_scroll_glass() -> None:
    assert "from gui.scroll_local_glass import install_scroll_local_glass" not in RUN
    assert "install_scroll_local_glass(window, visual)" not in RUN
    assert "shared GPU transform" in RUN


def test_legacy_widget_compositor_remains_unreferenced_for_safe_history() -> None:
    assert "class ScrollLocalGlassController(QObject)" in LOCAL
    assert "_PARALLAX_REPAINT_MS = 16" in LOCAL
    assert "install_scroll_local_glass" in LOCAL
    assert "scroll_local_glass" not in RUN


def test_legacy_source_still_compiles_without_importing_pyside() -> None:
    compile(LOCAL, str(ROOT / "gui" / "scroll_local_glass.py"), "exec")
