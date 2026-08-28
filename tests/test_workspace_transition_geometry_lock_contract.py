from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRANSITION = (ROOT / "gui" / "workspace_transition.py").read_text(encoding="utf-8")


def test_mode_switch_keeps_live_page_unchanged_until_neutral_cover() -> None:
    assert "self._transition_geometry: QRect | None = None" in TRANSITION
    assert "def _lock_surface_geometry" in TRANSITION
    assert "self._transition_geometry = QRect(geometry)" in TRANSITION
    assert "def _switch_target_under_cover" in TRANSITION

    request = TRANSITION.split("def request_mode", 1)[1].split("def _elapsed_ms", 1)[0]
    normal_path = request.split("# Pre-solve the hidden destination", 1)[1]
    assert normal_path.index("self._lock_surface_geometry()") < normal_path.index(
        "QTimer.singleShot(max(1, int(_EXIT_END_MS)), self._switch_target_under_cover)"
    )
    assert "self._set_mode(self._target_index)" not in normal_path

    covered_switch = TRANSITION.split("def _switch_target_under_cover", 1)[1].split(
        "def _current_layout_signature", 1
    )[0]
    assert covered_switch.index("outgoing_alpha=0.0") < covered_switch.index(
        "self._set_mode(self._target_index)"
    )
    assert covered_switch.index("incoming_alpha=0.0") < covered_switch.index(
        "self._set_mode(self._target_index)"
    )
    assert covered_switch.index("self._surface.repaint()") < covered_switch.index(
        "self._set_mode(self._target_index)"
    )


def test_target_page_is_prelaid_out_before_and_after_hidden_switch() -> None:
    assert "def _prelayout_target" in TRANSITION
    request = TRANSITION.split("def request_mode", 1)[1].split("def _elapsed_ms", 1)[0]
    assert "self._prelayout_target(index)" in request

    covered_switch = TRANSITION.split("def _switch_target_under_cover", 1)[1].split(
        "def _current_layout_signature", 1
    )[0]
    assert covered_switch.index("self._set_mode(self._target_index)") < covered_switch.index(
        "self._prelayout_target(self._target_index)"
    )


def test_transition_frames_are_never_non_uniformly_stretched() -> None:
    assert "Qt.AspectRatioMode.KeepAspectRatioByExpanding" in TRANSITION
    assert "Qt.AspectRatioMode.IgnoreAspectRatio" not in TRANSITION


def test_target_glass_card_geometry_must_settle_before_incoming_capture() -> None:
    assert "from .native_background import _GLASS_NAMES, _OVERSCAN" in TRANSITION
    assert "def _current_layout_signature" in TRANSITION
    signature = TRANSITION.split("def _current_layout_signature", 1)[1].split(
        "def _prepare_incoming", 1
    )[0]
    assert "page.findChildren(QFrame)" in signature
    assert "frame.objectName() not in _GLASS_NAMES" in signature
    assert "frame.mapTo(page, QPoint(0, 0))" in signature
    assert "int(frame.width())" in signature
    assert "int(frame.height())" in signature

    prepare = TRANSITION.split("def _prepare_incoming", 1)[1].split("@Slot()", 1)[0]
    assert "signature = self._current_layout_signature()" in prepare
    assert "signature != self._layout_signature" in prepare
    assert "quiet_ms" in prepare
    assert "_GEOMETRY_SETTLE_MS" in prepare
    assert "QTimer.singleShot(wait_ms, self._prepare_incoming)" in prepare


def test_internal_reflow_is_recorded_without_exposing_live_geometry() -> None:
    event_filter = TRANSITION.split("def eventFilter", 1)[1].split("def cleanup", 1)[0]
    assert "QEvent.Type.LayoutRequest" in event_filter
    assert "self._last_geometry_change_s = time.perf_counter()" in event_filter
    assert "self._layout_signature = None" in event_filter

    # The active transition owns the visible pixels. Internal stack/page reflow
    # therefore records instability instead of resizing the transition surface.
    stack_branch = event_filter.split("if watched is self.stack:", 1)[1].split(
        "elif watched in self._watched_pages:", 1
    )[0]
    active_reflow = stack_branch.split("if self._active:", 1)[1].split("else:", 1)[0]
    assert "self._sync_surface_geometry()" not in active_reflow
    assert "self._finish_immediate()" not in active_reflow


def test_incoming_capture_still_waits_for_presented_quick_frame() -> None:
    prepare = TRANSITION.split("def _prepare_incoming", 1)[1].split("@Slot()", 1)[0]
    assert "self._awaiting_quick_frame = True" in prepare
    assert "quick.update()" in prepare
    assert "self._capture_composite()" not in prepare
    assert "def _on_quick_frame_swapped" in TRANSITION
    assert "def _capture_incoming_after_quick_sync" in TRANSITION


def test_transition_source_compiles_without_importing_pyside() -> None:
    compile(TRANSITION, str(ROOT / "gui" / "workspace_transition.py"), "exec")
