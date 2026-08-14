from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROBE_PATH = ROOT / "tools" / "gui_card_tail_probe.py"
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_tail_probe_is_development_only_and_compiles() -> None:
    source = PROBE_PATH.read_text(encoding="utf-8")
    compile(source, str(PROBE_PATH), "exec")
    assert "gui_card_tail_probe" not in RUN
    assert "ROOT = Path(__file__).resolve().parents[1]" in source
    assert "sys.path.insert(0, str(ROOT))" in source


def test_tail_probe_is_narrow_and_causal() -> None:
    source = PROBE_PATH.read_text(encoding="utf-8")
    for token in (
        'POLICIES = ("dual_full", "incoming_priority", "incoming_only")',
        "class _TimedCardScaleEffect(_CardScaleEffect):",
        "self._stats.capture_ms.append(elapsed)",
        "self._stats.draw_ms.append",
        'classification = "concurrent-effect-redraw-dominant"',
        'classification = "capture-significant"',
        'classification = "mixed-or-backing-store"',
        "overlap_tick_rate",
        "capture_p95_ms",
        "draw_p99_ms",
    ):
        assert token in source


def test_tail_probe_does_not_add_a_new_renderer() -> None:
    source = PROBE_PATH.read_text(encoding="utf-8")
    assert "from gui.native_visual_style import _CardScaleEffect" in source
    assert "QOpenGLWidget" not in source
    assert "snapshot_gl" not in source
    assert "frozen_target_rect" not in source
