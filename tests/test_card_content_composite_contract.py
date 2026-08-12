from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NATIVE_VISUAL = (ROOT / "gui" / "native_visual_style.py").read_text(encoding="utf-8")
NATIVE_BG = (ROOT / "gui" / "native_background.py").read_text(encoding="utf-8")


def _body(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_complex_card_content_is_scaled_as_one_live_composite() -> None:
    effect = _body(NATIVE_VISUAL, "class _CardScaleEffect", "class NativeGlassProxy")

    assert "painter.translate(center)" in effect
    assert "painter.scale(scale, scale)" in effect
    assert "self.drawSource(painter)" in effect

    # Whole-card presentation must stay compositor-like. Never reimplement the
    # effect by resizing/repositioning every QLabel/button/editor/table child.
    assert "findChildren(" not in effect
    assert "setGeometry(" not in effect
    assert ".resize(" not in effect
    assert ".grab(" not in effect


def test_child_hover_press_focus_visuals_remain_live_during_card_scale() -> None:
    effect = _body(NATIVE_VISUAL, "class _CardScaleEffect", "class NativeGlassProxy")
    draw = _body(effect, "def draw", "class NativeGlassProxy") if "class NativeGlassProxy" in effect else effect.split("def draw", 1)[1]

    # A retained whole-card pixmap freezes QPushButton/QLineEdit hover, press and
    # focus styling while the parent card remains scaled. The effect must always
    # transform Qt's current live source instead.
    assert "_cached_source" not in effect
    assert "_source_snapshot" not in effect
    assert "sourcePixmap(" not in effect
    assert "painter.drawPixmap(" not in effect
    assert "self.drawSource(painter)" in draw


def test_scale_animation_does_not_mutate_child_layout_or_input_geometry() -> None:
    effect = _body(NATIVE_VISUAL, "class _CardScaleEffect", "class NativeGlassProxy")
    set_scale = _body(effect, "def set_scale", "def boundingRectFor")

    assert "active = abs(scale - 1.0) > 1e-4" in set_scale
    assert "self.setEnabled(active)" in set_scale
    assert "self.updateBoundingRect()" in set_scale
    assert "self.update()" in set_scale
    assert "setGeometry(" not in set_scale
    assert ".resize(" not in set_scale
    assert "move(" not in set_scale


def test_quick_glass_and_complete_widget_content_share_exact_scale() -> None:
    proxy = _body(NATIVE_VISUAL, "class NativeGlassProxy", "class NativeVisualStyleController")

    assert "self.background.set_card_presentation(" in proxy
    assert "scale=scale" in proxy
    assert "self._scale_effect.set_scale(scale)" in proxy

    # QML card presentation must still transform around the same center.
    assert "scale: cardScale" in NATIVE_BG
    assert "transformOrigin: Item.Center" in NATIVE_BG


def test_steady_state_disables_widget_content_scale_effect() -> None:
    effect = _body(NATIVE_VISUAL, "class _CardScaleEffect", "class NativeGlassProxy")
    set_scale = _body(effect, "def set_scale", "def boundingRectFor")

    assert "self.setEnabled(False)" in effect
    assert "active = abs(scale - 1.0) > 1e-4" in set_scale
    assert "self.setEnabled(active)" in set_scale


def test_contract_sources_compile_without_importing_pyside() -> None:
    compile(
        NATIVE_VISUAL,
        str(ROOT / "gui" / "native_visual_style.py"),
        "exec",
    )
    compile(
        NATIVE_BG,
        str(ROOT / "gui" / "native_background.py"),
        "exec",
    )
