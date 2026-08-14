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


def test_console_bar_uses_live_and_confirmed_progress() -> None:
    assert "progress_changed.disconnect(self.console._on_progress)" in PROGRESS
    assert "self.console.progress.setRange(0, 1000)" in PROGRESS
    assert "_confirmed" in PROGRESS
    assert "_live" in PROGRESS


def test_visual_pacing_stays_below_real_checkpoint_ceiling() -> None:
    assert "cap - self._SOFT_RESERVE" in PROGRESS
    assert "math.exp" in PROGRESS
    tick = PROGRESS.split("    def _tick(self) -> None:", 1)[1].split("    def _render", 1)[0]
    assert "_sync_overall" not in tick
    sync = PROGRESS.split("    def _sync_overall(self) -> None:", 1)[1].split("    def _confirm", 1)[0]
    assert "self._confirmed" in sync
    assert "self._live" not in sync


def test_detail_shows_confirmed_elapsed_and_heartbeat() -> None:
    assert "已确认" in PROGRESS
    assert "elapsed" in PROGRESS
    assert '("●··", "·●·", "··●")' in PROGRESS
    assert "QTimer" in PROGRESS
    assert "Qt.TimerType.PreciseTimer" in PROGRESS


def test_resolver_and_fill_plan_have_internal_checkpoints() -> None:
    markers = (
        "PRIMARY PRODUCT SOURCE CAPTURE",
        "captured exact product page:",
        "DIRECT PRODUCT RESOLUTION",
        "image_evidence=DONE",
        "compact_evidence=DONE",
        "product_facts=DONE",
        "web_fill=START",
        "web_fill=DONE",
        "best_effort_inference=DONE",
        "DIRECT RESOLUTION COMPLETE",
        "MAKRO AI-DECISION FILL PLAN",
        'text.startswith("live_fields=")',
        'text.startswith("Manifest=")',
    )
    for marker in markers:
        assert marker in PROGRESS
    for prefix in ("IMAGE", "LOCAL", "WEB", "INFERENCE"):
        assert prefix in PROGRESS


def test_detailed_progress_is_installed_after_end_to_end_activity() -> None:
    activity = RUN.index("install_activity_presence(window)")
    detailed = RUN.index("install_detailed_preparation_progress(window)")
    assert activity < detailed
