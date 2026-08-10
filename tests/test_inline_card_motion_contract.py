from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOTION = (ROOT / "gui" / "inline_card_motion.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_inline_motion_installs_after_layout_and_maturity_before_native_shell() -> None:
    assert "from gui.inline_card_motion import install_inline_card_motion" in RUNNER
    assert "install_ui_polish(window)" in RUNNER
    assert "install_mature_ui(window)" in RUNNER
    assert "install_inline_card_motion(window)" in RUNNER
    assert "install_native_window_shell(window, quick_window)" in RUNNER
    assert RUNNER.index("install_mature_ui(window)") < RUNNER.index("install_inline_card_motion(window)")
    assert RUNNER.index("install_inline_card_motion(window)") < RUNNER.index(
        "install_native_window_shell(window, quick_window)"
    )


def test_inline_card_body_uses_qt_animation_driver_not_python_frame_timer() -> None:
    assert "class AdaptiveReveal(QObject)" in MOTION
    assert "QPropertyAnimation" in MOTION
    assert "QParallelAnimationGroup" in MOTION
    assert 'QPropertyAnimation(self.wrapper, b"maximumHeight")' in MOTION
    assert "QEasingCurve.Type.InOutCubic" in MOTION
    assert "PreciseTimer" not in MOTION
    assert "def _tick(" not in MOTION
    assert "setInterval(8)" not in MOTION
    assert "setGeometry(" not in MOTION
    assert "QGraphicsOpacityEffect" not in MOTION


def test_travel_duration_is_short_and_adapts_to_content_distance() -> None:
    assert "_MIN_DURATION_MS = 154" in MOTION
    assert "_MAX_DURATION_MS = 198" in MOTION
    assert "146 + int(abs(distance) * 0.18)" in MOTION
    assert "def _measure_natural_height" in MOTION
    assert "self.wrapper.sizeHint().height()" in MOTION


def test_console_splitter_follows_widget_constraints_without_per_frame_setsizes() -> None:
    assert 'QPropertyAnimation(self.card, b"minimumHeight")' in MOTION
    assert 'QPropertyAnimation(self.card, b"maximumHeight")' in MOTION
    assert "self.splitter.setSizes" not in MOTION
    assert "available - 250" in MOTION
    assert "_inline_card_motion_active" in MOTION
    assert "removeEventFilter(mature)" in MOTION
    assert "installEventFilter(mature)" in MOTION


def test_glass_mask_is_not_manually_rebuilt_from_a_python_animation_loop() -> None:
    assert "_MASK_SYNC_MS" not in MOTION
    assert "_last_mask_sync" not in MOTION
    assert "schedule_mask_update" in MOTION
    assert MOTION.count("_glass_sync(self.window)") <= 2


def test_both_existing_inline_card_families_use_same_motion_engine() -> None:
    assert "def _build_real_settings_motion" in MOTION
    assert "def _build_console_motion" in MOTION
    assert 'collapsed_text="展开设置 ﹀"' in MOTION
    assert 'expanded_text="收起设置 ︿"' in MOTION
    assert 'collapsed_text="展开详情 ﹀"' in MOTION
    assert 'expanded_text="收起详情 ︿"' in MOTION
