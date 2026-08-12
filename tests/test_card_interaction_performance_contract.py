from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PERF = (ROOT / "gui" / "card_interaction_performance.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
NATIVE_VISUAL = (ROOT / "gui" / "native_visual_style.py").read_text(encoding="utf-8")


def _body(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_runtime_installs_card_perf_after_native_card_controller() -> None:
    assert "from gui.card_interaction_performance import install_card_interaction_performance" in RUN
    assert "card_fx = install_nekro_card_fx(window, visual)" in RUN
    assert "install_card_interaction_performance(window, visual, card_fx)" in RUN
    assert RUN.index("card_fx = install_nekro_card_fx(window, visual)") < RUN.index(
        "install_card_interaction_performance(window, visual, card_fx)"
    )


def test_high_refresh_card_motion_is_hard_capped_without_slowing_60hz() -> None:
    assert "_MAX_MOTION_HZ = 90.0" in PERF
    assert "_MIN_MOTION_FRAME_MS = max(1, round(1000.0 / _MAX_MOTION_HZ))" in PERF
    body = _body(PERF, "def _install_motion_rate_cap", "def _retire_stale_motions")
    assert "baseline = max(1, int(original()))" in body
    assert "return max(baseline, _MIN_MOTION_FRAME_MS)" in body
    assert "timer.setInterval(self.card_fx._frame_interval_ms())" in body


def test_rapid_cross_card_traversal_never_keeps_unbounded_old_motions() -> None:
    assert "_MAX_CONCURRENT_MOTIONS = 2" in PERF
    retire = _body(PERF, "def _retire_stale_motions", "def _install_motion_budget")
    assert "while len(moving) > _MAX_CONCURRENT_MOTIONS:" in retire
    assert "hovered = getattr(self.card_fx, \"hovered\", None)" in retire
    assert "pressed = getattr(self.card_fx, \"pressed\", None)" in retire
    assert "state.snap(_NORMAL_SCALE, _NORMAL_ALPHA)" in retire
    assert "moving.discard(stale)" in retire

    install = _body(PERF, "def _install_motion_budget", "def _install_content_scale_quantization")
    assert "performance._motion_serial += 1" in install
    assert "performance._motion_order[frame] = performance._motion_serial" in install
    assert "performance._retire_stale_motions()" in install


def test_widget_subtree_scale_skips_only_invisible_subpixel_deltas() -> None:
    assert "_CONTENT_SCALE_EPSILON = 0.00025" in PERF
    assert "_NORMAL_SCALE_EPSILON = 1e-5" in PERF
    body = _body(PERF, "def _install_content_scale_quantization", "def _cleanup")
    assert "current = float(getattr(effect_self, \"scale\", _NORMAL_SCALE))" in body
    assert "abs(requested - current) < _CONTENT_SCALE_EPSILON" in body
    assert "return" in body
    assert "abs(requested - _NORMAL_SCALE) <= _NORMAL_SCALE_EPSILON" in body
    assert "requested = _NORMAL_SCALE" in body
    assert "_original(requested)" in body


def test_performance_layer_does_not_replace_fresh_composite_or_child_interaction_path() -> None:
    effect = _body(NATIVE_VISUAL, "class _CardScaleEffect", "class NativeGlassProxy")

    # The previous regression fixes stay authoritative: every actual QWidget
    # effect redraw still gets a fresh whole-card composite, with no retained cache.
    assert "self.sourcePixmap(" in effect
    assert "painter.drawPixmap(offset, pixmap)" in effect
    assert "_cached_source" not in effect
    assert "_source_snapshot" not in effect

    # The performance layer must not intercept child widgets or mutate layout.
    assert "installEventFilter" not in PERF
    assert "findChildren" not in PERF
    assert "setGeometry" not in PERF
    assert ".resize(" not in PERF
    assert "QApplication" not in PERF
    assert "QCursor" not in PERF


def test_performance_source_compiles_without_importing_pyside() -> None:
    compile(
        PERF,
        str(ROOT / "gui" / "card_interaction_performance.py"),
        "exec",
    )
    compile(RUN, str(ROOT / "run_local_gui.py"), "exec")
