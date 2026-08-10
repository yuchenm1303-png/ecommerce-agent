from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLISH = (ROOT / "gui" / "ui_polish.py").read_text(encoding="utf-8")
FAST = (ROOT / "gui" / "card_details_fast.py").read_text(encoding="utf-8")
SUMMARY = (ROOT / "gui" / "console_summary_mode.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_obsolete_inline_animation_modules_are_removed() -> None:
    assert "inline_card_motion" not in RUNNER
    assert "inline_motion_glass_guard" not in RUNNER
    assert not (ROOT / "gui" / "inline_card_motion.py").exists()
    assert not (ROOT / "gui" / "inline_motion_glass_guard.py").exists()


def test_legacy_polish_toggles_exist_only_as_baseline_controls() -> None:
    assert "def _install_real_execution_collapse" in POLISH
    assert "def _install_console_collapse" in POLISH
    assert "QPropertyAnimation" not in POLISH
    assert "QEasingCurve" not in POLISH


def test_runtime_real_settings_disconnects_inline_toggle_and_opens_modal() -> None:
    assert "def _install_real_settings_action" in FAST
    assert "toggle.toggled.disconnect()" in FAST
    assert "toggle.setCheckable(False)" in FAST
    assert "toggle.clicked.connect(self.open_real_settings)" in FAST
    assert "self.open_custom(" in FAST


def test_runtime_console_detail_never_uses_a_second_splitter_size() -> None:
    assert "_DETAIL_MIN" not in SUMMARY
    assert "_DETAIL_MAX" not in SUMMARY
    assert "_detail_open" not in SUMMARY
    assert "self.details.open_console_details()" in SUMMARY
    assert "def _summary_target" in SUMMARY
    assert "def _detail_target" not in SUMMARY


def test_all_runtime_expand_paths_have_no_intermediate_layout_animation() -> None:
    for source in (FAST, SUMMARY):
        assert "QPropertyAnimation" not in source
        assert "QParallelAnimationGroup" not in source
        assert "QEasingCurve" not in source
    assert "frame.setGeometry" not in FAST
    assert "frame.resize" not in FAST
