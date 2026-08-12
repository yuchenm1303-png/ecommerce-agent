from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NATIVE_VISUAL = (ROOT / "gui" / "native_visual_style.py").read_text(encoding="utf-8")
NATIVE_BG = (ROOT / "gui" / "native_background.py").read_text(encoding="utf-8")


def _body(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_complex_card_content_is_flattened_and_scaled_as_one_composite() -> None:
    effect = _body(NATIVE_VISUAL, "class _CardScaleEffect", "class NativeGlassProxy")

    assert "self.sourcePixmap(" in effect
    assert "Qt.CoordinateSystem.LogicalCoordinates" in effect
    assert "QGraphicsEffect.PixmapPadMode.NoPad" in effect
    assert "painter.drawPixmap(offset, pixmap)" in effect
    assert "painter.translate(center)" in effect
    assert "painter.scale(scale, scale)" in effect

    assert "findChildren(" not in effect
    assert "setGeometry(" not in effect
    assert ".resize(" not in effect
    assert ".grab(" not in effect


def test_current_interactive_card_keeps_fresh_source_path() -> None:
    effect = _body(NATIVE_VISUAL, "class _CardScaleEffect", "class NativeGlassProxy")
    composite = _body(effect, "def _current_composite", "def draw")

    # Without frozen ownership, every redraw reaches sourcePixmap() again. This is
    # what keeps QPushButton/QLineEdit hover, press and focus visuals live.
    assert "self._frozen" in composite
    assert "return self._frozen_source, self._frozen_offset" in composite
    assert "self.sourcePixmap(" in composite
    assert "if self._frozen:" in composite
    assert "self._frozen_source = pixmap" in composite
    assert "_cached_source" not in effect
    assert "_source_snapshot" not in effect


def test_outgoing_card_can_reuse_exactly_one_frozen_composite() -> None:
    effect = _body(NATIVE_VISUAL, "class _CardScaleEffect", "class NativeGlassProxy")
    freeze = _body(effect, "def set_frozen", "def set_scale")
    composite = _body(effect, "def _current_composite", "def draw")

    assert "self._frozen_source: QPixmap | None = None" in effect
    assert "self._freeze_requested = False" in effect
    assert "self._clear_frozen_source()" in freeze
    assert "self._freeze_requested = frozen" in freeze
    assert "not self._freeze_requested" in composite
    assert "return self._frozen_source, self._frozen_offset" in composite
    assert "self._freeze_requested = False" in composite


def test_exact_rest_releases_frozen_card_memory_and_disables_effect() -> None:
    effect = _body(NATIVE_VISUAL, "class _CardScaleEffect", "class NativeGlassProxy")
    set_scale = _body(effect, "def set_scale", "def boundingRectFor")

    assert "active = abs(scale - 1.0) > 1e-4" in set_scale
    assert "self.setEnabled(active)" in set_scale
    assert "if not active:" in set_scale
    assert "self._frozen = False" in set_scale
    assert "self._freeze_requested = False" in set_scale
    assert "self._clear_frozen_source()" in set_scale


def test_scale_animation_does_not_mutate_child_layout_or_input_geometry() -> None:
    effect = _body(NATIVE_VISUAL, "class _CardScaleEffect", "class NativeGlassProxy")
    set_scale = _body(effect, "def set_scale", "def boundingRectFor")

    assert "self.updateBoundingRect()" in set_scale
    assert "self.update()" in set_scale
    assert "setGeometry(" not in set_scale
    assert ".resize(" not in set_scale
    assert "move(" not in set_scale


def test_quick_glass_and_complete_widget_composite_share_exact_scale() -> None:
    proxy = _body(NATIVE_VISUAL, "class NativeGlassProxy", "class NativeVisualStyleController")

    assert "self.background.set_card_presentation(" in proxy
    assert "scale=scale" in proxy
    assert "self._scale_effect.set_scale(scale)" in proxy
    assert "scale: cardScale" in NATIVE_BG
    assert "transformOrigin: Item.Center" in NATIVE_BG


def test_cleanup_explicitly_thaws_before_detaching_effect() -> None:
    proxy = _body(NATIVE_VISUAL, "class NativeGlassProxy", "class NativeVisualStyleController")
    cleanup = _body(proxy, "def cleanup", "class NativeVisualStyleController") if "class NativeVisualStyleController" in proxy else proxy.split("def cleanup", 1)[1]
    assert "self._scale_effect.set_frozen(False)" in cleanup
    assert "self._scale_effect.set_scale(1.0)" in cleanup
    assert "self.frame.setGraphicsEffect(None)" in cleanup


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
