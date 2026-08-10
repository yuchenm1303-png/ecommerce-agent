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


def test_fast_drawer_uses_qt_property_animation_not_python_8ms_loop() -> None:
    assert "QPropertyAnimation" in FAST_DETAILS
    assert "QParallelAnimationGroup" in FAST_DETAILS
    assert 'b"pos"' in FAST_DETAILS
    assert 'b"geometry"' in FAST_DETAILS
    assert "_DRAWER_OPEN_MS = 138" in FAST_DETAILS
    assert "_DRAWER_CLOSE_MS = 126" in FAST_DETAILS
    assert "PreciseTimer" not in FAST_DETAILS
    assert "_motion_timer" not in FAST_DETAILS
    assert "def _tick_motion" not in FAST_DETAILS
    assert "time.perf_counter" not in FAST_DETAILS
    assert "self.drawer.setGraphicsEffect(None)" in FAST_DETAILS


def test_source_card_feedback_stays_small_instead_of_resizing_to_drawer() -> None:
    assert "_CARD_PULSE_PAD = 4" in FAST_DETAILS
    assert "self._source_rect.adjusted" in FAST_DETAILS
    assert "self.ghost_effect" in FAST_DETAILS
    assert "ghost_geometry.setKeyValueAt" in FAST_DETAILS


def test_detail_body_has_separate_qt_animated_reveal_instead_of_popping() -> None:
    assert 'self.reveal_cover.setObjectName("cardDetailRevealCover")' in FAST_DETAILS
    assert "def _start_content_reveal" in FAST_DETAILS
    assert "_CONTENT_REVEAL_MS = 104" in FAST_DETAILS
    assert "self._populate(frame)" in FAST_DETAILS
    assert 'self.reveal_cover,\n                b"geometry"' in FAST_DETAILS


def test_close_animation_covers_complex_body_while_sliding_out() -> None:
    assert "_DRAWER_CLOSE_MS = 126" in FAST_DETAILS
    assert "end_pos" in FAST_DETAILS
    assert "self.reveal_cover" in FAST_DETAILS
    assert "self.drawer.hide()" in FAST_DETAILS
    assert "self.scrim.hide()" in FAST_DETAILS


def test_detail_geometry_notifications_are_coalesced() -> None:
    assert "_GEOMETRY_COALESCE_MS = 32" in FAST_DETAILS
    assert "self._geometry_timer.setSingleShot(True)" in FAST_DETAILS
    assert "def _schedule_geometry" in FAST_DETAILS
    assert "QTimer.singleShot(0, lambda card=" not in FAST_DETAILS


def test_details_are_useful_not_generic_placeholders() -> None:
    assert "def _populate_status" in BASE_DETAILS
    assert "accepted_rows=accepted" in BASE_DETAILS
    assert "def _clone_table" in BASE_DETAILS
    assert "def _populate_controls" in BASE_DETAILS
    assert "def _populate_text_views" in BASE_DETAILS
    assert "current_result" not in BASE_DETAILS
    assert "ReadOnlyRunner" not in BASE_DETAILS
    assert "RealExecutionRunner" not in BASE_DETAILS
    assert 'view.property("detailTitle")' in BASE_DETAILS
    assert 'table.property("detailTitle")' in BASE_DETAILS


def test_detail_motion_has_no_layout_height_animation_or_global_filter() -> None:
    assert "QApplication.instance().installEventFilter" not in FAST_DETAILS
    assert "setMinimumHeight" not in FAST_DETAILS
    assert "setMaximumHeight" not in FAST_DETAILS
    assert "frame.setGeometry" not in FAST_DETAILS
    assert "frame.resize" not in FAST_DETAILS


def test_escape_and_scrim_close_detail_page() -> None:
    assert "self.scrim.clicked.connect(self.close)" in BASE_DETAILS
    assert "event.key() == Qt.Key.Key_Escape" in BASE_DETAILS
    assert "self.close_button.clicked.connect(self.close)" in BASE_DETAILS
