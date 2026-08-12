from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
LOCAL = (ROOT / "gui" / "scroll_local_glass.py").read_text(encoding="utf-8")
NATIVE = (ROOT / "gui" / "native_background.py").read_text(encoding="utf-8")


def test_formal_runner_retires_widget_local_glass_prototype() -> None:
    assert "from gui.scroll_local_glass import install_scroll_local_glass" not in RUN
    assert "install_scroll_local_glass(window, visual)" not in RUN
    assert "bind_single_page_scroll" in NATIVE


def test_legacy_local_glass_module_remains_unreferenced_for_safe_rollback_only() -> None:
    # Keep the old module source available for bisect/history without putting its
    # 16 ms QWidget parallax repaint timer back on the formal runtime hot path.
    assert "_PARALLAX_REPAINT_MS = 16" in LOCAL
    assert "_parallax_timer" in LOCAL
    assert "scroll_local_glass" not in RUN


def test_sources_compile_without_importing_pyside() -> None:
    compile(LOCAL, str(ROOT / "gui" / "scroll_local_glass.py"), "exec")
    compile(NATIVE, str(ROOT / "gui" / "native_background.py"), "exec")
    compile(RUN, str(ROOT / "run_local_gui.py"), "exec")
