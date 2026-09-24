from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STABILITY = (ROOT / "gui" / "static_label_stability.py").read_text(encoding="utf-8")
GUI_INIT = (ROOT / "gui" / "__init__.py").read_text(encoding="utf-8")


def test_static_label_stability_is_installed_with_gui_extensions() -> None:
    assert "from .static_label_stability import install_static_label_stability" in GUI_INIT
    assert "install_static_label_stability()" in GUI_INIT


def test_quick_label_mirror_uses_stable_qobject_identity() -> None:
    assert "class _LiveLabelControl(QObject)" in STABILITY
    assert "changed = Signal()" in STABILITY
    assert "return live_label_control(self, widget, origin)" in STABILITY
    assert "bridge_cls._snapshot_widget = snapshot_widget" in STABILITY


def test_label_updates_do_not_require_card_control_replacement() -> None:
    assert "if next_state == self._state:" in STABILITY
    assert "self.changed.emit()" in STABILITY
    assert "cardControls" in STABILITY
    assert "phase-strip delegates stay alive" in STABILITY


def test_stale_live_label_controls_are_released() -> None:
    assert "self.window.findChildren(QLabel)" in STABILITY
    assert "pool.pop(identity, None)" in STABILITY
    assert "control.deleteLater()" in STABILITY
