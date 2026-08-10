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


def test_inline_card_body_animates_clipped_height_instead_of_visibility_snap() -> None:
    assert "class AdaptiveReveal(QObject)" in MOTION
    assert "self.wrapper.setMaximumHeight" in MOTION
    assert "self.wrapper.setMinimumHeight(0)" in MOTION
    assert "self._timer.setInterval(_TICK_MS)" in MOTION
    assert "Qt.TimerType.PreciseTimer" in MOTION
    assert "setGeometry(" not in MOTION
    assert "QPropertyAnimation" not in MOTION
    assert "QGraphicsOpacityEffect" not in MOTION


def test_travel_duration_adapts_to_real_content_distance() -> None:
    assert "_MIN_DURATION_MS = 156" in MOTION
    assert "_MAX_DURATION_MS = 218" in MOTION
    assert "148 + int(abs(distance) * 0.28)" in MOTION
    assert "def _measure_natural_height" in MOTION
    assert "self.wrapper.sizeHint().height()" in MOTION


def test_layout_reflows_siblings_and_console_splitter_during_motion() -> None:
    assert "self.splitter.setSizes" in MOTION
    assert "available - target_card" in MOTION
    assert "shell + wrapper_height" in MOTION
    assert "_inline_card_motion_active" in MOTION
    assert "removeEventFilter(mature)" in MOTION
    assert "installEventFilter(mature)" in MOTION


def test_glass_geometry_is_throttled_during_short_motion_only() -> None:
    assert "_MASK_SYNC_MS = 32" in MOTION
    assert "schedule_mask_update" in MOTION
    assert "now_ms - self._last_mask_sync >= _MASK_SYNC_MS" in MOTION
    assert "self._timer.stop()" in MOTION


def test_both_existing_inline_card_families_use_same_motion_engine() -> None:
    assert "def _build_real_settings_motion" in MOTION
    assert "def _build_console_motion" in MOTION
    assert 'collapsed_text="展开设置 ﹀"' in MOTION
    assert 'expanded_text="收起设置 ︿"' in MOTION
    assert 'collapsed_text="展开详情 ﹀"' in MOTION
    assert 'expanded_text="收起详情 ︿"' in MOTION
