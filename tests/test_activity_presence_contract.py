from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTIVITY = (ROOT / "gui" / "activity_presence.py").read_text(encoding="utf-8")
BOOT = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_activity_presence_is_installed_into_formal_gui() -> None:
    assert "from gui.activity_presence import install_activity_presence" in BOOT
    assert "install_activity_presence(window)" in BOOT


def test_activity_presence_uses_real_runner_progress_not_fake_percent_growth() -> None:
    assert "prep.progress_changed.connect(self._on_prep_progress)" in ACTIVITY
    assert "prep.phase_event.connect(self._on_phase_event)" in ACTIVITY
    assert "real.progress_changed.connect(self._on_real_progress)" in ACTIVITY
    assert "self.target_percent = next_target" in ACTIVITY
    assert "self.display_percent += delta * alpha" in ACTIVITY
    assert "min(self.display_percent, self.target_percent)" in ACTIVITY
    assert "self.target_percent +=" not in ACTIVITY
    assert "self.target_percent = (self.target_percent" not in ACTIVITY


def test_activity_presence_animation_is_time_driven_60fps_local_and_precise() -> None:
    assert "self.setFixedHeight(32)" in ACTIVITY
    assert "_FRAME_MS = 16" in ACTIVITY
    assert "Qt.TimerType.PreciseTimer" in ACTIVITY
    assert "time.perf_counter()" in ACTIVITY
    assert "1.0 - math.exp(-dt / self._PROGRESS_TAU_S)" in ACTIVITY
    assert "self._motion_time_s / self._SWEEP_PERIOD_S" in ACTIVITY
    assert "self.update()" in ACTIVITY
    assert "window.update()" not in ACTIVITY
    assert "self._timer.stop()" in ACTIVITY
    assert "WA_TransparentForMouseEvents" in ACTIVITY


def test_activity_presence_has_refined_layered_progress_visuals_without_blur_effects() -> None:
    assert "QLinearGradient" in ACTIVITY
    assert "QRadialGradient" in ACTIVITY
    assert "track_h = 3.0" in ACTIVITY
    assert "display_percent / 100.0" in ACTIVITY
    assert "luminous leading edge" in ACTIVITY
    assert "Independent activity shimmer" in ACTIVITY
    assert "QGraphicsBlurEffect" not in ACTIVITY
    assert "QGraphicsOpacityEffect" not in ACTIVITY


def test_activity_presence_covers_preparation_and_real_execution_states() -> None:
    for state in ("STANDBY", "PREPARING", "READY", "FILLING", "COMPLETE", "FAILED"):
        assert f'"{state}"' in ACTIVITY
    assert '"scan": "Source Capture"' in ACTIVITY
    assert '"plan": "Step 3 · Resolve / Fill Plan"' in ACTIVITY
    assert "photo_upload" in ACTIVITY
    assert "QC locked" in ACTIVITY
