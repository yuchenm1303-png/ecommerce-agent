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


def test_runtime_detail_drawer_uses_shared_clip_sheet_driver() -> None:
    assert "from .overlay_sheet_motion import ClipSheetMotion" in FAST_DETAILS
    assert "self._motion = ClipSheetMotion" in FAST_DETAILS
    assert 'edge="right"' in FAST_DETAILS
    assert "duration_ms=158" in FAST_DETAILS
    assert "QPropertyAnimation" not in FAST_DETAILS
    assert "QParallelAnimationGroup" not in FAST_DETAILS
    assert "QEasingCurve" not in FAST_DETAILS
    assert "QGraphicsOpacityEffect" not in FAST_DETAILS


def test_detail_body_is_populated_at_final_size_before_reveal() -> None:
    assert "self._populate(frame)" in FAST_DETAILS
    assert "self.body_layout.activate()" in FAST_DETAILS
    assert "self.drawer.layout().activate()" in FAST_DETAILS
    assert "self._motion.open()" in FAST_DETAILS
    assert FAST_DETAILS.index("self._populate(frame)") < FAST_DETAILS.index("self._motion.open()")


def test_detail_close_only_reverses_clip_viewport() -> None:
    assert "def close(self)" in FAST_DETAILS
    assert "self._motion.close()" in FAST_DETAILS
    assert "self.scrim.hide()" in FAST_DETAILS
    assert "drawer.setGeometry" not in FAST_DETAILS
    assert "drawer.resize" not in FAST_DETAILS


def test_detail_geometry_notifications_are_coalesced() -> None:
    assert "_GEOMETRY_COALESCE_MS = 32" in FAST_DETAILS
    assert "self._geometry_timer.setSingleShot(True)" in FAST_DETAILS
    assert "def _schedule_geometry" in FAST_DETAILS
    assert "self._motion.sync_geometry()" in FAST_DETAILS


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


def test_detail_path_has_no_layout_height_animation_or_global_filter() -> None:
    assert "QApplication.instance().installEventFilter" not in FAST_DETAILS
    assert "setMinimumHeight" not in FAST_DETAILS
    assert "setMaximumHeight" not in FAST_DETAILS
    assert "frame.setGeometry" not in FAST_DETAILS
    assert "frame.resize" not in FAST_DETAILS


def test_escape_and_scrim_close_detail_page() -> None:
    assert "self.scrim.clicked.connect(self.close)" in BASE_DETAILS
    assert "event.key() == Qt.Key.Key_Escape" in FAST_DETAILS
    assert "self.close_button.clicked.connect(self.close)" in BASE_DETAILS
