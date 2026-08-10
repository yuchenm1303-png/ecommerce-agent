from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DETAILS = (ROOT / "gui" / "card_details.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_card_details_are_installed_after_layout_before_native_focus_and_hover() -> None:
    assert "from gui.card_details import install_card_details" in RUNNER
    assert "install_ui_polish(window)" in RUNNER
    assert "install_card_details(window)" in RUNNER
    assert "install_native_window_shell(window, quick_window)" in RUNNER
    assert "install_nekro_card_fx(window, visual)" in RUNNER
    assert RUNNER.index("install_ui_polish(window)") < RUNNER.index("install_card_details(window)")
    assert RUNNER.index("install_card_details(window)") < RUNNER.index(
        "install_native_window_shell(window, quick_window)"
    )
    assert RUNNER.index("install_card_details(window)") < RUNNER.index(
        "install_nekro_card_fx(window, visual)"
    )


def test_every_presentation_card_family_gets_expand_affordance() -> None:
    assert '"glassCard"' in DETAILS
    assert '"heroCard"' in DETAILS
    assert '"statusCard"' in DETAILS
    assert '"microCard"' in DETAILS
    assert '"consolePhaseUnit"' in DETAILS
    assert 'button.setObjectName("cardExpandButton")' in DETAILS
    assert 'button.setText("↗")' in DETAILS
    assert "frame.installEventFilter(self)" in DETAILS


def test_open_animation_morphs_card_and_slides_fades_detail_drawer() -> None:
    assert 'b"geometry"' in DETAILS
    assert 'b"opacity"' in DETAILS
    assert "QParallelAnimationGroup" in DETAILS
    assert "QEasingCurve.Type.OutCubic" in DETAILS
    assert "self.ghost.setGeometry(source)" in DETAILS
    assert "self.drawer.setGeometry(drawer_start)" in DETAILS
    assert "self.drawer_effect.setOpacity(0.0)" in DETAILS
    assert "group.finished.connect(self._finish_open)" in DETAILS


def test_close_animation_is_full_reverse_transition() -> None:
    assert "QEasingCurve.Type.InOutCubic" in DETAILS
    assert "self.ghost.setGeometry(target)" in DETAILS
    assert "self.drawer_effect.opacity()" in DETAILS
    assert "group.finished.connect(self._finish_close)" in DETAILS
    assert "self.drawer.hide()" in DETAILS
    assert "self.scrim.hide()" in DETAILS


def test_details_are_useful_not_generic_placeholders() -> None:
    assert "def _populate_status" in DETAILS
    assert "accepted_rows=accepted" in DETAILS
    assert "def _clone_table" in DETAILS
    assert "def _populate_controls" in DETAILS
    assert "def _populate_text_views" in DETAILS
    assert "current_result" not in DETAILS
    assert "ReadOnlyRunner" not in DETAILS
    assert "RealExecutionRunner" not in DETAILS


def test_detail_animation_has_no_continuous_frame_timer_or_layout_reflow() -> None:
    assert "setInterval(16)" not in DETAILS
    assert "QApplication.instance().installEventFilter" not in DETAILS
    assert "setMinimumHeight" not in DETAILS.split("def open", 1)[1].split("def _finish_open", 1)[0]
    assert "setMaximumHeight" not in DETAILS.split("def open", 1)[1].split("def _finish_open", 1)[0]
    assert "frame.setGeometry" not in DETAILS
    assert "frame.resize" not in DETAILS


def test_escape_and_scrim_close_detail_page() -> None:
    assert "self.scrim.clicked.connect(self.close)" in DETAILS
    assert "event.key() == Qt.Key.Key_Escape" in DETAILS
    assert "self.close_button.clicked.connect(self.close)" in DETAILS
