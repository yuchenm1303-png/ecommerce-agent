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


def test_console_bar_keeps_fractional_checkpoint_resolution() -> None:
    assert "progress_changed.disconnect(self.console._on_progress)" in PROGRESS
    assert "self.console.progress.setRange(0, 1000)" in PROGRESS
    assert "_confirmed" in PROGRESS


def test_progress_bar_mutates_only_from_real_checkpoint_confirmation() -> None:
    confirm = PROGRESS.split("    def _confirm(self, value: float, detail: str, *, force: bool = False) -> None:", 1)[1].split("    def _render", 1)[0]
    assert "self.console.progress.setValue(target)" in confirm
    assert "self._sync_overall()" in confirm
    assert "def _tick(" not in PROGRESS
    assert "_soft_target" not in PROGRESS
    assert "Qt.TimerType.PreciseTimer" not in PROGRESS
    assert "QTimer" not in PROGRESS


def test_detail_stays_checkpoint_driven_and_truthful() -> None:
    assert "已确认" in PROGRESS
    assert "elapsed" in PROGRESS
    assert "Quick-owned visual motion" in PROGRESS
    assert "checkpoint-driven only" in PROGRESS


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
