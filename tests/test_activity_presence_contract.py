from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTIVITY = (ROOT / "gui" / "activity_presence.py").read_text(encoding="utf-8")
BOOT = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_activity_presence_is_installed_into_formal_gui() -> None:
    assert "from gui.activity_presence import install_activity_presence" in BOOT
    assert "install_activity_presence(window)" in BOOT


def test_activity_presence_uses_real_telemetry_not_fake_percent_growth() -> None:
    assert "prep.progress_changed.connect(self._on_prep_progress)" in ACTIVITY
    assert "prep.phase_event.connect(self._on_phase_event)" in ACTIVITY
    assert "prep.log.connect(self._on_prep_log)" in ACTIVITY
    assert "real.progress_changed.connect(self._on_real_progress)" in ACTIVITY
    assert "real.log.connect(self._on_real_log)" in ACTIVITY
    assert 'text.startswith("GUI_EXEC_FIELD\\t")' in ACTIVITY
    assert "self.target_percent = next_target" in ACTIVITY
    assert "self.display_percent += delta * alpha" in ACTIVITY
    assert "min(self.display_percent, self.target_percent)" in ACTIVITY
    assert "self.target_percent +=" not in ACTIVITY
    assert "self.target_percent = (self.target_percent" not in ACTIVITY


def test_activity_presence_is_one_monotonic_product_level_timeline() -> None:
    assert "_PREP_OVERALL_END = 45" in ACTIVITY
    assert "_REAL_OVERALL_START = 45" in ACTIVITY
    assert "_REAL_OVERALL_SPAN = 55" in ACTIVITY
    assert "return round(_PREP_OVERALL_END" in ACTIVITY
    assert "return _REAL_OVERALL_START + round(_REAL_OVERALL_SPAN" in ACTIVITY
    assert "self._real_internal = 0" in ACTIVITY
    assert "self._set_real(0" in ACTIVITY
    assert "准备阶段 100% · 总进度" in ACTIVITY


def test_activity_presence_exposes_granular_work_copy() -> None:
    for text in (
        "正在采集供应商商品证据",
        "正在确认 Makro Vertical",
        "正在确认 Brand",
        "正在扫描 live schema",
        "Resolver · Cold",
        "Resolver · Hot/Cache",
        "Fill Plan",
        "正在填写 {label}",
        "Save / reopen verify 完成",
        "Product Photos",
        "字段 {next_index}/{self._real_field_total}",
    ):
        assert text in ACTIVITY


def test_activity_presence_animation_is_time_driven_60fps_local_and_precise() -> None:
    assert "self.setFixedHeight(42)" in ACTIVITY
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
    assert "This shimmer communicates liveness" in ACTIVITY
    assert "self.meta" in ACTIVITY
    assert "QGraphicsBlurEffect" not in ACTIVITY
    assert "QGraphicsOpacityEffect" not in ACTIVITY


def test_activity_presence_covers_preparation_and_real_execution_states() -> None:
    for state in ("STANDBY", "PREPARING", "READY", "FILLING", "COMPLETE", "FAILED"):
        assert f'"{state}"' in ACTIVITY
    assert '"scan": "Source Capture"' in ACTIVITY
    assert '"plan": "Step 3 · Resolve / Fill Plan"' in ACTIVITY
    assert "photo_upload" in ACTIVITY
    assert "QC locked" in ACTIVITY
