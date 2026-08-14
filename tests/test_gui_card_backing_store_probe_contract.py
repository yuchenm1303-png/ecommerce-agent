from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "tools" / "gui_card_backing_store_probe.py"
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_backing_store_probe_is_development_only_and_compiles() -> None:
    source = PROBE.read_text(encoding="utf-8")
    compile(source, str(PROBE), "exec")
    assert "gui_card_backing_store_probe" not in RUNNER


def test_backing_store_probe_targets_effect_state_and_paint_invalidation() -> None:
    source = PROBE.read_text(encoding="utf-8")
    for token in (
        'POLICIES = ("production_toggle", "stable_enabled")',
        "class PaintProbe(QObject):",
        "QEvent.Type.UpdateRequest",
        "QEvent.Type.Paint",
        "window_paint_ratio_p95",
        "enable_toggles",
        "bounding_updates",
        "class _StableEnabledEffect(_TimedProductionEffect):",
        '"effect-enable-disable-churn-significant"',
        '"persistent-effect-more-expensive"',
        '"effect-state-toggle-not-dominant"',
    ):
        assert token in source
