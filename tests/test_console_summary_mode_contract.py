from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODE = (ROOT / "gui" / "console_summary_mode.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_console_summary_mode_is_installed_after_mature_layout() -> None:
    assert "from gui.console_summary_mode import install_console_summary_mode" in RUNNER
    assert "install_mature_ui(window)" in RUNNER
    assert "install_console_summary_mode(window)" in RUNNER
    assert RUNNER.index("install_mature_ui(window)") < RUNNER.index("install_console_summary_mode(window)")


def test_old_tiny_console_collapsed_state_is_not_used() -> None:
    assert "_SUMMARY_MIN = 300" in MODE
    assert "_SUMMARY_MAX = 460" in MODE
    assert "112" not in MODE
    assert "122" not in MODE
    assert "phase_units" in MODE
    assert "unit.show()" in MODE
    assert "tabs.show()" in MODE


def test_default_summary_is_permanent_and_detail_never_resizes_splitter() -> None:
    assert "self.toggle.toggled.disconnect()" in MODE
    assert "self.toggle.setCheckable(True)" in MODE
    assert "self.toggle.setChecked(True)" in MODE
    assert 'self.toggle.setText("展开详情")' in MODE
    assert "_DETAIL_MIN" not in MODE
    assert "_DETAIL_MAX" not in MODE
    assert "_detail_open" not in MODE
    assert "def _open_detail" in MODE
    assert "self.details.open_console_details()" in MODE


def test_console_summary_keeps_only_one_atomic_base_size() -> None:
    assert "self.console.setMinimumHeight(_SUMMARY_MIN)" in MODE
    assert "self.console.setMaximumHeight(_SUMMARY_MAX)" in MODE
    assert "target = self._summary_target(available)" in MODE
    assert "QPropertyAnimation" not in MODE
    assert "QParallelAnimationGroup" not in MODE
    assert "QEasingCurve" not in MODE


def test_summary_mode_only_changes_splitter_when_target_really_changes() -> None:
    assert "def _set_sizes_if_needed" in MODE
    assert "abs(a - b) > 3" in MODE
    assert "splitter.setSizes(target)" in MODE


def test_source_compiles_without_importing_pyside() -> None:
    compile(MODE, str(ROOT / "gui" / "console_summary_mode.py"), "exec")
