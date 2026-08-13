from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
PAGE = (ROOT / "gui" / "page_scroll_layout.py").read_text(encoding="utf-8")
SMOOTH = (ROOT / "gui" / "smooth_scroll.py").read_text(encoding="utf-8")
SUMMARY = (ROOT / "gui" / "console_summary_mode.py").read_text(encoding="utf-8")


def test_formal_single_installs_fixed_layout_after_maturity_before_summary_and_modes() -> None:
    assert "from gui.page_scroll_layout import install_page_scroll_layout" in RUN
    assert "install_ui_polish(window)" in RUN
    assert "install_mature_ui(window)" in RUN
    assert "install_page_scroll_layout(window, visual)" in RUN
    assert "install_console_summary_mode(window)" in RUN
    assert "window.install_mode_workspace()" in RUN

    assert RUN.index("install_ui_polish(window)") < RUN.index("install_mature_ui(window)")
    assert RUN.index("install_mature_ui(window)") < RUN.index("install_page_scroll_layout(window, visual)")
    assert RUN.index("install_page_scroll_layout(window, visual)") < RUN.index("install_console_summary_mode(window)")
    assert RUN.index("install_console_summary_mode(window)") < RUN.index("window.install_mode_workspace()")


def test_single_keeps_original_root_ownership_and_has_no_outer_scroll_page() -> None:
    assert "QScrollArea" not in PAGE
    assert 'setObjectName("singlePageScroll")' not in PAGE
    assert "scroll.setWidget" not in PAGE
    assert "_single_page_scroll" not in PAGE
    assert "setParent(page)" not in PAGE
    assert "outer.addWidget(scroll" not in PAGE

    assert 'body = getattr(window, "_ui_polish_body_splitter", None)' in PAGE
    assert 'setattr(window, "_single_fixed_body", body)' in PAGE


def test_single_gives_source_controls_distinct_vertical_bands() -> None:
    assert "def _compact_input_rows" in PAGE
    assert 'stage_anchor = getattr(window, "step1_button", None)' in PAGE
    assert 'settings_anchor = getattr(window, "source_port", None)' in PAGE
    assert "settings_row.removeWidget" not in PAGE
    assert "stage_row.addWidget(widget)" not in PAGE
    assert "stage_row.setSpacing(10)" in PAGE
    assert "stage_row.setContentsMargins(0, 1, 0, 1)" in PAGE
    assert "settings_row.setSpacing(10)" in PAGE
    assert "settings_row.setContentsMargins(0, 2, 0, 1)" in PAGE
    assert "_INPUT_CARD_MIN_HEIGHT = 222" in PAGE
    assert "_INPUT_CARD_MAX_HEIGHT = 238" in PAGE
    assert "layout.setContentsMargins(14, 9, 14, 10)" in PAGE
    assert "layout.setSpacing(7)" in PAGE

    assert "ReadOnlyRunner(" not in PAGE
    assert "RealExecutionRunner(" not in PAGE
    assert "AcceptanceConsole(" not in PAGE


def test_single_aligns_field_glass_bottom_with_right_panel_without_rebuilding_table() -> None:
    assert "_FIELD_CARD_BOTTOM_INSET = 28" in PAGE
    assert "def _align_field_card_to_side_panel" in PAGE
    assert 'host.setObjectName("fieldReviewHost")' in PAGE
    assert "host_layout.setContentsMargins(0, 0, 0, _FIELD_CARD_BOTTOM_INSET)" in PAGE
    assert "field_card.setParent(host)" in PAGE
    assert "host_layout.addWidget(field_card, 1)" in PAGE
    assert "workspace_splitter.insertWidget(index, host)" in PAGE
    assert 'setattr(window, "_single_field_alignment_host", host)' in PAGE
    assert "_align_field_card_to_side_panel(window, workspace_splitter)" in PAGE


def test_single_uses_console_first_one_screen_height_budget() -> None:
    assert "_STATUS_CARD_MIN_HEIGHT = 54" in PAGE
    assert "_STATUS_CARD_MAX_HEIGHT = 58" in PAGE
    assert "_WORKSPACE_MIN_HEIGHT = 220" in PAGE
    assert "_FIELD_TABLE_MIN_HEIGHT = 136" in PAGE
    assert "_SIDE_MIN_WIDTH = 360" in PAGE
    assert "_SIDE_MAX_WIDTH = 480" in PAGE
    assert "_SIDE_TARGET_RATIO = 0.29" in PAGE
    assert "_CONSOLE_MIN_HEIGHT = 292" in PAGE
    assert "_CONSOLE_MAX_HEIGHT = 336" in PAGE
    assert "_CONSOLE_TARGET_HEIGHT = 310" in PAGE

    assert "def _compact_workspace_cards" in PAGE
    assert "layout.setContentsMargins(14, 8, 14, 10)" in PAGE
    assert "layout.setContentsMargins(14, 9, 14, 10)" in PAGE
    assert "workspace.setMaximumHeight(16777215)" in PAGE
    assert "side_tabs.setMaximumHeight(16777215)" in PAGE
    assert "side_tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)" in PAGE
    assert "_strip_trailing_spacers(side_layout)" in PAGE
    assert "body.setStretchFactor(0, 1)" in PAGE
    assert "body.setStretchFactor(1, 0)" in PAGE


