from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE_DETAILS = (ROOT / "gui" / "card_details.py").read_text(encoding="utf-8")
FAST_DETAILS = (ROOT / "gui" / "card_details_fast.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_card_details_are_installed_after_polish_and_reconciled_after_mature_layout() -> None:
    assert "from gui.card_details_fast import install_card_details" in RUNNER
    assert "install_ui_polish(window)" in RUNNER
    assert "details = install_card_details(window)" in RUNNER
    assert "mature = install_mature_ui(window)" in RUNNER
    assert "details.attach_mature(mature)" in RUNNER
    assert "install_native_window_shell(window, quick_window)" in RUNNER
    assert "install_nekro_card_fx(window, visual)" in RUNNER
    assert RUNNER.index("install_ui_polish(window)") < RUNNER.index("details = install_card_details(window)")
    assert RUNNER.index("details = install_card_details(window)") < RUNNER.index("mature = install_mature_ui(window)")
    assert RUNNER.index("details.attach_mature(mature)") < RUNNER.index(
        "install_native_window_shell(window, quick_window)"
    )


def test_every_presentation_card_family_is_discovered_for_details() -> None:
    assert '"glassCard"' in BASE_DETAILS
    assert '"heroCard"' in BASE_DETAILS
    assert '"statusCard"' in BASE_DETAILS
    assert '"microCard"' in BASE_DETAILS
    assert '"consolePhaseUnit"' in BASE_DETAILS
    assert "frame.installEventFilter(self)" in BASE_DETAILS


def test_legacy_expand_icon_is_removed_before_window_show() -> None:
    assert "self._expandable_cards = frozenset(self._installed_cards)" in FAST_DETAILS
    assert "for button in tuple(self._buttons.values())" in FAST_DETAILS
    assert "button.hide()" in FAST_DETAILS
    assert "button.setParent(None)" in FAST_DETAILS
    assert "button.deleteLater()" in FAST_DETAILS
    assert "self._buttons.clear()" in FAST_DETAILS
    assert RUNNER.index("details = install_card_details(window)") < RUNNER.index("shell.show()")


def test_card_surface_click_opens_detail_without_hijacking_child_controls() -> None:
    assert "QEvent.Type.MouseButtonRelease" in FAST_DETAILS
    assert "isinstance(event, QMouseEvent)" in FAST_DETAILS
    assert "event.button() == Qt.MouseButton.LeftButton" in FAST_DETAILS
    assert "self.open(watched)" in FAST_DETAILS
    # Only the expandable frame itself is filtered here. Interactive child
    # controls keep their native event handlers and do not receive an extra
    # controller-level filter.
    assert "child.installEventFilter(self)" not in FAST_DETAILS
    assert "widget.installEventFilter(self)" not in FAST_DETAILS


def test_legacy_expand_lane_is_reclaimed_after_mature_responsive_pass() -> None:
    assert "def attach_mature" in FAST_DETAILS
    assert "timer.timeout.connect(self._reclaim_expand_lane)" in FAST_DETAILS
    assert "def _reclaim_expand_lane" in FAST_DETAILS
    assert "if margins.right() >= 38" in FAST_DETAILS
    assert "layout.setContentsMargins" in FAST_DETAILS


def test_detail_drawer_is_atomic_and_has_no_animation_driver() -> None:
    assert "QPropertyAnimation" not in FAST_DETAILS
    assert "QParallelAnimationGroup" not in FAST_DETAILS
    assert "QEasingCurve" not in FAST_DETAILS
    assert "QAbstractAnimation" not in FAST_DETAILS
    assert "QGraphicsOpacityEffect" not in FAST_DETAILS
    assert "_DRAWER_OPEN_MS" not in FAST_DETAILS
    assert "_DRAWER_CLOSE_MS" not in FAST_DETAILS
    assert "_CONTENT_REVEAL_MS" not in FAST_DETAILS
    assert "_CARD_PULSE_PAD" not in FAST_DETAILS


def test_detail_open_populates_final_geometry_before_single_repaint() -> None:
    assert "updates_were_enabled = self.root.updatesEnabled()" in FAST_DETAILS
    assert "self.root.setUpdatesEnabled(False)" in FAST_DETAILS
    assert "self._populate(frame)" in FAST_DETAILS
    assert "self.drawer.setGeometry(self._drawer_rect())" in FAST_DETAILS
    assert "self.drawer.show()" in FAST_DETAILS
    assert "self.root.setUpdatesEnabled(True)" in FAST_DETAILS
    assert "self.root.update()" in FAST_DETAILS


def test_detail_close_is_immediate_without_exit_animation() -> None:
    assert "def close(self)" in FAST_DETAILS
    assert "self.drawer.hide()" in FAST_DETAILS
    assert "self.scrim.hide()" in FAST_DETAILS
    assert "end_pos" not in FAST_DETAILS
    assert "reveal_cover" not in FAST_DETAILS


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
