from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = (ROOT / "gui" / "modal_overlay_zorder.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
MODAL = (ROOT / "gui" / "modal_interaction.py").read_text(encoding="utf-8")


def test_transition_surface_is_mapped_only_while_qml_item_is_active() -> None:
    assert 'getattr(item, "activeChanged", None)' in BRIDGE
    assert "active_changed.connect(self._sync_surface_visibility)" in BRIDGE
    assert 'bool(item.property("active"))' in BRIDGE
    assert "surface.show()" in BRIDGE
    assert "surface.raise_()" in BRIDGE
    assert "surface.requestUpdate()" in BRIDGE
    assert "surface.hide()" in BRIDGE
    assert "surface.lower()" not in BRIDGE


def test_active_signal_precedes_animation_command_and_idle_surface_is_hidden() -> None:
    issue = MODAL.split("def _issue_transition", 1)[1].split(
        "def _begin_pending_open", 1
    )[0]
    assert issue.index('item.setProperty("active", True)') < issue.index(
        'item.setProperty("command", self._command)'
    )

    bind = BRIDGE.split("def _bind_after_modal_prime", 1)[1].split(
        "def _sync_surface_visibility", 1
    )[0]
    assert "self._sync_surface_visibility()" in bind
    assert "_BIND_RETRIES = 4" in BRIDGE
    assert "self._bind_attempts += 1" in bind
    assert "self._bind_attempts < _BIND_RETRIES" in bind
    assert "QTimer.singleShot(0, self._bind_after_modal_prime)" in BRIDGE


def test_handoff_watchdog_guarantees_fullscreen_overlay_release() -> None:
    assert "_OPEN_HANDOFF_GUARD_MS = 310" in BRIDGE
    assert "_CLOSE_HANDOFF_GUARD_MS = 230" in BRIDGE
    assert "self._handoff_guard.setSingleShot(True)" in BRIDGE
    assert "self._handoff_guard.timeout.connect(self._force_handoff)" in BRIDGE

    sync = BRIDGE.split("def _sync_surface_visibility", 1)[1].split(
        "def _force_handoff", 1
    )[0]
    assert 'closing = bool(item.property("closingRequest"))' in sync
    assert "self._handoff_guard.start(" in sync
    assert "self._handoff_guard.stop()" in sync

    force = BRIDGE.split("def _force_handoff", 1)[1].split("def cleanup", 1)[0]
    assert 'state == "opening"' in force
    assert 'state == "closing"' in force
    assert "self.modal._on_transition_finished(True)" in force
    assert "self.modal._on_transition_finished(False)" in force
    assert "finally:" in force
    assert "self.modal._deactivate_transition()" in force
    assert "item.setProperty(\"active\", False)" in force
    assert "surface.hide()" in force


def test_watchdog_does_not_drive_per_frame_animation() -> None:
    assert "OpacityAnimator" in MODAL
    assert "YAnimator" in MODAL
    assert "ScaleAnimator" in MODAL
    assert "QTimer" in BRIDGE
    assert "timeout.connect(self._force_handoff)" in BRIDGE
    assert "setInterval" not in BRIDGE
    assert "PreciseTimer" not in BRIDGE


def test_runner_installs_overlay_lifecycle_immediately_after_modal_controller() -> None:
    assert "from gui.modal_overlay_zorder import install_modal_overlay_zorder" in RUNNER
    assert "modal = install_modal_interaction(window, details)" in RUNNER
    assert "install_modal_overlay_zorder(window, modal)" in RUNNER
    assert RUNNER.index("modal = install_modal_interaction(window, details)") < RUNNER.index(
        "install_modal_overlay_zorder(window, modal)"
    )
    assert RUNNER.index("install_modal_overlay_zorder(window, modal)") < RUNNER.index(
        "window.install_mode_workspace()"
    )


def test_overlay_lifecycle_bridge_compiles_without_importing_pyside() -> None:
    compile(BRIDGE, str(ROOT / "gui" / "modal_overlay_zorder.py"), "exec")
