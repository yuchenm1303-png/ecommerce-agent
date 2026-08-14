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
    assert "--scenario crossover" in doc
    assert "baseline_frozen,frozen_target_rect" in doc


def test_analyzer_never_mixes_cadence_with_renderer_selection() -> None:
    analyzer = ANALYZER_PATH.read_text(encoding="utf-8")
    assert 'CADENCE_ONLY = {"baseline_60hz", "baseline_72hz", "baseline_90hz"}' in analyzer
    assert "excluded from renderer decision" in analyzer
    assert "hz_mismatch" in analyzer
    assert "abs(candidate[\"target_hz\"] - base[\"target_hz\"])" in analyzer


def test_analyzer_prioritizes_crossover_and_guards_start_gap() -> None:
    analyzer = ANALYZER_PATH.read_text(encoding="utf-8")
    doc = DOC_PATH.read_text(encoding="utf-8")
    assert "CROSSOVER_WEIGHT = 0.80" in analyzer
    assert "SINGLE_WEIGHT = 0.20" in analyzer
    assert "MAX_START_GAP_REGRESSION_PERCENT = 15.0" in analyzer
    assert "MIN_REPLACEMENT_SCORE_IMPROVEMENT_PERCENT = 2.0" in analyzer
    assert "KEEP BASELINE" in analyzer
    assert "FINAL BENCHMARK CANDIDATE" in analyzer
    assert "large crossover" in doc.lower()
    assert "huge crossover" in doc.lower()
