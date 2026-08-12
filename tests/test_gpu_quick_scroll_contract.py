from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NATIVE = (ROOT / "gui" / "native_background.py").read_text(encoding="utf-8")
PAGE = (ROOT / "gui" / "page_scroll_layout.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def _body(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_quick_mask_is_scene_graph_native_not_cpu_full_window_image() -> None:
    assert 'id: glassMask' in NATIVE
    assert 'layer.enabled: true' in NATIVE
    assert 'maskSource: glassMask' in NATIVE
    assert 'id: maskImg' not in NATIVE
    render_mask = _body(NATIVE, "def render_mask", "def _qml_source")
    assert "QImage(1, 1" in render_mask
    assert "drawRoundedRect" not in render_mask


def test_single_page_cards_have_one_scroll_role_and_parent_transform() -> None:
    assert "SCROLLABLE_ROLE" in NATIVE
    assert 'QByteArray(b"cardScrollable")' in NATIVE
    assert 'id: singleMaskGroup' in NATIVE
    assert 'id: singlePresentationGroup' in NATIVE
    assert 'y: -root.singleScrollY' in NATIVE
    assert 'y: -root.singleViewportY - root.singleScrollY' in NATIVE


def test_scroll_hot_path_publishes_only_one_quick_property() -> None:
    publish = _body(NATIVE, "def _publish_single_scroll", "def _sync_single_viewport")
    assert 'quick.setProperty("singleScrollY", float(value))' in publish
    assert "card_model.sync_geometry" not in publish
    assert "schedule_mask_update" not in publish
    assert "render_mask" not in publish
    assert "mapTo(" not in publish


def test_page_move_event_does_not_reenter_geometry_pipeline() -> None:
    event_filter = _body(NATIVE, "def eventFilter", "def shutdown")
    assert "watched is self._single_scroll_page and event_type == QEvent.Type.Move" in event_filter
    assert "return False" in event_filter


def test_formal_runner_keeps_every_single_card_in_quick_scene() -> None:
    assert "install_scroll_local_glass" not in RUN
    assert "attach_single_page_scroll" in NATIVE
    assert "attach_scroll(scroll, page)" in PAGE
