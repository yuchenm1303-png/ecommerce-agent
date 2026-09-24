from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROBE = (ROOT / "tools" / "gui_glass_perf_probe.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_glass_probe_is_development_only_and_compiles() -> None:
    compile(PROBE, "tools/gui_glass_perf_probe.py", "exec")
    assert "gui_glass_perf_probe" not in RUN


def test_glass_probe_compares_only_multieffect_at_fixed_cadence() -> None:
    for token in (
        'MODES = ("production_glass", "no_multieffect")',
        "ShaderEffectSource",
        "live: false",
        "MultiEffect",
        "framePulse",
        "root.requestUpdate()",
        "frameSwapped.connect",
        "swap_per_tick",
    ):
        assert token in PROBE
    assert "static_background" not in PROBE
    assert "glass_overlay_only" not in PROBE


def test_glass_probe_rejects_cadence_mismatch_before_cost_decision() -> None:
    for token in (
        "cadence-mismatch-retry",
        "multieffect-material",
        "multieffect-minor",
        "keep-production-glass",
        "MIN_MATERIAL_GAIN_PERCENT = 10.0",
        "FIXED-CADENCE GLASS PROBE SUMMARY",
        "VERDICT:",
    ):
        assert token in PROBE
