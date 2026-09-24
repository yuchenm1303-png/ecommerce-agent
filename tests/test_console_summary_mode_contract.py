from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODE = (ROOT / "gui" / "console_summary_mode.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_console_summary_mode_is_installed_after_mature_layout() -> None:
    assert "from gui.console_summary_mode import install_console_summary_mode" in RUNNER
    assert "install_mature_ui(window)" in RUNNER
    assert "install_console_summary_mode(window)" in RUNNER
    assert RUNNER.index("install_mature_ui(window)") < RUNNER.index(
        "install_console_summary_mode(window)"
    )


def test_indicator_moves_phase_cards_into_header_and_removes_old_row() -> None:
    compact = MODE.split("def _compact_indicator_layout", 1)[1].split(
        "def _bind_mode_stack", 1
    )[0]

    assert "source_layout.removeWidget(unit)" in compact
    assert "header.insertWidget(" in compact
    assert "insert_at = 1" in compact
    assert "detail.hide()" in compact
    assert "self._detach_child_layout(root_layout, source_layout)" in compact
    assert "header.takeAt(1)" in compact


def test_compact_phase_cards_preserve_state_but_drop_detail_height() -> None:
    assert "_PHASE_UNIT_MIN_WIDTH = 132" in MODE
    assert "_PHASE_UNIT_MIN_HEIGHT = 38" in MODE
    assert "_PHASE_UNIT_MAX_HEIGHT = 42" in MODE
    assert "unit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)" in MODE
    assert "unit.show()" in MODE
    assert "detail.hide()" in MODE


def test_freed_vertical_budget_is_given_to_live_console_tabs() -> None:
    assert "_SUMMARY_MIN = 292" in MODE
    assert "_SUMMARY_MAX = 336" in MODE
    assert "_CONSOLE_TABS_MIN = 146" in MODE
    assert "_CONSOLE_TABS_MAX = 190" in MODE
    assert "root_layout.setContentsMargins(13, 8, 13, 10)" in MODE
    assert "root_layout.setSpacing(6)" in MODE
    assert "page_layout.setContentsMargins(5, 4, 5, 5)" in MODE
    assert "page_layout.setSpacing(4)" in MODE


def test_detail_action_does_not_reopen_splitter_geometry_path() -> None:
    assert "self.toggle.toggled.disconnect()" in MODE
    assert "self.toggle.setCheckable(False)" in MODE
    assert "self.toggle.setEnabled(True)" in MODE
    assert "self.toggle.show()" in MODE
    assert 'self.toggle.setText("展开详情")' in MODE
    assert "self.toggle.clicked.connect(self._open_detail)" in MODE

    open_detail = MODE.split("def _open_detail", 1)[1].split("def schedule", 1)[0]
    assert "self.details.open_console_details()" in open_detail
    assert "setSizes(" not in open_detail


def test_single_geometry_stays_coalesced_and_animation_free() -> None:
    assert "def _set_sizes_if_needed" in MODE
    assert "abs(a - b) <= 3" in MODE
    assert "splitter.setSizes(target)" in MODE
    assert "LayoutRequest" in MODE
    assert "QPropertyAnimation" not in MODE
    assert "QParallelAnimationGroup" not in MODE
    assert "QEasingCurve" not in MODE


def test_source_compiles_without_importing_pyside() -> None:
    compile(MODE, str(ROOT / "gui" / "console_summary_mode.py"), "exec")
