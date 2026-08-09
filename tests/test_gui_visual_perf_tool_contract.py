from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RECORDER = (ROOT / "gui" / "visual_perf.py").read_text(encoding="utf-8")
HOOKS = (ROOT / "gui" / "visual_perf_hooks.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_visual_perf_capture_is_opt_in_and_memory_buffered() -> None:
    assert 'QKeySequence("Ctrl+Alt+P")' in HOOKS
    assert "_CAPTURE_MS = 15_000" in HOOKS
    assert "if self.active:" in RECORDER
    assert "self.samples: deque" in RECORDER
    assert "with self.latest_samples_path.open" in RECORDER
    stop_body = RECORDER.split("def stop(self)", 1)[1].split("def counter", 1)[0]
    assert "write_text" in stop_body
    assert ".open(" in stop_body
    counter_body = RECORDER.split("def counter", 1)[1].split("def value", 1)[0]
    value_body = RECORDER.split("def value", 1)[1].split("def timing_ms", 1)[0]
    assert "write_text" not in counter_body
    assert ".open(" not in counter_body
    assert "write_text" not in value_body
    assert ".open(" not in value_body


def test_visual_perf_records_stutter_and_quantization_signals() -> None:
    required = (
        "visual.callback_interval_ms",
        "visual.motion_tick_ms",
        "visual.paint_gate_skips",
        "visual.quantized_holds",
        "visual.float_offset_step_px",
        "visual.source_step_px",
        "visual.paint_ms",
        "visual.paint_dirty_bbox_pct",
        "visual.repair_region_ms",
        "effects.frame_ms",
        "effects.paint_ms",
        "card_fx.tick_ms",
        "logs.flush_ms",
        "input.mouse_move_events",
    )
    for metric in required:
        assert metric in HOOKS


def test_visual_perf_is_wired_after_all_visual_components_exist() -> None:
    assert "VisualPerfRecorder" in RUNNER
    assert "install_visual_perf_hooks" in RUNNER
    assert "visual = install_visual_style(window)" in RUNNER
    assert "card_fx = install_nekro_card_fx(window, visual)" in RUNNER
    assert "buffered_logs = install_buffered_logs(window)" in RUNNER
    assert "effects = install_nekro_effects(window, sakura_count=3)" in RUNNER
    assert RUNNER.index("install_visual_perf_hooks(") > RUNNER.index("effects = install_nekro_effects")