def test_fixed_single_sleeps_old_mature_geometry_owner() -> None:
    assert "from types import MethodType" in SUMMARY
    assert "_COALESCE_MS = 16" in SUMMARY
    assert "def _install_mature_single_fast_path" in SUMMARY
    assert "timer.stop()" in SUMMARY
    assert "timer.timeout.disconnect()" in SUMMARY
    assert "timer.timeout.connect(self._dispatch_mature_apply)" in SUMMARY
    assert "mature.schedule = MethodType(fixed_aware_schedule, mature)" in SUMMARY
    assert "if controller._single_active():" in SUMMARY
    assert "original_schedule()" in SUMMARY
    assert "if self._single_active():" in SUMMARY
    assert "self._mature_original_apply()" in SUMMARY
    assert "_apply_after_mature" not in SUMMARY


def test_fixed_single_only_recomputes_for_real_geometry_changes() -> None:
    assert "def _geometry_signature" in SUMMARY
    assert "if signature == self._last_geometry_signature:" in SUMMARY
    assert "self.root.installEventFilter(self)" in SUMMARY
    assert "self.body.installEventFilter(self)" in SUMMARY

    event = SUMMARY.split("def eventFilter", 1)[1].split("def cleanup", 1)[0]
    assert "QEvent.Type.Resize" in event
    assert "QEvent.Type.Show" in event
    assert "QEvent.Type.LayoutRequest" not in event

    apply_body = SUMMARY.split("def apply(self)", 1)[1].split("def _open_detail", 1)[0]
    assert "background.schedule_mask_update" not in apply_body
    assert "_schedule_mask_after_geometry" not in SUMMARY
    assert "self._apply_workspace_width()" in apply_body
    assert "self._apply_body_height()" in apply_body


def test_fixed_single_restores_mature_responsive_behavior_for_batch() -> None:
    assert "def _on_mode_changed" in SUMMARY
    mode = SUMMARY.split("def _on_mode_changed", 1)[1].split(
        "def _install_mature_single_fast_path", 1
    )[0]
    assert "if int(index) == 0:" in mode
    assert "self.schedule()" in mode
    assert "self._mature_original_schedule()" in mode

    dispatch = SUMMARY.split("def _dispatch_mature_apply", 1)[1].split(
        "@staticmethod", 1
    )[0]
    assert "if self._single_active():" in dispatch
    assert "self._mature_original_apply()" in dispatch


def test_fixed_single_has_no_per_scroll_quick_geometry_path() -> None:
    assert "valueChanged.connect" not in PAGE
    assert "sync_geometry" not in PAGE
    assert "render_mask" not in PAGE
    assert "install_single_scroll_glass_fastpath" not in PAGE
    assert "install_scroll_local_glass" not in PAGE
    assert "QTimer.singleShot(0, schedule)" in PAGE


def test_console_gets_real_working_space_without_changing_idle_hot_path() -> None:
    assert "QScrollArea" not in SUMMARY
    assert 'self.body = getattr(window, "_ui_polish_body_splitter", None)' in SUMMARY
    assert "self.toggle.setCheckable(False)" in SUMMARY
    assert "unit.show()" in SUMMARY
    assert "tabs.show()" in SUMMARY
    assert "tabs.hide()" not in SUMMARY
    assert "_SUMMARY_MIN = 292" in SUMMARY
    assert "_SUMMARY_MAX = 336" in SUMMARY
    assert "_SUMMARY_TARGET = 310" in SUMMARY
    assert "_WORKSPACE_MIN = 220" in SUMMARY
    assert "_CONSOLE_TABS_MIN = 132" in SUMMARY
    assert "_CONSOLE_TABS_MAX = 176" in SUMMARY
    assert "proportional = round(available * 0.52)" in SUMMARY
    assert "self.details.open_console_details()" in SUMMARY


def test_nested_internal_scroll_still_uses_continuous_wheel_behavior() -> None:
    assert "def _can_move" in SMOOTH
    assert "def _parent_scroll_area" in SMOOTH
    assert "def _scroll_owner" in SMOOTH
    owner = SMOOTH.split("def _scroll_owner", 1)[1].split("def eventFilter", 1)[0]
    assert "while current is not None:" in owner
    assert "if self._can_move(current, scroll_delta):" in owner
    assert "current = self._parent_scroll_area(current)" in owner

    event = SMOOTH.split("def eventFilter", 1)[1].split("def cleanup", 1)[0]
    assert "owner = self._scroll_owner(area, scroll_delta)" in event
    assert "self._scroller.scroll_pixels(owner.verticalScrollBar(), scroll_delta)" in event
    assert "self._scroller.add_wheel_delta(owner.verticalScrollBar(), notch_delta)" in event


def test_layout_sources_compile_without_importing_pyside() -> None:
    for path, source in (
        (ROOT / "run_local_gui.py", RUN),
        (ROOT / "gui" / "page_scroll_layout.py", PAGE),
        (ROOT / "gui" / "smooth_scroll.py", SMOOTH),
        (ROOT / "gui" / "console_summary_mode.py", SUMMARY),
    ):
        compile(source, str(path), "exec")
