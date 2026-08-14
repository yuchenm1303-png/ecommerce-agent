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


def test_round_two_matrix_is_baseline_centered() -> None:
    lab = LAB_PATH.read_text(encoding="utf-8")
    for token in (
        'name = "baseline_frozen"',
        'name = "frozen_target_rect"',
        'name = "frozen_transform"',
        'name = "frozen_fast"',
        'name = "quantized_12"',
        'name = "baseline_60hz"',
        'name = "baseline_72hz"',
        'name = "baseline_90hz"',
        'name = "no_scale_control"',
        "from gui.native_visual_style import _CardScaleEffect",
        "TRANSITION_MS = 300",
        "HOVER_SCALE = 1.02",
    ):
        assert token in lab
    for retired in ("snapshot_cpu", "snapshot_gl", "cached_levels", "_PaintGate", "QOpenGLWidget"):
        assert retired not in lab


def test_lab_covers_single_and_multi_card_hot_paths() -> None:
    lab = LAB_PATH.read_text(encoding="utf-8")
    doc = DOC_PATH.read_text(encoding="utf-8")
    assert 'choices=("single", "crossover", "both")' in lab
    assert "class CrossoverRun(QObject):" in lab
    assert "PATH = (0, 1, 2, 3, 4, 5, 4, 3, 2, 1)" in lab
    assert "CROSSOVER_DWELL_MS = 70" in lab
    assert "--compare-demo" in lab
    assert "多卡" in doc
    assert "--scenario both" in doc


def test_perf_lab_records_tail_latency_and_analyzer_rejects_noise_wins() -> None:
    lab = LAB_PATH.read_text(encoding="utf-8")
    analyzer = ANALYZER_PATH.read_text(encoding="utf-8")
    doc = DOC_PATH.read_text(encoding="utf-8")
    for token in (
        "frame_p95_ms",
        "frame_p99_ms",
        "long_1_5x_rate",
        "transition_start_gap_p95_ms",
        "transition_prepare_p95_ms",
        "tick_work_p95_ms",
        "cpu_core_percent",
        "eligible_default",
        "scenario",
    ):
        assert token in lab
    assert "MIN_REPLACEMENT_SCORE_IMPROVEMENT_PERCENT = 2.0" in analyzer
    assert "KEEP BASELINE" in analyzer
    assert "--min-replacement-improvement" in analyzer
    assert "--parity" in analyzer
    assert "FINAL CANDIDATE" in analyzer
    assert "同屏" in doc
