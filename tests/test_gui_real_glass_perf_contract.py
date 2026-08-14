from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = (ROOT / "tools" / "gui_real_glass_perf.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
NATIVE = (ROOT / "gui" / "native_background.py").read_text(encoding="utf-8")


def test_real_glass_ab_is_development_only_and_compiles() -> None:
    compile(TOOL, "tools/gui_real_glass_perf.py", "exec")
    assert "gui_real_glass_perf" not in RUN
    assert "regionalGlass" not in NATIVE


def test_regional_candidate_uses_explicit_stable_source_and_mask_lifetimes() -> None:
    for token in (
        'VARIANTS = ("full-window", "regional")',
        "_ORIGINAL_QML = bg._qml_source",
        "bg._qml_source = _regional_qml_source",
        "id: regionalGlass",
        "id: regionalSource",
        "id: regionalMask",
        "id: regionalEffect",
        "source: regionalSource",
        "maskSource: regionalMask",
        "layer.enabled: true",
        "maskEnabled: true",
        "radius: {bg._GLASS_RADIUS:.1f}",
        "width: root.width * {bg._OVERSCAN}",
        "x: root.imageX - cardX",
        "y: root.imageY - cardY",
        "source: root.blurUrl",
        "fillMode: Image.PreserveAspectCrop",
        "cache: true",
    ):
        assert token in TOOL
    assert "layer.effect: MultiEffect" not in TOOL.split("def _regional_qml_source", 1)[1].split("def _latest_new_json", 1)[0]
    assert '"id: glassMaskScene" in candidate' in TOOL
    assert '"ShaderEffectSource" in candidate' in TOOL


def test_real_glass_ab_reuses_real_app_profiler_and_requires_visual_gate() -> None:
    for token in (
        "from tools import gui_real_app_perf as real_perf",
        '"--variant",\n        "current"',
        "real_perf.main()",
        "REAL GUI GLASS A/B · regional vs full-window",
        "weighted = gains[\"quick_swap_p95_ms\"] * 0.4",
        'verdict = "REGIONAL PERF CANDIDATE"',
        'verdict = "KEEP FULL-WINDOW"',
        'verdict = "INCONCLUSIVE"',
        'payload["visual_gate_required"] = True',
        "VISUAL GATE: REQUIRED",
        "every visible card keeps blur",
    ):
        assert token in TOOL
