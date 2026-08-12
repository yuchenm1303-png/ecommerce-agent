from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NATIVE = (ROOT / "gui" / "native_background.py").read_text(encoding="utf-8")
FAST = (ROOT / "gui" / "single_scroll_glass_fastpath.py").read_text(encoding="utf-8")
RUNTIME = (ROOT / "gui" / "ui_runtime_optimizations.py").read_text(encoding="utf-8")


def _body(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_fastpath_preserves_proven_blur_and_mask_visual_pipeline() -> None:
    assert 'property url blurUrl' in NATIVE
    assert 'property url maskUrl' in NATIVE
    assert 'id: blurSource' in NATIVE
    assert 'source: root.blurUrl' in NATIVE
    assert 'id: maskImg' in NATIVE
    assert 'source: root.maskUrl' in NATIVE
    assert 'maskSource: maskImg' in NATIVE
    assert 'MultiEffect {' in NATIVE
    assert 'autoPaddingEnabled: false' in NATIVE

    # The performance module is forbidden from defining a replacement QML scene
    # or any independent visual style tokens.
    assert 'def _qml_source' not in FAST
    assert 'MultiEffect {' not in FAST
    assert 'FrameAnimation {' not in FAST
    assert 'cardAlpha / 255.0' not in FAST


def test_fastpath_mask_rasterization_matches_existing_renderer() -> None:
    render = _body(FAST, "def _render_mask", "def _install_geometry_watchers")
    assert 'QImage.Format.Format_ARGB32_Premultiplied' in FAST
    assert 'QPainter.RenderHint.Antialiasing' in render
    assert 'painter.setPen(Qt.PenStyle.NoPen)' in render
    assert 'painter.setBrush(Qt.GlobalColor.white)' in render
    assert 'painter.setClipRect(clip)' in render
    assert 'painter.drawRoundedRect(card, radius, radius)' in render
    assert 'native_background_module' in render


def test_continuous_scroll_does_not_enter_widget_geometry_or_24ms_mask_timer() -> None:
    hot = _body(FAST, "def _on_scroll", "def _on_scroll_range_changed")
    assert '_apply_cached_scroll' in hot
    assert 'mapTo(' not in hot
    assert 'parentWidget(' not in hot
    assert 'sync_geometry' not in hot
    assert 'schedule_mask_update' not in hot
    assert '_geometry_timer' not in hot

    apply = _body(FAST, "def _apply_cached_scroll", "def _publish_current_mask")
    assert 'mapTo(' not in apply
    assert 'parentWidget(' not in apply
    assert 'sync_geometry' not in apply


def test_scroll_mask_publication_stays_in_memory_when_runtime_optimizer_is_installed() -> None:
    assert 'engine.addImageProvider(_MASK_PROVIDER_ID, provider)' in RUNTIME
    assert 'provider.publish(image)' in RUNTIME
    assert 'QUrl(f"image://{_MASK_PROVIDER_ID}/{bg._mask_revision}")' in RUNTIME
    assert 'background._update_mask_texture = MethodType(update_mask_texture, background)' in RUNTIME
    publish = _body(FAST, "def _publish_current_mask", "def _on_scroll")
    assert 'update_mask()' in publish
    assert 'quick.update()' in publish


def test_fastpath_source_compiles_without_importing_pyside() -> None:
    compile(FAST, str(ROOT / "gui" / "single_scroll_glass_fastpath.py"), "exec")