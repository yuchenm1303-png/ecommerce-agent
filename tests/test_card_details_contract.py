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
    assert RUNNER.index("install_ui_polish(window)") < RUNNER.index("details = install_card_details(window)")
    assert RUNNER.index("details = install_card_details(window)") < RUNNER.index("mature = install_mature_ui(window)")
    assert RUNNER.index("details.attach_mature(mature)") < RUNNER.index("shell.show()")


def test_every_presentation_card_family_is_discovered_for_details() -> None:
    assert '"glassCard"' in BASE_DETAILS
    assert '"heroCard"' in BASE_DETAILS
    assert '"statusCard"' in BASE_DETAILS
    assert '"microCard"' in BASE_DETAILS
    assert '"consolePhaseUnit"' in BASE_DETAILS
    assert "frame.installEventFilter(self)" in BASE_DETAILS


def test_legacy_expand_icon_is_removed_before_window_show() -> None:
    assert "self._expandable_cards = tuple(self._installed_cards)" in FAST_DETAILS
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
    assert "child.installEventFilter(self)" not in FAST_DETAILS
    assert "widget.installEventFilter(self)" not in FAST_DETAILS


def test_shared_modal_uses_one_blurred_backdrop_and_centered_glass_panel() -> None:
    assert 'self.backdrop.setObjectName("cardDetailBackdrop")' in FAST_DETAILS
    assert "screen.grabWindow(" in FAST_DETAILS
    assert "QGraphicsBlurEffect" in FAST_DETAILS
    assert "_BACKDROP_SCALE = 0.34" in FAST_DETAILS
    assert "_BACKDROP_BLUR_RADIUS = 8.0" in FAST_DETAILS
    assert "background-color: rgba(220, 228, 238, 74)" in FAST_DETAILS
    assert "background-color: rgba(12, 17, 26, 94)" in FAST_DETAILS
    assert "(root.width() - width) // 2" in FAST_DETAILS
    assert "(root.height() - height) // 2" in FAST_DETAILS
    assert "_DEFAULT_RATIO = (0.80, 0.80)" in FAST_DETAILS


def test_modal_is_atomic_and_has_no_geometry_animation_driver() -> None:
    assert "QPropertyAnimation" not in FAST_DETAILS
    assert "QParallelAnimationGroup" not in FAST_DETAILS
    assert "QEasingCurve" not in FAST_DETAILS
    assert "QAbstractAnimation" not in FAST_DETAILS
    assert "QGraphicsOpacityEffect" not in FAST_DETAILS
    assert "_DRAWER_OPEN_MS" not in FAST_DETAILS
    assert "_DRAWER_CLOSE_MS" not in FAST_DETAILS
    assert "frame.setGeometry" not in FAST_DETAILS
    assert "frame.resize" not in FAST_DETAILS


def test_modal_open_settles_content_before_single_repaint() -> None:
    assert "updates_were_enabled = self.root.updatesEnabled()" in FAST_DETAILS
    assert "self.root.setUpdatesEnabled(False)" in FAST_DETAILS
    assert "self.drawer.setGeometry(self._drawer_rect())" in FAST_DETAILS
    assert "self.backdrop.show()" in FAST_DETAILS
    assert "self.scrim.show()" in FAST_DETAILS
    assert "self.drawer.show()" in FAST_DETAILS
    assert "self.root.setUpdatesEnabled(True)" in FAST_DETAILS
    assert "self.root.update()" in FAST_DETAILS


def test_modal_close_uses_explicit_hidden_state_when_parent_layer_is_hidden() -> None:
    close_body = FAST_DETAILS.split("def close(self) -> None:", 1)[1].split(
        "def _install_real_settings_action", 1
    )[0]
    assert "self.drawer.isHidden() and self.scrim.isHidden()" in close_body
    assert "not self.drawer.isVisible()" not in close_body
    assert "self.drawer.hide()" in close_body
    assert "self.scrim.hide()" in close_body


def test_real_settings_uses_shared_modal_instead_of_inline_visibility_toggle() -> None:
    assert "def _install_real_settings_action" in FAST_DETAILS
    assert "toggle.toggled.disconnect()" in FAST_DETAILS
    assert "toggle.setCheckable(False)" in FAST_DETAILS
    assert "toggle.clicked.connect(self.open_real_settings)" in FAST_DETAILS
    assert "def open_real_settings" in FAST_DETAILS
    assert 'title="真实填写设置"' in FAST_DETAILS
    assert "self.open_custom(" in FAST_DETAILS
    assert "widget.setVisible(expanded)" not in FAST_DETAILS


def test_console_detail_uses_the_same_modal_and_clones_all_tabs_read_only() -> None:
    assert "def open_console_details" in FAST_DETAILS
    assert 'title="运行控制台详情"' in FAST_DETAILS
    assert "_CONSOLE_RATIO = (0.90, 0.86)" in FAST_DETAILS
    assert "for index in range(tabs_source.count())" in FAST_DETAILS
    assert "self._clone_console_page(source_page)" in FAST_DETAILS
    assert "clone.setReadOnly(True)" in FAST_DETAILS


def test_legacy_expand_lane_is_reclaimed_after_mature_responsive_pass() -> None:
    assert "def attach_mature" in FAST_DETAILS
    assert "timer.timeout.connect(self._reclaim_expand_lane)" in FAST_DETAILS
    assert "if margins.right() >= 38" in FAST_DETAILS
    assert "layout.setContentsMargins" in FAST_DETAILS


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
    assert 'view.property("detailTitle")' in BASE_DETAILS
    assert 'table.property("detailTitle")' in BASE_DETAILS


def test_escape_scrim_and_close_button_all_close_the_shared_modal() -> None:
    assert "self.scrim.clicked.connect(self.close)" in BASE_DETAILS
    assert "event.key() == Qt.Key.Key_Escape" in FAST_DETAILS
    assert "self.close_button.clicked.connect(self.close)" in BASE_DETAILS
    assert "self.backdrop.hide()" in FAST_DETAILS
    assert "self.scrim.hide()" in FAST_DETAILS
    assert "self.drawer.hide()" in FAST_DETAILS