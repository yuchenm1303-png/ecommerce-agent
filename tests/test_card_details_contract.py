from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE_DETAILS = (ROOT / "gui" / "card_details.py").read_text(encoding="utf-8")
FAST_DETAILS = (ROOT / "gui" / "card_details_fast.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_card_details_are_installed_after_layout_before_native_focus_and_hover() -> None:
    assert "from gui.card_details_fast import install_card_details" in RUNNER
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
    assert '"glassCard"' in BASE_DETAILS
    assert '"heroCard"' in BASE_DETAILS
    assert '"statusCard"' in BASE_DETAILS
    assert '"microCard"' in BASE_DETAILS
    assert '"consolePhaseUnit"' in BASE_DETAILS
    assert 'button.setObjectName("cardExpandButton")' in BASE_DETAILS
    assert 'button.setText("↗")' in BASE_DETAILS
    assert "frame.installEventFilter(self)" in BASE_DETAILS


def test_fast_drawer_uses_short_lived_high_refresh_position_only_motion() -> None:
    assert "_MOTION_INTERVAL_MS = 8" in FAST_DETAILS
    assert "Qt.TimerType.PreciseTimer" in FAST_DETAILS
    assert 'self._motion_timer.setInterval(_MOTION_INTERVAL_MS)' in FAST_DETAILS
    assert 'self._start_motion("opening")' in FAST_DETAILS
    assert 'self.drawer.move(' in FAST_DETAILS
    assert 'self.drawer.setGraphicsEffect(None)' in FAST_DETAILS
    assert 'b"geometry"' not in FAST_DETAILS
    assert "QParallelAnimationGroup" not in FAST_DETAILS
    assert "QPropertyAnimation" not in FAST_DETAILS


def test_source_card_feedback_stays_small_instead_of_resizing_to_drawer() -> None:
    assert "_CARD_PULSE_PAD = 5" in FAST_DETAILS
    assert "self._source_rect.adjusted(-pad, -pad, pad, pad)" in FAST_DETAILS
    assert "self.ghost_effect.setOpacity" in FAST_DETAILS
    assert "source,\n                target" not in FAST_DETAILS


def test_detail_body_has_separate_animated_reveal_instead_of_popping() -> None:
    assert 'self.reveal_cover.setObjectName("cardDetailRevealCover")' in FAST_DETAILS
    assert "def _set_reveal_progress" in FAST_DETAILS
    assert "def _start_content_reveal" in FAST_DETAILS
    assert 'self._start_motion("revealing")' in FAST_DETAILS
    assert "_CONTENT_REVEAL_MS = 108" in FAST_DETAILS
    assert "self._populate(frame)" in FAST_DETAILS
    assert FAST_DETAILS.index("self.drawer.setGeometry(target)") < FAST_DETAILS.index("self._populate(frame)")


def test_close_animation_covers_complex_body_while_sliding_out() -> None:
    assert 'self._start_motion("closing")' in FAST_DETAILS
    assert "_DRAWER_CLOSE_MS = 128" in FAST_DETAILS
    assert "self._set_reveal_progress(1.0 - cover)" in FAST_DETAILS
    assert "self.drawer.hide()" in FAST_DETAILS
    assert "self.scrim.hide()" in FAST_DETAILS


def test_details_are_useful_not_generic_placeholders() -> None:
    assert "def _populate_status" in BASE_DETAILS
    assert "accepted_rows=accepted" in BASE_DETAILS
    assert "def _clone_table" in BASE_DETAILS
    assert "def _populate_controls" in BASE_DETAILS
    assert "def _populate_text_views" in BASE_DETAILS
    assert "current_result" not in BASE_DETAILS
    assert "ReadOnlyRunner" not in BASE_DETAILS
    assert "RealExecutionRunner" not in BASE_DETAILS


def test_detail_motion_has_no_continuous_idle_timer_or_layout_height_animation() -> None:
    assert "self._motion_timer.start()" in FAST_DETAILS
    assert "self._motion_timer.stop()" in FAST_DETAILS
    assert "setInterval(16)" not in FAST_DETAILS
    assert "QApplication.instance().installEventFilter" not in FAST_DETAILS
    assert "setMinimumHeight" not in FAST_DETAILS
    assert "setMaximumHeight" not in FAST_DETAILS
    assert "frame.setGeometry" not in FAST_DETAILS
    assert "frame.resize" not in FAST_DETAILS


def test_escape_and_scrim_close_detail_page() -> None:
    assert "self.scrim.clicked.connect(self.close)" in BASE_DETAILS
    assert "event.key() == Qt.Key.Key_Escape" in BASE_DETAILS
    assert "self.close_button.clicked.connect(self.close)" in BASE_DETAILS
