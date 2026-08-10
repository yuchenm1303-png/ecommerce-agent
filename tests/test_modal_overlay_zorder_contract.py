from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = (ROOT / "gui" / "modal_overlay_zorder.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_overlay_is_only_above_qwidget_during_quick_transition() -> None:
    assert 'getattr(item, "commandChanged", None)' in BRIDGE
    assert 'getattr(item, "transitionFinished", None)' in BRIDGE
    assert "command_changed.connect(self._raise_for_transition)" in BRIDGE
    assert "transition_finished.connect(self._lower_after_transition)" in BRIDGE
    assert "surface.raise_()" in BRIDGE
    assert BRIDGE.count("surface.lower()") >= 2
    assert ".show()" not in BRIDGE
    assert ".hide()" not in BRIDGE


def test_idle_overlay_is_lowered_after_modal_surface_prime() -> None:
    bind = BRIDGE.split("def _bind_after_modal_prime", 1)[1].split(
        "def _raise_for_transition", 1
    )[0]
    assert "surface.lower()" in bind
    assert "_BIND_RETRIES = 4" in BRIDGE
    assert "self._bind_attempts += 1" in bind
    assert "self._bind_attempts < _BIND_RETRIES" in bind
    assert "QTimer.singleShot(0, self._bind_after_modal_prime)" in BRIDGE


def test_runner_installs_zorder_policy_immediately_after_modal_controller() -> None:
    assert "from gui.modal_overlay_zorder import install_modal_overlay_zorder" in RUNNER
    assert "modal = install_modal_interaction(window, details)" in RUNNER
    assert "install_modal_overlay_zorder(window, modal)" in RUNNER
    assert RUNNER.index("modal = install_modal_interaction(window, details)") < RUNNER.index(
        "install_modal_overlay_zorder(window, modal)"
    )
    assert RUNNER.index("install_modal_overlay_zorder(window, modal)") < RUNNER.index(
        "window.install_mode_workspace()"
    )


def test_zorder_bridge_compiles_without_importing_pyside() -> None:
    compile(BRIDGE, str(ROOT / "gui" / "modal_overlay_zorder.py"), "exec")
