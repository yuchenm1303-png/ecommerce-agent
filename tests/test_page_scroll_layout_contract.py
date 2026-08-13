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


def test_single_compacts_existing_input_rows_without_rebuilding_widgets() -> None:
    assert "def _compact_input_rows" in PAGE
    assert 'for name in ("makro_port", "source_port", "vertical_input", "current_page_check")' in PAGE
    assert "settings_row.removeWidget(widget)" in PAGE
    assert "stage_row.addWidget(widget)" in PAGE
    assert "_INPUT_CARD_MAX_HEIGHT = 176" in PAGE
    assert "layout.setContentsMargins(16, 8, 16, 9)" in PAGE
    assert "layout.setSpacing(4)" in PAGE

    assert "ReadOnlyRunner(" not in PAGE
    assert "RealExecutionRunner(" not in PAGE
    assert "AcceptanceConsole(" not in PAGE


def test_single_uses_compact_fixed_height_budgets() -> None:
    assert "_STATUS_CARD_MIN_HEIGHT = 68" in PAGE
    assert "_STATUS_CARD_MAX_HEIGHT = 72" in PAGE
    assert "_WORKSPACE_MIN_HEIGHT = 292" in PAGE
    assert "_FIELD_TABLE_MIN_HEIGHT = 205" in PAGE
    assert "_SIDE_TABS_HEIGHT = 300" in PAGE
    assert "_CONSOLE_MIN_HEIGHT = 120" in PAGE
    assert "_CONSOLE_MAX_HEIGHT = 136" in PAGE
    assert "_CONSOLE_TARGET_HEIGHT = 128" in PAGE

    assert "workspace.setMaximumHeight(16777215)" in PAGE
    assert "body.setStretchFactor(0, 1)" in PAGE
    assert "body.setStretchFactor(1, 0)" in PAGE


def test_fixed_single_has_no_per_scroll_quick_geometry_path() -> None:
    assert "valueChanged.connect" not in PAGE
    assert "sync_geometry" not in PAGE
    assert "render_mask" not in PAGE
    assert "install_single_scroll_glass_fastpath" not in PAGE
    assert "install_scroll_local_glass" not in PAGE
    assert "QTimer.singleShot(0, schedule)" in PAGE


def test_console_keeps_phase_summary_and_opens_full_detail_as_modal() -> None:
    assert "QScrollArea" not in SUMMARY
    assert 'self.body = getattr(window, "_ui_polish_body_splitter", None)' in SUMMARY
    assert "self.toggle.setCheckable(False)" in SUMMARY
    assert "unit.show()" in SUMMARY
    assert "tabs.hide()" in SUMMARY
    assert "_SUMMARY_MIN = 120" in SUMMARY
    assert "_SUMMARY_MAX = 136" in SUMMARY
    assert "_SUMMARY_TARGET = 128" in SUMMARY
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
