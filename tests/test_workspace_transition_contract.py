from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
TOGGLE = (ROOT / "gui" / "mode_toggle.py").read_text(encoding="utf-8")
TRANSITION = (ROOT / "gui" / "workspace_transition.py").read_text(encoding="utf-8")
SNAPSHOT = (ROOT / "gui" / "workspace_transition_snapshot.py").read_text(encoding="utf-8")
LAYOUT_OWNER = (ROOT / "gui" / "workspace_layout_commit.py").read_text(encoding="utf-8")


def test_formal_runner_installs_switch_before_workspace_transition() -> None:
    assert "install_workspace_mode_switch(window)" in RUNNER
    assert "install_workspace_transition(window, visual)" in RUNNER
    assert RUNNER.index("install_workspace_mode_switch(window)") < RUNNER.index(
        "install_workspace_transition(window, visual)"
    )


def test_toggle_keeps_original_switch_geometry_and_motion() -> None:
    assert "_CORE_WIDTH = 40.0" in TOGGLE
    assert "_CORE_HEIGHT = 20.0" in TOGGLE
    assert "_ACTION_SIZE = 16.0" in TOGGLE
    assert "_ACTION_LEFT_OFF = 1.0" in TOGGLE
    assert "_ACTION_LEFT_ON = 23.0" in TOGGLE
    assert "_TRANSITION_MS = 300" in TOGGLE


def test_workspace_fade_timing_stays_on_approved_profile() -> None:
    assert "_HOLD_MS = 40" in TRANSITION
    assert "_EXIT_END_MS = 155" in TRANSITION
    assert "_ENTER_START_MS = 175" in TRANSITION
    assert "_TOTAL_MS = 390" in TRANSITION
    assert "_VEIL_MAX_OPACITY = 0.06" in TRANSITION


def test_transition_never_rasterizes_a_workspace_page() -> None:
    assert "WorkspaceTransitionBackdropRenderer" in TRANSITION
    assert "WorkspaceTransitionSnapshotRenderer" not in TRANSITION
    assert "capture_composite" not in TRANSITION
    assert "page.render(" not in TRANSITION
    assert "page.render(" not in SNAPSHOT
    assert "page.grab()" not in SNAPSHOT
    assert "grabWindow" not in TRANSITION
    assert "grabWindow" not in SNAPSHOT
    assert "_paint_glass" not in SNAPSHOT
    assert "capture_composite" not in SNAPSHOT


def test_static_qml_mode_switch_is_synchronous_before_queued_scene_refresh() -> None:
    request = TRANSITION.split("def request_mode", 1)[1].split("def _elapsed_ms", 1)[0]
    static_branch = request.split("if self._static_qml_active():", 1)[1].split(
        "if (", 1
    )[0]
    assert static_branch.index("self._commit_target_layout(index)") < static_branch.index(
        "self._set_mode(index)"
    )
    assert "return" in static_branch
    assert request.index("if self._static_qml_active():") < request.index(
        "self._lock_surface_geometry()"
    )


def test_legacy_page_changes_only_after_cover_is_opaque() -> None:
    request = TRANSITION.split("def request_mode", 1)[1].split("def _elapsed_ms", 1)[0]
    legacy = request.split("self._lock_surface_geometry()", 1)[1]
    assert "self._capture_neutral_background()" in legacy
    assert "capture_composite" not in legacy
    assert "self._set_mode(index)" not in legacy
    assert "self._switch_target_under_cover" in legacy

    switch = TRANSITION.split("def _switch_target_under_cover", 1)[1].split(
        "def _prepare_incoming", 1
    )[0]
    assert switch.index("self._surface.set_cover(opacity=1.0") < switch.index(
        "self._set_mode(self._target_index)"
    )
    assert switch.index("self._surface.repaint()") < switch.index(
        "self._set_mode(self._target_index)"
    )
    assert "QTimer.singleShot(0, self._prepare_incoming)" in switch

    prepare = TRANSITION.split("def _prepare_incoming", 1)[1].split("@Slot()", 1)[0]
    assert "self._commit_target_layout(self._target_index)" in prepare


def test_layout_activation_remains_centralized_in_workspace_layout_owner() -> None:
    assert "class WorkspaceLayoutCommitter" in LAYOUT_OWNER
    assert "layout.activate()" in LAYOUT_OWNER
    assert "layout.activate()" not in TRANSITION
    assert 'getattr(owner, "prepare_page", None)' in TRANSITION
    assert "self.prime_all()" in LAYOUT_OWNER
    assert "QEvent.Type.Resize" in LAYOUT_OWNER
    assert "QEvent.Type.Show" in LAYOUT_OWNER


def test_quick_sync_happens_after_hidden_target_layout_commit() -> None:
    prepare = TRANSITION.split("def _prepare_incoming", 1)[1].split("@Slot()", 1)[0]
    assert prepare.index("self._commit_target_layout(self._target_index)") < prepare.index(
        "quick.update()"
    )
    assert "self._awaiting_quick_frame = True" in prepare
    assert "frameSwapped.connect" in TRANSITION
    assert "self._live_ready = True" in TRANSITION

    advance = TRANSITION.split("def _advance", 1)[1].split(
        "def _clear_transition_state", 1
    )[0]
    assert "self._mode_switched" in advance
    assert "self._live_ready" in advance


def test_transition_surface_fades_only_neutral_backdrop() -> None:
    surface = TRANSITION.split("class _WorkspaceTransitionSurface", 1)[1].split(
        "class WorkspaceTransitionController", 1
    )[0]
    assert "QGraphicsOpacityEffect" in surface
    assert "WA_OpaquePaintEvent" in surface
    assert "self._backdrop" in surface
    assert "_outgoing" not in surface
    assert "_incoming" not in surface
    assert "CompositionMode_Source" in surface

    raise_surface = TRANSITION.split("def _raise_transition_surface", 1)[1].split(
        "def _capture_neutral_background", 1
    )[0]
    assert "self._surface.raise_()" in raise_surface


def test_transition_sources_compile_without_importing_pyside() -> None:
    for name, source in (
        ("gui/mode_toggle.py", TOGGLE),
        ("gui/workspace_transition.py", TRANSITION),
        ("gui/workspace_transition_snapshot.py", SNAPSHOT),
        ("gui/workspace_layout_commit.py", LAYOUT_OWNER),
    ):
        compile(source, str(ROOT / name), "exec")
