from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VISUAL = (ROOT / "gui" / "native_visual_style.py").read_text(encoding="utf-8")
STYLE = (ROOT / "gui" / "nekro_style.py").read_text(encoding="utf-8")


def test_native_renderer_imports_static_tokens_not_legacy_renderer() -> None:
    assert "from .nekro_style import NEKRO_STYLE" in VISUAL
    assert "from .visual_style import" not in VISUAL
    assert not (ROOT / "gui" / "visual_style.py").exists()
    assert "window.setStyleSheet(window.styleSheet() + \"\\n\" + NEKRO_STYLE)" in VISUAL


def test_static_style_module_contains_no_runtime_rendering_or_timers() -> None:
    assert "NEKRO_STYLE = r\"\"\"" in STYLE
    for forbidden in (
        "QTimer",
        "QCursor",
        "QPainter",
        "QGraphicsBlurEffect",
        "BackgroundLayer",
        "GlassBackdrop",
        "VisualStyleController",
    ):
        assert forbidden not in STYLE


def test_style_and_visual_sources_compile_without_importing_qt() -> None:
    compile(STYLE, "gui/nekro_style.py", "exec")
    compile(VISUAL, "gui/native_visual_style.py", "exec")
