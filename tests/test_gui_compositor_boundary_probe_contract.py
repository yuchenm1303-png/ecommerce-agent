from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = (ROOT / "tools" / "gui_compositor_boundary_probe.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_compositor_boundary_probe_is_development_only_and_compiles() -> None:
    compile(TOOL, "tools/gui_compositor_boundary_probe.py", "exec")
    assert "gui_compositor_boundary_probe" not in RUN


def test_probe_alternates_background_and_card_phases_inside_one_real_gui_run() -> None:
    for token in (
        'PHASES = ("background-only", "card-crossover")',
        "real_perf.RealGuiProfiler = BoundaryProfiler",
        '"--manual"',
        "self._phase_timer.timeout.connect(self._drive_phase)",
        "QCursor.setPos(point)",
        "self._make_background_points()",
        "self._card_path",
    ):
        assert token in TOOL


def test_probe_correlates_qwidget_paint_with_quick_swap_tail() -> None:
    for token in (
        "QEvent.Type.UpdateRequest",
        "QEvent.Type.Paint",
        "def _on_swap(self)",
        "paint_associated_swap",
        "paint_clean_swap",
        "card_phase_p99_penalty_percent",
        "paint_associated_p99_penalty_percent",
        "REAL GUI COMPOSITOR BOUNDARY SUMMARY",
    ):
        assert token in TOOL


def test_probe_has_explicit_root_cause_classifications() -> None:
    for token in (
        "widget-quick-boundary-contention-likely",
        "widget-quick-boundary-contention-moderate",
        "quick-frame-scheduling-not-widget-paint-bound",
        "phase-separation-insufficient",
        "insufficient-data",
    ):
        assert token in TOOL
