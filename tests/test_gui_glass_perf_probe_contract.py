from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROBE = (ROOT / "tools" / "gui_glass_perf_probe.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_glass_probe_is_development_only_and_compiles() -> None:
    compile(PROBE, "tools/gui_glass_perf_probe.py", "exec")
    assert "gui_glass_perf_probe" not in RUN


def test_glass_probe_is_focused_on_multieffect_and_parallax() -> None:
    for token in (
        'MODES = ("production_glass", "glass_overlay_only", "static_background")',
        "ShaderEffectSource",
        "live: false",
        "MultiEffect",
        "FrameAnimation",
        "layer.enabled: root.glassEnabled",
        "root.parallaxEnabled && root.animationRunning",
        "frameSwapped.connect",
    ):
        assert token in PROBE


def test_glass_probe_reports_quick_tail_latency_and_root_cause() -> None:
    for token in (
        "swap_p95_ms",
        "swap_p99_ms",
        "swap_long_1_5x_rate",
        "multieffect-dominant",
        "parallax-source-motion-dominant",
        "combined-glass-parallax-cost",
        "glass-stack-not-dominant",
        "GLASS PERFORMANCE PROBE SUMMARY",
        "VERDICT:",
    ):
        assert token in PROBE
