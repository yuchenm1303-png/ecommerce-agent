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


def test_rich_console_summary_has_readable_legacy_and_scroll_page_heights() -> None:
    assert "_SUMMARY_MIN = 300" in MODE
    assert "_SUMMARY_MAX = 460" in MODE
    assert "_PAGE_SUMMARY_MIN = 420" in MODE
    assert "_PAGE_SUMMARY_MAX = 560" in MODE
    assert "112" not in MODE
    assert "122" not in MODE
    assert "phase_units" in MODE
    assert "unit.show()" in MODE
    assert "tabs.show()" in MODE


def test_default_summary_is_permanent_and_detail_never_resizes_on_click() -> None:
    assert "self.toggle.toggled.disconnect()" in MODE
    assert "self.toggle.setCheckable(True)" in MODE
    assert "self.toggle.setChecked(True)" in MODE
    assert "self.toggle.setEnabled(True)" in MODE
    assert "self.toggle.show()" in MODE
    assert 'self.toggle.setText("展开详情")' in MODE
    assert "_DETAIL_MIN" not in MODE
    assert "_DETAIL_MAX" not in MODE
    assert "_detail_open" not in MODE
    assert "def _open_detail" in MODE
    assert "self.details.open_console_details()" in MODE


def test_scroll_page_summary_owns_natural_height_without_splitter_resizing() -> None:
    assert 'self.page_scroll = getattr(window, "_single_page_scroll", None)' in MODE
    assert "not isinstance(self.body, QSplitter) and not isinstance(self.page_scroll, QScrollArea)" in MODE
    apply = MODE.split("def apply(self) -> None:", 1)[1].split("def _apply_after_mature", 1)[0]
    assert "if isinstance(self.page_scroll, QScrollArea):" in apply
    assert "self.console.setMinimumHeight(_PAGE_SUMMARY_MIN)" in apply
    assert "self.console.setMaximumHeight(_PAGE_SUMMARY_MAX)" in apply
    page_branch = apply.split("if isinstance(self.page_scroll, QScrollArea):", 1)[1].split("if not isinstance(self.body, QSplitter):", 1)[0]
    assert "self.body.setSizes" not in page_branch
    assert "return" in page_branch


def test_legacy_splitter_summary_still_changes_size_only_when_needed() -> None:
    assert "self.console.setMinimumHeight(_SUMMARY_MIN)" in MODE
    assert "self.console.setMaximumHeight(_SUMMARY_MAX)" in MODE
    assert "target = self._summary_target(available)" in MODE
    assert "def _set_sizes_if_needed" in MODE
    assert "abs(a - b) > 3" in MODE
    assert "splitter.setSizes(target)" in MODE
    assert "QPropertyAnimation" not in MODE
    assert "QParallelAnimationGroup" not in MODE
    assert "QEasingCurve" not in MODE


def test_source_compiles_without_importing_pyside() -> None:
    compile(MODE, str(ROOT / "gui" / "console_summary_mode.py"), "exec")
