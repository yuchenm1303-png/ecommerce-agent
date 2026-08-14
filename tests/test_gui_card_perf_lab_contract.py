from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAB_PATH = ROOT / "tools" / "gui_card_perf_lab.py"
ANALYZER_PATH = ROOT / "tools" / "analyze_gui_card_perf.py"
DOC_PATH = ROOT / "docs" / "GUI_CARD_PERF_LAB.md"
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_perf_lab_is_development_only_and_sources_compile() -> None:
    lab = LAB_PATH.read_text(encoding="utf-8")
    analyzer = ANALYZER_PATH.read_text(encoding="utf-8")
    compile(lab, str(LAB_PATH), "exec")
    compile(analyzer, str(ANALYZER_PATH), "exec")
    assert "gui_card_perf_lab" not in RUN
    assert "analyze_gui_card_perf" not in RUN


def test_perf_lab_contains_current_baseline_and_candidate_matrix() -> None:
    lab = LAB_PATH.read_text(encoding="utf-8")
    for token in (
        'name = "baseline_frozen"',
        'name = "live_effect"',
        'name = "snapshot_cpu"',
        'name = "snapshot_gl"',
        'name = "cached_levels"',
        'name = "no_scale_control"',
        "from gui.native_visual_style import _CardScaleEffect",
        "TRANSITION_MS = 300",
        "HOVER_SCALE = 1.02",
    ):
        assert token in lab


def test_perf_lab_records_tail_latency_and_requires_parity_gate() -> None:
    lab = LAB_PATH.read_text(encoding="utf-8")
    analyzer = ANALYZER_PATH.read_text(encoding="utf-8")
    doc = DOC_PATH.read_text(encoding="utf-8")
    for token in (
        "frame_p95_ms",
        "frame_p99_ms",
        "long_1_5x_rate",
        "transition_start_gap_p95_ms",
        "transition_prepare_p95_ms",
        "cpu_core_percent",
    ):
        assert token in lab
    assert "--parity" in analyzer
    assert "FINAL CANDIDATE" in analyzer
    assert "视觉与交互一致性门槛" in doc
