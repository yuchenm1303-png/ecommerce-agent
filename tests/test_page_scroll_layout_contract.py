from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
PAGE = (ROOT / "gui" / "page_scroll_layout.py").read_text(encoding="utf-8")
FAST = (ROOT / "gui" / "single_scroll_glass_fastpath.py").read_text(encoding="utf-8")
SMOOTH = (ROOT / "gui" / "smooth_scroll.py").read_text(encoding="utf-8")
SUMMARY = (ROOT / "gui" / "console_summary_mode.py").read_text(encoding="utf-8")


def test_formal_single_installs_final_page_scroll_after_maturity_before_summary_and_modes() -> None:
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


def test_header_stays_fixed_while_single_body_becomes_one_scroll_page() -> None:
    assert 'scroll.setObjectName("singlePageScroll")' in PAGE
    assert "scroll.setWidgetResizable(True)" in PAGE
    assert "ScrollBarAlwaysOff" in PAGE
    assert "ScrollBarAsNeeded" in PAGE
    assert "outer.addWidget(scroll, 1)" in PAGE

    assert "_take_widget(outer, input_card)" in PAGE
    assert "_take_layout(outer, status_layout)" in PAGE
    assert "_take_widget(outer, body)" in PAGE
    assert "outer.takeAt(0)" not in PAGE


def test_existing_business_widgets_are_reparented_not_rebuilt() -> None:
    assert "input_card.setParent(page)" in PAGE
    assert "workspace.setParent(page)" in PAGE
    assert "console.setParent(page)" in PAGE
    assert "page_layout.addWidget(input_card)" in PAGE
    assert "page_layout.addWidget(workspace)" in PAGE
    assert "page_layout.addWidget(console)" in PAGE
    assert 'setattr(window, "_ui_polish_body_splitter", None)' in PAGE

    assert "ReadOnlyRunner(" not in PAGE
    assert "RealExecutionRunner(" not in PAGE
    assert "AcceptanceConsole(" not in PAGE


def test_workspace_is_compact_while_console_keeps_real_reading_height() -> None:
    assert "_WORKSPACE_HEIGHT = 350" in PAGE
    assert "_FIELD_TABLE_MIN_HEIGHT = 255" in PAGE
    assert "_SIDE_TABS_HEIGHT = 320" in PAGE
    assert "_CONSOLE_MIN_HEIGHT = 420" in PAGE
    assert "_CONSOLE_TABS_MIN_HEIGHT = 250" in PAGE
    assert "_CONSOLE_LOG_MIN_HEIGHT = 180" in PAGE

    assert "workspace.setMinimumHeight(_WORKSPACE_HEIGHT)" in PAGE
    assert "workspace.setMaximumHeight(_WORKSPACE_HEIGHT)" in PAGE
    assert "field_table.setMinimumHeight(_FIELD_TABLE_MIN_HEIGHT)" in PAGE
    assert "side_tabs.setMinimumHeight(_SIDE_TABS_HEIGHT)" in PAGE
    assert "side_tabs.setMaximumHeight(_SIDE_TABS_HEIGHT)" in PAGE
    assert "console.setMinimumHeight(_CONSOLE_MIN_HEIGHT)" in PAGE
    assert "tabs.setMinimumHeight(_CONSOLE_TABS_MIN_HEIGHT)" in PAGE
    assert "log_view.setMinimumHeight(_CONSOLE_LOG_MIN_HEIGHT)" in PAGE


def test_side_diagnostics_removes_legacy_bottom_clearance() -> None:
    assert "side_layout = side_host.layout()" in PAGE
    assert "side_layout.setContentsMargins(margins.left(), margins.top(), margins.right(), 0)" in PAGE
    assert "visibly empty column under Telemetry" in PAGE


def test_page_scroll_uses_cached_fastpath_not_per_tick_widget_geometry_scan() -> None:
    assert "install_single_scroll_glass_fastpath" in PAGE
    assert "install_single_scroll_glass_fastpath(window, resolved_visual, scroll, page)" in PAGE
    assert "def sync_scroll_glass" not in PAGE
    assert "sync_geometry" not in PAGE
    assert "schedule_mask" not in PAGE
    assert "QTimer.singleShot(0, sync_scroll_glass)" not in PAGE

    hot = FAST.split("def _on_scroll", 1)[1].split("def _on_scroll_range_changed", 1)[0]
    assert "_apply_cached_scroll" in hot
    assert "mapTo(" not in hot
    assert "sync_geometry" not in hot
    assert "schedule_mask" not in hot


def test_nested_wheel_scroll_chains_outward_at_inner_boundaries() -> None:
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


def test_wheel_scrolling_uses_one_continuous_damped_target_for_discrete_notches() -> None:
    assert "event.pixelDelta().y()" in SMOOTH
    assert "event.angleDelta().y()" in SMOOTH
    assert "_WHEEL_TRAVEL_PX" in SMOOTH
    assert "_SPRING_OMEGA" in SMOOTH
    assert "_ScrollMotion" in SMOOTH
    assert "position: float" in SMOOTH
    assert "target: float" in SMOOTH
    assert "velocity: float" in SMOOTH
    assert "motion.target = self._clamp_position" in SMOOTH
    assert "c2 = motion.velocity + omega * offset" in SMOOTH
    assert "next_offset = (offset + c2 * dt) * decay" in SMOOTH
    assert "ScrollPerPixel" in SMOOTH
    assert "push_impulse" not in SMOOTH
    assert "_WHEEL_IMPULSE_PX_S" not in SMOOTH
    assert "round(delta / 120" not in SMOOTH


def test_console_summary_has_native_page_scroll_mode_and_keeps_detail_modal() -> None:
    assert 'self.page_scroll = getattr(window, "_single_page_scroll", None)' in SUMMARY
    assert "if isinstance(self.page_scroll, QScrollArea):" in SUMMARY
    assert "self.console.setMinimumHeight(_PAGE_SUMMARY_MIN)" in SUMMARY
    assert "self.console.setMaximumHeight(_PAGE_SUMMARY_MAX)" in SUMMARY
    assert "self.details.open_console_details()" in SUMMARY


def test_scroll_layout_sources_compile_without_importing_pyside() -> None:
    for path, source in (
        (ROOT / "run_local_gui.py", RUN),
        (ROOT / "gui" / "page_scroll_layout.py", PAGE),
        (ROOT / "gui" / "single_scroll_glass_fastpath.py", FAST),
        (ROOT / "gui" / "smooth_scroll.py", SMOOTH),
        (ROOT / "gui" / "console_summary_mode.py", SUMMARY),
    ):
        compile(source, str(path), "exec")
