from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROGRESS = (ROOT / "gui" / "preparation_progress.py").read_text(encoding="utf-8")
ACTIVITY = (ROOT / "gui" / "activity_presence.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_preparation_progress_source_compiles() -> None:
    compile(PROGRESS, "gui/preparation_progress.py", "exec")


def test_overall_timeline_keeps_preparation_at_45_percent() -> None:
    assert "_PREP_OVERALL_END = 45" in ACTIVITY
    assert "_REAL_OVERALL_START = 45" in ACTIVITY
    assert "_REAL_OVERALL_SPAN = 55" in ACTIVITY


def test_legacy_preparation_bar_is_no_longer_driven_by_four_phase_slots() -> None:
    assert "progress_changed.disconnect(self.console._on_progress)" in PROGRESS
    assert "if not self._full_mode()" in PROGRESS
    assert "_PHASE_START" in PROGRESS
    assert "_PHASE_COMPLETE" in PROGRESS
    assert '"plan": 46' in PROGRESS
    assert '"plan": 100' in PROGRESS


def test_preparation_bar_uses_real_subtask_and_ai_liveness_events() -> None:
    for marker in (
        "vertical 安全校验通过",
        "STEP 3 CURRENT RESOLVER · COLD",
        "STEP 3 CURRENT RESOLVER · HOT/CACHE",
        "STEP 3 CURRENT READ-ONLY FILL PLAN",
        "AI request started;",
        "AI connection established",
        "AI first output received",
        "AI still running:",
        "AI response complete",
    ):
        assert marker in PROGRESS


def test_preparation_progress_is_smooth_but_does_not_invent_timer_progress() -> None:
    assert "QVariantAnimation" in PROGRESS
    assert "QEasingCurve.Type.OutCubic" in PROGRESS
    assert "QTimer" not in PROGRESS
    assert "self._target + 2" in PROGRESS
    assert "Every real completed model response advances" in PROGRESS


def test_detailed_preparation_progress_is_installed_after_end_to_end_activity() -> None:
    activity = RUN.index("install_activity_presence(window)")
    detailed = RUN.index("install_detailed_preparation_progress(window)")
    assert activity < detailed
