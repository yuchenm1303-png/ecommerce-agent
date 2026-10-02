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


def test_maturity_is_polish_only_and_owns_no_geometry() -> None:
    # workspace_composer.py is the single page-layout owner. The maturity layer
    # must not size splitters, reserve card lanes or react to resize events.
    assert "_EXPAND_SAFE_RIGHT" not in MATURE
    assert "cardExpandButton" not in MATURE
    assert "QSplitter" not in MATURE
    assert "setSizes" not in MATURE
    assert "installEventFilter" not in MATURE
    assert "class MatureResponsiveController" not in MATURE
    assert "def install_mature_ui(window: QMainWindow) -> None:" in MATURE


def test_console_phase_strip_is_two_line_and_safe() -> None:
    assert 'dict(getattr(console, "phase_units", {}))' in MATURE
    assert 'detail.hide()' in MATURE
    assert 'unit.setMinimumHeight(44)' in MATURE
    assert 'unit.setMaximumHeight(48)' in MATURE
    assert 'unit_layout.setContentsMargins(10, 5, 10, 5)' in MATURE
    assert 'state_label.setText(f"{state} · {elapsed}" if elapsed else state)' in MATURE


def test_console_tabs_prioritize_real_viewport() -> None:
    assert 'tabs.setMinimumHeight(205)' in MATURE
    assert 'QSizePolicy.Policy.Expanding' in MATURE
    assert '("Console", "Timeline", "Artifacts", "Diagnostics", "Real Run")' in MATURE


def test_compact_tabs_and_tables() -> None:
    assert 'QTabWidget#sideDetailTabs QTabBar::tab:selected' in MATURE
    assert 'QFrame#acceptanceConsole QTabBar::tab:selected' in MATURE
    assert 'field_table.verticalHeader().setDefaultSectionSize(38)' in MATURE
    assert 'table.verticalHeader().setDefaultSectionSize(35)' in MATURE
