from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOTION = (ROOT / "gui" / "inline_card_motion.py").read_text(encoding="utf-8")
GUARD = (ROOT / "gui" / "inline_motion_glass_guard.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_inline_motion_installs_after_layout_and_maturity_before_native_shell() -> None:
    assert "from gui.inline_card_motion import install_inline_card_motion" in RUNNER
    assert "from gui.inline_motion_glass_guard import install_inline_motion_glass_guard" in RUNNER
    assert "install_inline_motion_glass_guard(window)" in RUNNER
    assert "install_ui_polish(window)" in RUNNER
    assert "install_mature_ui(window)" in RUNNER
    assert "install_inline_card_motion(window)" in RUNNER
    assert "install_native_window_shell(window, quick_window)" in RUNNER
    assert RUNNER.index("install_mature_ui(window)") < RUNNER.index("install_inline_card_motion(window)")
    assert RUNNER.index("install_inline_card_motion(window)") < RUNNER.index(
        "install_native_window_shell(window, quick_window)"
    )


def test_inline_card_uses_qt_driver_and_one_layout_constraint_only() -> None:
    assert "class AdaptiveReveal(QObject)" in MOTION
    assert "QPropertyAnimation" in MOTION
    assert "QParallelAnimationGroup" not in MOTION
    assert "PreciseTimer" not in MOTION
    assert "def _tick(" not in MOTION
    assert "setInterval(8)" not in MOTION
    assert "self.splitter.setSizes" not in MOTION
    assert "QGraphicsOpacityEffect" not in MOTION
    assert 'QPropertyAnimation(self.card, b"minimumHeight")' in MOTION
    assert 'QPropertyAnimation(self.card, b"maximumHeight")' in MOTION
    assert "self.wrapper.setMaximumHeight(self._expanded_wrapper_height)" in MOTION


def test_travel_duration_is_short_and_distance_adaptive() -> None:
    assert "_MIN_DURATION_MS = 150" in MOTION
    assert "_MAX_DURATION_MS = 188" in MOTION
    assert "142 + int(abs(distance) * 0.15)" in MOTION
    assert "QEasingCurve.Type.InOutCubic" in MOTION
    assert "_measure_target_wrapper_height" in MOTION


def test_motion_suspends_responsive_competition_and_uses_shared_gate() -> None:
    assert "removeEventFilter(mature)" in MOTION
    assert "installEventFilter(mature)" in MOTION
    assert "begin_inline_motion(self.window)" in MOTION
    assert "end_inline_motion(self.window)" in MOTION


def test_glass_png_and_competing_ui_timers_are_frozen_during_reflow() -> None:
    assert "_update_mask_texture" in GUARD
    assert "_inline_card_motion_active" in GUARD
    assert "_mask_ready" in GUARD
    assert 'quick.setProperty("animationRunning", False)' in GUARD
    assert '_pause_timer(background, "_pointer_timer"' in GUARD
    assert '_pause_timer(effects, "timer"' in GUARD
    assert '_pause_timer(card_fx, "_sample_timer"' in GUARD
    assert '_pause_timer(card_fx, "_animation_timer"' in GUARD
    assert '_pause_timer(scroller, "_timer"' in GUARD
    assert 'setattr(background, "_last_pointer_norm", None)' in GUARD
    assert "schedule()" in GUARD


def test_both_inline_card_families_use_same_engine() -> None:
    assert "def _build_real_settings_motion" in MOTION
    assert "def _build_console_motion" in MOTION
    assert 'collapsed_text="展开设置 ﹀"' in MOTION
    assert 'expanded_text="收起设置 ︿"' in MOTION
    assert 'collapsed_text="展开详情 ﹀"' in MOTION
    assert 'expanded_text="收起详情 ︿"' in MOTION
