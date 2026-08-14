from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VISUAL = (ROOT / "gui" / "native_visual_style.py").read_text(encoding="utf-8")
CARD = (ROOT / "gui" / "nekro_card_fx.py").read_text(encoding="utf-8")
NATIVE = (ROOT / "gui" / "native_background.py").read_text(encoding="utf-8")


def _body(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_complex_card_content_is_scaled_as_one_composite() -> None:
    effect = _body(VISUAL, "class _CardScaleEffect", "class NativeGlassProxy")
    assert "self.sourcePixmap(" in effect
    assert "QGraphicsEffect.PixmapPadMode.NoPad" in effect
    assert "painter.scale(scale, scale)" in effect
    assert "painter.drawPixmap(offset, pixmap)" in effect
    for forbidden in ("findChildren(", "setGeometry(", ".resize(", ".grab("):
        assert forbidden not in effect


def test_frozen_mode_reuses_the_same_source_pixmap_during_tween() -> None:
    effect = _body(VISUAL, "class _CardScaleEffect", "class NativeGlassProxy")
    composite = _body(effect, "def _current_composite", "def draw")
    assert "return self._frozen_source, self._frozen_offset" in composite
    assert "self.sourcePixmap(" in composite
    assert "if self._frozen:" in composite
    assert "self._frozen_source = pixmap" in composite
    assert "self._freeze_requested = False" in composite

    recapture = _body(CARD, "def _recapture_for_motion", "def _retire_stale_motions")
    assert "self._set_content_frozen(state, False)" in recapture
    assert "self._set_content_frozen(state, True)" in recapture


def test_tween_endpoint_releases_frozen_content_and_effect_memory() -> None:
    advance = _body(CARD, "def _advance_state", "def _advance_motions")
    assert "if not state.moving:" in advance
    assert "self._set_content_frozen(state, False)" in advance

    effect = _body(VISUAL, "class _CardScaleEffect", "class NativeGlassProxy")
    set_scale = _body(effect, "def set_scale", "def boundingRectFor")
    assert "exact_rest = abs(requested - 1.0) <= _NORMAL_SCALE_EPSILON" in set_scale
    assert "self.setEnabled(active)" in set_scale
    assert "if not active:" in set_scale
    assert "self._clear_frozen_source()" in set_scale


def test_scale_animation_never_changes_child_layout_geometry() -> None:
    effect = _body(VISUAL, "class _CardScaleEffect", "class NativeGlassProxy")
    assert "painter.translate(center)" in effect
    assert "painter.scale(scale, scale)" in effect
    assert "setGeometry(" not in effect
    assert ".resize(" not in effect
    assert "move(" not in effect


def test_quick_glass_and_widget_composite_share_requested_scale() -> None:
    proxy = _body(VISUAL, "class NativeGlassProxy", "class NativeVisualStyleController")
    assert "self.background.set_card_presentation(" in proxy
    assert "scale=scale" in proxy
    assert "self._scale_effect.set_scale(scale)" in proxy
    assert "scale: cardScale" in NATIVE
    assert "transformOrigin: Item.Center" in NATIVE


def test_cleanup_thaws_before_detaching_effect() -> None:
    proxy = _body(VISUAL, "class NativeGlassProxy", "class NativeVisualStyleController")
    cleanup = proxy.split("def cleanup", 1)[1]
    assert "self._scale_effect.set_frozen(False)" in cleanup
    assert "self._scale_effect.set_scale(1.0)" in cleanup
    assert "self.frame.setGraphicsEffect(None)" in cleanup


def test_sources_compile_without_importing_pyside() -> None:
    for relative in (
        "gui/native_visual_style.py",
        "gui/nekro_card_fx.py",
        "gui/native_background.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        compile(source, relative, "exec")
