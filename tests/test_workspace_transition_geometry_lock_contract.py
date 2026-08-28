from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRANSITION = (ROOT / "gui" / "workspace_transition.py").read_text(encoding="utf-8")


def test_mode_switch_locks_one_transition_viewport_before_page_change() -> None:
    assert "self._transition_geometry: QRect | None = None" in TRANSITION
    assert "def _lock_surface_geometry" in TRANSITION
    assert "self._transition_geometry = QRect(geometry)" in TRANSITION

    request = TRANSITION.split("def request_mode", 1)[1].split("def _elapsed_ms", 1)[0]
    assert request.index("self._lock_surface_geometry()") < request.index("self._set_mode(index)")


def test_transition_frames_are_never_non_uniformly_stretched() -> None:
    assert "Qt.AspectRatioMode.KeepAspectRatioByExpanding" in TRANSITION
    assert "Qt.AspectRatioMode.IgnoreAspectRatio" not in TRANSITION


def test_internal_stack_reflow_does_not_abort_or_resize_active_transition() -> None:
    event_filter = TRANSITION.split("def eventFilter", 1)[1].split("def cleanup", 1)[0]
    stack_branch = event_filter.split("if watched is self.stack:", 1)[1].split(
        "elif watched is self.root:", 1
    )[0]
    active_reflow = stack_branch.split("if self._active:", 1)[1].split("else:", 1)[0]

    assert "self._last_geometry_change_s = time.perf_counter()" in active_reflow
    assert "self._finish_immediate()" not in active_reflow
    assert "self._sync_surface_geometry()" not in active_reflow


def test_incoming_capture_waits_for_layout_quiet_period() -> None:
    assert "_GEOMETRY_SETTLE_MS = 24" in TRANSITION
    prepare = TRANSITION.split("def _prepare_incoming", 1)[1].split("@Slot()", 1)[0]
    assert "quiet_ms" in prepare
    assert "QTimer.singleShot(wait_ms, self._prepare_incoming)" in prepare


def test_transition_source_compiles_without_importing_pyside() -> None:
    compile(TRANSITION, str(ROOT / "gui" / "workspace_transition.py"), "exec")
