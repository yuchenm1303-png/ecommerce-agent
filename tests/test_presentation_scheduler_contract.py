from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLOCK = (ROOT / "gui" / "presentation_clock.py").read_text(encoding="utf-8")


def test_presentation_clock_serializes_quick_before_qwidget_work() -> None:
    compile(CLOCK, "gui/presentation_clock.py", "exec")
    assert "_WIDGET_STARVATION_MS = 40" in CLOCK
    assert "quick.frameSwapped.connect(self._on_quick_frame_swapped)" in CLOCK
    assert "QTimer.singleShot(0, self._flush_widget_lane_after_swap)" in CLOCK
    assert "self.background.presentation_tick(global_pos, input_changed=input_changed)" in CLOCK
    assert "self._queue_widget_sample(" in CLOCK
    assert "self._schedule_widget_lane()" in CLOCK

    tick = CLOCK.split("def _tick(self) -> None:", 1)[1].split("def eventFilter", 1)[0]
    assert tick.index("self.background.presentation_tick") < tick.index("self._queue_widget_sample")
    assert "self.card_fx.presentation_tick" not in tick
    assert "self.effects.presentation_tick" not in tick

    flush = CLOCK.split("def _flush_widget_lane(self) -> None:", 1)[1].split("def _tick", 1)[0]
    assert "self.card_fx.presentation_tick" in flush
    assert "self.effects.presentation_tick" in flush


def test_widget_lane_coalesces_pointer_motion_but_preserves_button_edges() -> None:
    queue = CLOCK.split("def _queue_widget_sample", 1)[1].split("def _quick_lane_active", 1)[0]
    assert "if button_edge or self._widget_samples[-1].button_edge:" in queue
    assert "self._widget_samples[-1] = sample" in queue
    assert "sample.input_changed or self._widget_samples[-1].input_changed" in queue


def test_widget_lane_flushes_immediately_when_quick_is_idle_and_has_watchdog() -> None:
    assert 'quick.property("animationRunning")' in CLOCK
    schedule = CLOCK.split("def _schedule_widget_lane", 1)[1].split(
        "def _on_quick_frame_swapped", 1
    )[0]
    assert "if self._quick_lane_active():" in schedule
    assert "self._widget_watchdog.start()" in schedule
    assert "self._flush_widget_lane()" in schedule
