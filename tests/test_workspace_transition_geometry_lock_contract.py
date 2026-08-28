from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRANSITION = (ROOT / "gui" / "workspace_transition.py").read_text(encoding="utf-8")
SNAPSHOT = (ROOT / "gui" / "workspace_transition_snapshot.py").read_text(encoding="utf-8")
LAYOUT_OWNER = (ROOT / "gui" / "workspace_layout_commit.py").read_text(encoding="utf-8")
BATCH_RESPONSIVE = (ROOT / "gui" / "batch_card_responsive.py").read_text(encoding="utf-8")
TOGGLE = (ROOT / "gui" / "mode_toggle.py").read_text(encoding="utf-8")


def test_mode_switch_locks_one_transition_viewport() -> None:
    assert "self._transition_geometry: QRect | None = None" in TRANSITION
    assert "def _lock_surface_geometry" in TRANSITION
    assert "self._transition_geometry = QRect(geometry)" in TRANSITION

    request = TRANSITION.split("def request_mode", 1)[1].split("def _elapsed_ms", 1)[0]
    assert request.index("self._lock_surface_geometry()") < request.index(
        "self._capture_composite()"
    )


def test_transition_frames_are_never_non_uniformly_stretched() -> None:
    assert "Qt.AspectRatioMode.KeepAspectRatioByExpanding" in TRANSITION
    assert "Qt.AspectRatioMode.IgnoreAspectRatio" not in TRANSITION


def test_mode_click_path_cannot_force_layout_reflow() -> None:
    forbidden = (
        "layout.activate()",
        "page_layout",
        "_prelayout_target",
        "prepare_page(",
        "updateGeometry()",
        "setGeometry(contents)",
    )
    for token in forbidden:
        assert token not in TRANSITION


def test_persistent_pages_are_precommitted_only_at_global_geometry_boundaries() -> None:
    event_filter = LAYOUT_OWNER.split("def eventFilter", 1)[1].split("def cleanup", 1)[0]
    assert "QEvent.Type.Resize" in event_filter
    assert "QEvent.Type.Show" in event_filter
    assert "self.prime_all()" in event_filter
    assert "currentChanged" not in LAYOUT_OWNER
    assert "QTimer" not in LAYOUT_OWNER


def test_batch_card_width_has_no_deferred_correction_frame() -> None:
    assert "QTimer" not in BATCH_RESPONSIVE
    assert "singleShot" not in BATCH_RESPONSIVE
    assert "def commit_now" in BATCH_RESPONSIVE

    schedule = BATCH_RESPONSIVE.split("def schedule_refresh", 1)[1].split(
        "def eventFilter", 1
    )[0]
    assert "self.commit_now()" in schedule

    event_filter = BATCH_RESPONSIVE.split("def eventFilter", 1)[1].split(
        "def install_batch_card_responsive", 1
    )[0]
    assert "QEvent.Type.Resize" in event_filter
    assert "QEvent.Type.Show" in event_filter
    assert "QEvent.Type.LayoutRequest" in event_filter
    assert "self.commit_now()" in event_filter


def test_single_only_header_action_keeps_its_layout_slot_when_hidden() -> None:
    assert "open_run_button = getattr(window, \"open_run_button\", None)" in TOGGLE
    assert "policy.setRetainSizeWhenHidden(True)" in TOGGLE
    assert "open_run_button.setSizePolicy(policy)" in TOGGLE


def test_snapshot_glass_and_qwidget_content_share_one_geometry_source() -> None:
    assert "def _card_geometry" in SNAPSHOT
    assert "frame.mapToGlobal" in SNAPSHOT
    assert "page.render(" in SNAPSHOT
    assert "grabWindow" not in SNAPSHOT


def test_active_stack_reflow_cannot_resize_transition_surface() -> None:
    event_filter = TRANSITION.split("def eventFilter", 1)[1].split("def cleanup", 1)[0]
    stack_branch = event_filter.split("if watched is self.stack:", 1)[1].split(
        "elif watched is self.root:", 1
    )[0]
    assert "if not self._active:" in stack_branch
    assert "self._sync_surface_geometry()" in stack_branch
    assert "self._finish_immediate()" not in stack_branch.split(
        "QEvent.Type.Move", 1
    )[1].split("elif event_type == QEvent.Type.Show", 1)[0]


def test_geometry_sources_compile_without_importing_pyside() -> None:
    for name, source in (
        ("gui/workspace_transition.py", TRANSITION),
        ("gui/workspace_transition_snapshot.py", SNAPSHOT),
        ("gui/workspace_layout_commit.py", LAYOUT_OWNER),
        ("gui/batch_card_responsive.py", BATCH_RESPONSIVE),
        ("gui/mode_toggle.py", TOGGLE),
    ):
        compile(source, str(ROOT / name), "exec")
