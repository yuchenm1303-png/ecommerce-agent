from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATURE = (ROOT / "gui" / "ui_maturity.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")

def test_maturity_pass_runs_after_detail_widgets_exist_before_native_shell() -> None:
    assert "from gui.ui_maturity import install_mature_ui" in RUNNER
    assert RUNNER.index("install_ui_polish(window)") < RUNNER.index("install_card_details(window)")
    assert RUNNER.index("install_card_details(window)") < RUNNER.index("install_mature_ui(window)")
    assert RUNNER.index("install_mature_ui(window)") < RUNNER.index("install_native_window_shell(window, quick_window)")

def test_every_expandable_card_reserves_a_real_button_lane() -> None:
    assert '_EXPAND_SAFE_RIGHT = 38' in MATURE
    assert 'frame.objectName() not in _EXPANDABLE_NAMES' in MATURE
    assert 'max(margins.right(), _EXPAND_SAFE_RIGHT)' in MATURE
    assert 'button.setText("⤢")' in MATURE
    assert 'button.setFixedSize(20, 20)' in MATURE
    assert 'parent.width() - 27' in MATURE

def test_console_phase_cards_cannot_overlap_state_text_and_expand_affordance() -> None:
    assert 'phase_units = list(getattr(console, "phase_units", {}).values())' in MATURE
    assert 'unit.setMinimumHeight(56)' in MATURE
    assert 'unit.setMaximumHeight(62)' in MATURE
    assert 'unit_layout.setContentsMargins(10, 6, _EXPAND_SAFE_RIGHT, 6)' in MATURE

def test_responsive_controller_coalesces_and_is_idempotent() -> None:
    assert 'class MatureResponsiveController(QObject)' in MATURE
    assert 'self._timer.setSingleShot(True)' in MATURE
    assert 'self._timer.setInterval(32)' in MATURE
    assert 'def _set_splitter_sizes_if_needed' in MATURE
    assert 'any(abs(a - b) > 3 for a, b in zip(current, target))' in MATURE
    assert 'QEvent.Type.Resize' in MATURE
    assert 'QEvent.Type.LayoutRequest' in MATURE
    assert 'setInterval(16)' not in MATURE
    assert 'QApplication.instance().installEventFilter' not in MATURE

def test_workspace_stays_primary_even_when_console_details_are_expanded() -> None:
    assert 'side_target = 330 if width >= 1500 else 310' in MATURE
    assert 'side.setMinimumWidth(300)' in MATURE
    assert 'side.setMaximumWidth(370)' in MATURE
    assert '0.34 if height >= 980 else 0.31' in MATURE
    assert 'target = min(350, max(260, target))' in MATURE
    assert 'target = 116' in MATURE

def test_tabs_and_tables_share_one_compact_visual_hierarchy() -> None:
    assert 'QTabWidget#sideDetailTabs QTabBar::tab:selected' in MATURE
    assert 'QFrame#acceptanceConsole QTabBar::tab:selected' in MATURE
    assert 'field_table.verticalHeader().setDefaultSectionSize(38)' in MATURE
    assert 'table.verticalHeader().setDefaultSectionSize(35)' in MATURE
