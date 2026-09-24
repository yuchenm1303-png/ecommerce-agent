from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATURE = (ROOT / "gui" / "ui_maturity.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_maturity_install_order() -> None:
    assert "from gui.ui_maturity import install_mature_ui" in RUNNER
    assert RUNNER.index("install_ui_polish(window)") < RUNNER.index("install_card_details(window)")
    assert RUNNER.index("install_card_details(window)") < RUNNER.index("install_mature_ui(window)")
    assert RUNNER.index("install_mature_ui(window)") < RUNNER.index("install_native_window_shell(window, quick_window)")


def test_expand_button_safe_lane() -> None:
    assert '_EXPAND_SAFE_RIGHT = 38' in MATURE
    assert 'max(margins.right(), _EXPAND_SAFE_RIGHT)' in MATURE
    assert 'button.setText("⤢")' in MATURE
    assert 'button.setFixedSize(20, 20)' in MATURE
    assert 'parent.width() - 27' in MATURE


def test_console_phase_strip_is_two_line_and_safe() -> None:
    assert 'dict(getattr(console, "phase_units", {}))' in MATURE
    assert 'detail.hide()' in MATURE
    assert 'unit.setMinimumHeight(44)' in MATURE
    assert 'unit.setMaximumHeight(48)' in MATURE
    assert 'unit_layout.setContentsMargins(10, 5, _EXPAND_SAFE_RIGHT, 5)' in MATURE
    assert 'state_label.setText(f"{state} · {elapsed}" if elapsed else state)' in MATURE


def test_console_tabs_prioritize_real_viewport() -> None:
    assert 'tabs.setMinimumHeight(205)' in MATURE
    assert 'QSizePolicy.Policy.Expanding' in MATURE
    assert '("Console", "Timeline", "Artifacts", "Diagnostics", "Real Run")' in MATURE
    assert 'tabs.currentChanged.connect' in MATURE


def test_responsive_controller_is_coalesced_and_idempotent() -> None:
    assert 'self._timer.setSingleShot(True)' in MATURE
    assert 'self._timer.setInterval(32)' in MATURE
    assert 'def _set_splitter_sizes_if_needed' in MATURE
    assert 'any(abs(a - b) > 3 for a, b in zip(current, target))' in MATURE
    assert 'setInterval(16)' not in MATURE
    assert 'QApplication.instance().installEventFilter' not in MATURE


def test_expanded_console_gets_diagnostics_budget_without_destroying_workspace() -> None:
    assert 'side_target = 330 if width >= 1500 else 310' in MATURE
    assert 'side.setMinimumWidth(300)' in MATURE
    assert 'side.setMaximumWidth(370)' in MATURE
    assert 'target = min(440, max(340, available - 300))' in MATURE
    assert 'target = min(target, max(260, available - 260))' in MATURE
    assert 'console.setMaximumHeight(460)' in MATURE
    assert 'target = 116' in MATURE
    assert '[max(260, available - target), target]' in MATURE


def test_compact_tabs_and_tables() -> None:
    assert 'QTabWidget#sideDetailTabs QTabBar::tab:selected' in MATURE
    assert 'QFrame#acceptanceConsole QTabBar::tab:selected' in MATURE
    assert 'field_table.verticalHeader().setDefaultSectionSize(38)' in MATURE
    assert 'table.verticalHeader().setDefaultSectionSize(35)' in MATURE
