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
    assert "self.drawSource(painter)" in effect

    # Whole-card presentation must stay compositor-like. Never reimplement the
    # effect by resizing/repositioning every QLabel/button/editor/table child.
    assert "findChildren(" not in effect
    assert "setGeometry(" not in effect
    assert ".resize(" not in effect
    assert ".grab(" not in effect


def test_scale_only_animation_frames_reuse_the_same_widget_source_pixmap() -> None:
    effect = _body(NATIVE_VISUAL, "class _CardScaleEffect", "class NativeGlassProxy")
    snapshot = _body(effect, "def _source_snapshot", "def draw")
    set_scale = _body(effect, "def set_scale", "def boundingRectFor")

    assert "self._cached_source" in effect
    assert "if pixmap is not None and not pixmap.isNull():" in snapshot
    assert "return pixmap, self._cached_offset" in snapshot
    assert "self._cached_source = pixmap" in snapshot

    # A scale tick only updates the effect; it must not rebuild the source.
    assert "self.update()" in set_scale
    assert "sourcePixmap(" not in set_scale


def test_real_widget_changes_invalidate_the_cached_card_composite() -> None:
    effect = _body(NATIVE_VISUAL, "class _CardScaleEffect", "class NativeGlassProxy")
    source_changed = _body(effect, "def sourceChanged", "def set_scale")

    assert "self._clear_source_cache()" in source_changed
    assert "super().sourceChanged(flags)" in source_changed

    clear_cache = _body(effect, "def _clear_source_cache", "def sourceChanged")
    assert "self._cached_source = None" in clear_cache
    assert "self._cached_offset = QPoint()" in clear_cache


def test_quick_glass_and_complete_widget_composite_share_exact_scale() -> None:
    proxy = _body(NATIVE_VISUAL, "class NativeGlassProxy", "class NativeVisualStyleController")

    assert "self.background.set_card_presentation(" in proxy
    assert "scale=scale" in proxy
    assert "self._scale_effect.set_scale(scale)" in proxy

    # QML card presentation must still transform around the same center.
    assert "scale: cardScale" in NATIVE_BG
    assert "transformOrigin: Item.Center" in NATIVE_BG


def test_card_composite_cache_is_released_outside_active_transform() -> None:
    effect = _body(NATIVE_VISUAL, "class _CardScaleEffect", "class NativeGlassProxy")
    set_scale = _body(effect, "def set_scale", "def boundingRectFor")

    assert "active = abs(scale - 1.0) > 1e-4" in set_scale
    assert "self.setEnabled(active)" in set_scale
    assert "if not active:" in set_scale
    assert "self._clear_source_cache()" in set_scale


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
