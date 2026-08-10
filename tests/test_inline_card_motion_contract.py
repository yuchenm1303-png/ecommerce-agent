from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLISH = (ROOT / "gui" / "ui_polish.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_runner_uses_stable_ui_polish_toggle_without_inline_animation_override() -> None:
    assert "install_ui_polish(window)" in RUNNER
    assert "inline_card_motion" not in RUNNER
    assert "inline_motion_glass_guard" not in RUNNER
    assert "install_inline_card_motion" not in RUNNER
    assert "install_inline_motion_glass_guard" not in RUNNER


def test_obsolete_inline_animation_modules_are_removed() -> None:
    assert not (ROOT / "gui" / "inline_card_motion.py").exists()
    assert not (ROOT / "gui" / "inline_motion_glass_guard.py").exists()


def test_real_execution_expand_is_atomic_visibility_toggle() -> None:
    assert "def _install_real_execution_collapse" in POLISH
    assert "widget.setVisible(expanded)" in POLISH
    assert 'toggle.setText("收起设置" if expanded else "展开设置")' in POLISH
    assert "QPropertyAnimation" not in POLISH
    assert "QParallelAnimationGroup" not in POLISH
    assert "setDuration(" not in POLISH


def test_console_expand_jumps_directly_to_final_splitter_geometry() -> None:
    assert "def _install_console_collapse" in POLISH
    assert "unit.setVisible(expanded)" in POLISH
    assert "tabs.setVisible(expanded)" in POLISH
    assert 'toggle.setText("收起详情" if expanded else "展开详情")' in POLISH
    assert "body.setSizes([max(260, available - target), target])" in POLISH
    assert "body.setSizes([max(320, body.height() - 122), 122])" in POLISH


def test_expand_paths_have_no_intermediate_height_animation() -> None:
    assert "QPropertyAnimation" not in POLISH
    assert "QEasingCurve" not in POLISH
    assert "minimumHeight" not in POLISH.split("def _install_real_execution_collapse", 1)[1].split(
        "def _tabify_side_panel", 1
    )[0]
