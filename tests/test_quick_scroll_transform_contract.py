from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NATIVE = (ROOT / "gui" / "native_background.py").read_text(encoding="utf-8")
PAGE = (ROOT / "gui" / "page_scroll_layout.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
RUNTIME = (ROOT / "gui" / "ui_runtime_optimizations.py").read_text(encoding="utf-8")


def _body(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_glass_mask_is_native_qml_geometry_not_image_url() -> None:
    qml = _body(NATIVE, "def _qml_source", "class NativeQuickBackground")
    assert "id: glassMask" in qml
    assert "layer.enabled: true" in qml
    assert "maskSource: glassMask" in qml
    assert "Repeater" in qml
    assert "maskUrl" not in qml
    assert "id: maskImg" not in qml


def test_scroll_hot_path_publishes_exactly_one_scalar_without_geometry_or_mask_work() -> None:
    publish = _body(NATIVE, "def _publish_single_scroll", "def set_card_alpha")
    assert 'quick.setProperty("singleScrollY", float(value))' in publish
    assert "self._mask_revision += 1" in publish
    for forbidden in (
        "sync_geometry",
        "render_mask",
        "schedule_mask_update",
        "QImage",
        ".save(",
        "mapTo(",
    ):
        assert forbidden not in publish


def test_scroll_cards_use_stable_base_coordinates_and_one_scene_offset() -> None:
    snapshot = _body(NATIVE, "def _snapshot", "@staticmethod\n    def _different")
    assert "card_rect.translate(0.0, self._scroll_value())" in snapshot
    qml = _body(NATIVE, "def _qml_source", "class NativeQuickBackground")
    assert "cardY - (cardScrolls ? root.singleScrollY : 0.0)" in qml
    assert "property real singleScrollY: 0.0" in qml


def test_resting_clip_and_hover_overflow_are_separate_qml_concerns() -> None:
    qml = _body(NATIVE, "def _qml_source", "class NativeQuickBackground")
    assert "readonly property bool overflowVisible" in qml
    assert "clip: !overflowVisible" in qml
    assert "x: overflowVisible ? 0 : clipX" in qml
    assert "y: overflowVisible ? 0 : clipY" in qml
    assert "transformOrigin: Item.Center" in qml


def test_formal_runner_has_no_widget_glass_repaint_path() -> None:
    assert "install_scroll_local_glass" not in RUN
    assert "scroll_local_glass" not in RUN
    assert "bind_single_page_scroll" in NATIVE
    assert "bind_scroll(scroll, page)" in PAGE


def test_legacy_runtime_image_provider_cannot_be_on_scroll_hot_path() -> None:
    # The provider remains as a compatibility fallback for older renderers. The
    # GPU renderer never calls _update_mask_texture from its geometry flush, so
    # installing the runtime optimizer cannot put CPU mask rendering back into a
    # continuous scrollbar tick.
    flush = _body(NATIVE, "def _flush_geometry", "def _update_mask_texture")
    assert "_update_mask_texture" not in flush
    assert "render_mask" not in flush
    assert "_GlassMaskImageProvider" in RUNTIME


def test_new_renderer_sources_compile_without_importing_pyside() -> None:
    for path, source in (
        (ROOT / "gui" / "native_background.py", NATIVE),
        (ROOT / "gui" / "page_scroll_layout.py", PAGE),
        (ROOT / "run_local_gui.py", RUN),
    ):
        compile(source, str(path), "exec")
