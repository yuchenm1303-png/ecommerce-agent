from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLOCK = (ROOT / "gui" / "presentation_clock.py").read_text(encoding="utf-8")


def test_normal_quick_pointer_path_is_event_driven() -> None:
    compile(CLOCK, "gui/presentation_clock.py", "exec")
    assert "quick.installEventFilter(self)" in CLOCK
    assert "QEvent.Type.MouseMove" in CLOCK
    assert "QEvent.Type.HoverMove" in CLOCK
    assert "self.background.presentation_tick(global_pos, input_changed=True)" in CLOCK

    event_filter = CLOCK.split("def eventFilter", 1)[1].split("def cleanup", 1)[0]
    assert "self._publish_quick_pointer(event)" in event_filter
    assert "QCursor.pos()" not in event_filter


def test_quick_mode_has_no_continuous_python_pointer_clock() -> None:
    assert "_ACTIVE_PRESENTATION_TICK_MS" not in CLOCK
    assert "_AMBIENT_PRESENTATION_TICK_MS" not in CLOCK
    assert "_INTERACTION_GRACE_MS" not in CLOCK
    assert "_widget_watchdog" not in CLOCK
    assert "frameSwapped.connect" not in CLOCK
    assert "_queue_widget_sample" not in CLOCK
    assert "_schedule_widget_lane" not in CLOCK


def test_timer_is_reserved_for_legacy_widget_fallback() -> None:
    assert "_LEGACY_FRAME_MS = 16" in CLOCK
    assert "self.timer.timeout.connect(self._legacy_tick)" in CLOCK
    assert "legacy_should_run = bool(" in CLOCK
    assert "self._widget_lane_enabled" in CLOCK

    legacy_tick = CLOCK.split("def _legacy_tick(self) -> None:", 1)[1].split(
        "def eventFilter", 1
    )[0]
    assert "QCursor.pos()" in legacy_tick
    assert "self.card_fx.presentation_tick" in legacy_tick
    assert "self.effects.presentation_tick" in legacy_tick


def test_quick_animation_remains_scene_graph_owned() -> None:
    background = (ROOT / "gui" / "native_background.py").read_text(encoding="utf-8")
    assert "FrameAnimation" in background
    assert 'quick.setProperty("animationRunning", True)' in CLOCK
    assert "QPropertyAnimation" not in CLOCK
