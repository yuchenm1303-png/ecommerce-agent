from __future__ import annotations

import math
import time
from pathlib import Path
from types import MethodType
from typing import Any, Callable

from PySide6.QtCore import QEvent, QObject, QPointF, QRect, QTimer
from PySide6.QtGui import QMouseEvent, QPaintEvent
from PySide6.QtWidgets import QApplication, QMainWindow

from .visual_perf import VisualPerfRecorder


_CAPTURE_MS = 15_000


def _bbox_ratio(widget: Any, rect: QRect) -> float:
    total = max(1, int(widget.width()) * int(widget.height()))
    return max(0.0, min(100.0, rect.width() * rect.height() / total * 100.0))


def _series(summary: dict[str, Any], name: str, stat: str = "p95") -> float:
    try:
        return float(summary.get("series", {}).get(name, {}).get(stat, 0.0))
    except (TypeError, ValueError):
        return 0.0


class VisualPerfHooks(QObject):
    """Diagnostics-only instrumentation around the existing visual pipeline.

    The production motion algorithm is not changed here. Timers are reconnected
    only so the original callbacks can be timed from immediately before to
    immediately after execution. Paint virtuals and a few Python hot-path methods
    are wrapped at the class level and restored when the window is destroyed.
    """

    def __init__(
        self,
        window: QMainWindow,
        visual: Any,
        effects: Any,
        card_fx: Any,
        buffered_logs: Any,
        recorder: VisualPerfRecorder,
    ) -> None:
        super().__init__(window)
        self.window = window
        self.visual = visual
        self.scene = visual.scene
        self.effects = effects
        self.card_fx = card_fx
        self.buffered_logs = buffered_logs
        self.recorder = recorder
        self._last_motion_callback = 0.0
        self._last_effects_callback = 0.0
        self._motion_seq = 0
        self._restorers: list[Callable[[], None]] = []

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        self._install_scene_hooks()
        self._install_effects_hooks()
        self._install_card_hooks()
        self._install_log_hooks()

        from PySide6.QtGui import QKeySequence, QShortcut

        self.shortcut = QShortcut(QKeySequence("Ctrl+Alt+P"), window)
        self.shortcut.setContext(QtShortcutContext(window))
        self.shortcut.activated.connect(self.toggle_capture)

        self.auto_stop = QTimer(self)
        self.auto_stop.setSingleShot(True)
        self.auto_stop.timeout.connect(self.stop_capture)
        window.destroyed.connect(self._cleanup)

    def _install_scene_hooks(self) -> None:
        scene = self.scene
        cls = type(scene)

        # Reconnect the timer around our wrapper so the measured interval covers
        # exactly one original motion callback, not event-loop delay afterwards.
        original_tick_bound = scene._motion_tick
        try:
            scene._motion_timer.timeout.disconnect(original_tick_bound)
        except (RuntimeError, TypeError):
            pass
        original_tick = cls._motion_tick
        hook = self

        def motion_tick(instance: Any) -> Any:
            if instance is not scene or not hook.recorder.active:
                return original_tick(instance)
            now = time.perf_counter()
            if hook._last_motion_callback:
                hook.recorder.value("visual.callback_interval_ms", (now - hook._last_motion_callback) * 1000.0)
            hook._last_motion_callback = now
            before_offset = QPointF(instance._offset)
            before_rect = QRect(instance._source_rect)
            was_pending = bool(instance._paint_pending)
            started = time.perf_counter()
            result = original_tick(instance)
            hook.recorder.timing_ms("visual.motion_tick_ms", started)
            hook.recorder.counter("visual.motion_ticks")
            if was_pending:
                hook.recorder.counter("visual.paint_gate_skips")

            after_offset = QPointF(instance._offset)
            after_rect = QRect(instance._source_rect)
            float_step = math.hypot(
                after_offset.x() - before_offset.x(),
                after_offset.y() - before_offset.y(),
            )
            source_dx = after_rect.x() - before_rect.x()
            source_dy = after_rect.y() - before_rect.y()
            source_step = math.hypot(source_dx, source_dy)
            hook.recorder.value("visual.float_offset_step_px", float_step)
            hook.recorder.value("visual.source_step_px", source_step)
            hook.recorder.value(
                "visual.target_error_px",
                math.hypot(
                    instance._target.x() - instance._offset.x(),
                    instance._target.y() - instance._offset.y(),
                ),
            )
            if float_step > 0.01 and source_step < 0.01:
                hook.recorder.counter("visual.quantized_holds")
            if source_step > 0:
                hook.recorder.counter("visual.source_moves")

            hook._motion_seq += 1
            if hook._motion_seq % 20 == 0:
                hook.recorder.sample(
                    "visual.motion",
                    {
                        "float_step_px": round(float_step, 4),
                        "source_dx": source_dx,
                        "source_dy": source_dy,
                        "offset_x": round(instance._offset.x(), 4),
                        "offset_y": round(instance._offset.y(), 4),
                        "target_x": round(instance._target.x(), 4),
                        "target_y": round(instance._target.y(), 4),
                        "paint_pending": bool(instance._paint_pending),
                    },
                )
            return result

        setattr(cls, "_motion_tick", motion_tick)
        scene._motion_timer.timeout.connect(scene._motion_tick)

        def restore_tick() -> None:
            try:
                scene._motion_timer.timeout.disconnect(scene._motion_tick)
            except (RuntimeError, TypeError):
                pass
            setattr(cls, "_motion_tick", original_tick)
            scene._motion_timer.timeout.connect(scene._motion_tick)

        self._restorers.append(restore_tick)

        self._wrap_method(cls, scene, "_apply_source_rect", self._after_apply_source)
        self._wrap_method(cls, scene, "_scroll_repair_region", self._after_repair_region)
        self._wrap_method(cls, scene, "_refresh_geometry", self._after_geometry)
        self._wrap_method(cls, scene, "_rebuild", self._after_rebuild)
        self._wrap_paint(cls, scene, "paintEvent", "visual")

    def _install_effects_hooks(self) -> None:
        effects = self.effects
        cls = type(effects)
        original_bound = effects._frame
        try:
            effects.timer.timeout.disconnect(original_bound)
        except (RuntimeError, TypeError):
            pass
        original = cls._frame
        hook = self

        def frame(instance: Any) -> Any:
            if instance is not effects or not hook.recorder.active:
                return original(instance)
            now = time.perf_counter()
            if hook._last_effects_callback:
                hook.recorder.value("effects.callback_interval_ms", (now - hook._last_effects_callback) * 1000.0)
            hook._last_effects_callback = now
            started = time.perf_counter()
            result = original(instance)
            hook.recorder.timing_ms("effects.frame_ms", started)
            hook.recorder.counter("effects.frames")
            return result

        setattr(cls, "_frame", frame)
        effects.timer.timeout.connect(effects._frame)

        def restore_frame() -> None:
            try:
                effects.timer.timeout.disconnect(effects._frame)
            except (RuntimeError, TypeError):
                pass
            setattr(cls, "_frame", original)
            effects.timer.timeout.connect(effects._frame)

        self._restorers.append(restore_frame)
        self._wrap_paint(cls, effects, "paintEvent", "effects")

    def _install_card_hooks(self) -> None:
        controller = self.card_fx
        cls = type(controller)
        original_bound = controller._tick
        try:
            controller.timer.timeout.disconnect(original_bound)
        except (RuntimeError, TypeError):
            pass
        original = cls._tick
        hook = self

        def tick(instance: Any) -> Any:
            if instance is not controller or not hook.recorder.active:
                return original(instance)
            started = time.perf_counter()
            result = original(instance)
            hook.recorder.timing_ms("card_fx.tick_ms", started)
            hook.recorder.counter("card_fx.ticks")
            hook.recorder.value(
                "card_fx.animating_cards",
                sum(1 for state in instance.states.values() if state.animating),
            )
            return result

        setattr(cls, "_tick", tick)
        controller.timer.timeout.connect(controller._tick)

        def restore_tick() -> None:
            try:
                controller.timer.timeout.disconnect(controller._tick)
            except (RuntimeError, TypeError):
                pass
            setattr(cls, "_tick", original)
            controller.timer.timeout.connect(controller._tick)

        self._restorers.append(restore_tick)

    def _install_log_hooks(self) -> None:
        presenter = self.buffered_logs
        if presenter is None:
            return
        cls = type(presenter)
        original_bound = presenter.flush
        try:
            presenter.timer.timeout.disconnect(original_bound)
        except (RuntimeError, TypeError):
            pass
        original = cls.flush
        hook = self

        def flush(instance: Any) -> Any:
            if instance is not presenter or not hook.recorder.active:
                return original(instance)
            pending = len(instance.pending)
            started = time.perf_counter()
            result = original(instance)
            hook.recorder.timing_ms("logs.flush_ms", started)
            hook.recorder.value("logs.flush_lines", pending)
            hook.recorder.counter("logs.flushes")
            return result

        setattr(cls, "flush", flush)
        presenter.timer.timeout.connect(presenter.flush)

        def restore_flush() -> None:
            try:
                presenter.timer.timeout.disconnect(presenter.flush)
            except (RuntimeError, TypeError):
                pass
            setattr(cls, "flush", original)
            presenter.timer.timeout.connect(presenter.flush)

        self._restorers.append(restore_flush)

    def _wrap_method(
        self,
        cls: type,
        instance: Any,
        name: str,
        after: Callable[[Any, tuple[Any, ...], Any, float], None],
    ) -> None:
        original = getattr(cls, name)
        hook = self

        def wrapped(obj: Any, *args: Any, **kwargs: Any) -> Any:
            if obj is not instance or not hook.recorder.active:
                return original(obj, *args, **kwargs)
            started = time.perf_counter()
            result = original(obj, *args, **kwargs)
            after(obj, args, result, started)
            return result

        setattr(cls, name, wrapped)
        self._restorers.append(lambda: setattr(cls, name, original))

    def _wrap_paint(self, cls: type, instance: Any, name: str, prefix: str) -> None:
        original = getattr(cls, name)
        hook = self

        def wrapped(obj: Any, event: QPaintEvent) -> Any:
            if obj is not instance or not hook.recorder.active:
                return original(obj, event)
            region = event.region()
            bbox = region.boundingRect()
            hook.recorder.value(f"{prefix}.paint_dirty_bbox_pct", _bbox_ratio(obj, bbox))
            try:
                hook.recorder.value(f"{prefix}.paint_dirty_rect_count", float(region.rectCount()))
            except AttributeError:
                pass
            started = time.perf_counter()
            result = original(obj, event)
            hook.recorder.timing_ms(f"{prefix}.paint_ms", started)
            hook.recorder.counter(f"{prefix}.paint_events")
            if _bbox_ratio(obj, bbox) >= 80.0:
                hook.recorder.counter(f"{prefix}.near_full_paints")
            return result

        setattr(cls, name, wrapped)
        self._restorers.append(lambda: setattr(cls, name, original))

    def _after_apply_source(self, scene: Any, args: tuple[Any, ...], _result: Any, started: float) -> None:
        self.recorder.timing_ms("visual.apply_source_ms", started)
        if args:
            next_rect = args[0]
            if isinstance(next_rect, QRect):
                self.recorder.gauge("visual.last_source_x", next_rect.x())
                self.recorder.gauge("visual.last_source_y", next_rect.y())

    def _after_repair_region(self, scene: Any, args: tuple[Any, ...], result: Any, started: float) -> None:
        self.recorder.timing_ms("visual.repair_region_ms", started)
        if result is not None:
            bbox = result.boundingRect()
            self.recorder.value("visual.repair_bbox_pct", _bbox_ratio(scene, bbox))
            try:
                self.recorder.value("visual.repair_rect_count", float(result.rectCount()))
            except AttributeError:
                pass

    def _after_geometry(self, _scene: Any, _args: tuple[Any, ...], _result: Any, started: float) -> None:
        self.recorder.timing_ms("visual.geometry_refresh_ms", started)
        self.recorder.counter("visual.geometry_refreshes")

    def _after_rebuild(self, _scene: Any, _args: tuple[Any, ...], _result: Any, started: float) -> None:
        self.recorder.timing_ms("visual.wallpaper_rebuild_ms", started)
        self.recorder.counter("visual.wallpaper_rebuilds")

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if self.recorder.active and isinstance(event, QMouseEvent) and event.type() == QEvent.MouseMove:
            self.recorder.counter("input.mouse_move_events")
        return False

    def toggle_capture(self) -> None:
        if self.recorder.active:
            self.stop_capture()
        else:
            self.start_capture()

    def start_capture(self) -> None:
        if self.recorder.active:
            return
        screen = self.window.screen()
        refresh = float(screen.refreshRate()) if screen is not None else 0.0
        dpr = float(self.window.devicePixelRatioF())
        metadata = {
            "screen_refresh_hz": round(refresh, 3),
            "device_pixel_ratio": round(dpr, 3),
            "window_width": self.window.width(),
            "window_height": self.window.height(),
            "motion_timer_interval_ms": self.scene._motion_timer.interval(),
            "effects_timer_interval_ms": self.effects.timer.interval(),
            "sakura_particles": len(self.effects.particles),
            "parallax_max_travel_px": float(self.scene._MAX_TRAVEL_PX),
            "algorithm": "integer QWidget.scroll + glass XOR repair",
        }
        path = self.recorder.start(metadata)
        self._last_motion_callback = 0.0
        self._last_effects_callback = 0.0
        self._motion_seq = 0
        self._emit(
            "[VISUAL PERF] capture started · 15s. "
            "请依次：慢速移动 5s → 快速左右移动 5s → 画圆/斜向移动 5s。"
        )
        self._emit(f"[VISUAL PERF] output={path}")
        self.auto_stop.start(_CAPTURE_MS)

    def stop_capture(self) -> None:
        if not self.recorder.active:
            return
        self.auto_stop.stop()
        summary = self.recorder.stop()
        diagnosis = self._diagnose(summary)
        self._emit("[VISUAL PERF] capture complete")
        for line in self._summary_lines(summary, diagnosis):
            self._emit("[VISUAL PERF] " + line)
        if self.recorder.latest_summary_path:
            self._emit(f"[VISUAL PERF] summary={self.recorder.latest_summary_path}")
        if self.recorder.latest_samples_path:
            self._emit(f"[VISUAL PERF] samples={self.recorder.latest_samples_path}")

    def _diagnose(self, summary: dict[str, Any]) -> list[str]:
        counters = summary.get("counters", {})
        gauges = summary.get("gauges", {})
        ticks = max(1, int(counters.get("visual.motion_ticks", 0)))
        quantized = int(counters.get("visual.quantized_holds", 0)) / ticks
        gated = int(counters.get("visual.paint_gate_skips", 0)) / ticks
        refresh = max(30.0, float(gauges.get("screen_refresh_hz", 60.0) or 60.0))
        frame_budget = 1000.0 / refresh
        callback_p95 = _series(summary, "visual.callback_interval_ms")
        paint_p95 = _series(summary, "visual.paint_ms")
        effects_p95 = _series(summary, "effects.paint_ms")
        repair_p95 = _series(summary, "visual.repair_region_ms")

        findings: list[str] = []
        if quantized >= 0.25:
            findings.append(f"整数像素量化明显：{quantized * 100:.1f}% motion tick 没有产生 source 像素移动")
        if gated >= 0.10:
            findings.append(f"paint back-pressure 明显：{gated * 100:.1f}% motion tick 被 paint-in-flight gate 跳过")
        if callback_p95 > frame_budget * 1.45:
            findings.append(
                f"主线程/Timer cadence 抖动：motion callback p95={callback_p95:.2f}ms，"
                f"屏幕帧预算约 {frame_budget:.2f}ms"
            )
        if paint_p95 > frame_budget * 0.60:
            findings.append(f"背景 paint 偏重：p95={paint_p95:.2f}ms")
        if effects_p95 > frame_budget * 0.25:
            findings.append(f"樱花/鼠标 Overlay paint 占用可见：p95={effects_p95:.2f}ms")
        if repair_p95 > 1.0:
            findings.append(f"Glass XOR repair 计算偏重：p95={repair_p95:.2f}ms")
        if not findings:
            findings.append("单项绘制成本不高；优先检查 cadence/整数像素步进与 Windows compositor 提交节奏")
        return findings

    def _summary_lines(self, summary: dict[str, Any], diagnosis: list[str]) -> list[str]:
        counters = summary.get("counters", {})
        gauges = summary.get("gauges", {})
        ticks = int(counters.get("visual.motion_ticks", 0))
        quantized = int(counters.get("visual.quantized_holds", 0))
        gated = int(counters.get("visual.paint_gate_skips", 0))
        return [
            (
                f"screen={gauges.get('screen_refresh_hz', '?')}Hz · "
                f"motion_timer={gauges.get('motion_timer_interval_ms', '?')}ms · "
                f"DPR={gauges.get('device_pixel_ratio', '?')}"
            ),
            (
                f"mouse={counters.get('input.mouse_move_events', 0)} · motion_ticks={ticks} · "
                f"source_moves={counters.get('visual.source_moves', 0)} · "
                f"quantized_holds={quantized} · gate_skips={gated}"
            ),
            (
                f"motion_interval p50/p95={_series(summary, 'visual.callback_interval_ms', 'p50'):.2f}/"
                f"{_series(summary, 'visual.callback_interval_ms'):.2f}ms · "
                f"paint p50/p95={_series(summary, 'visual.paint_ms', 'p50'):.2f}/"
                f"{_series(summary, 'visual.paint_ms'):.2f}ms"
            ),
            (
                f"repair p95={_series(summary, 'visual.repair_region_ms'):.3f}ms · "
                f"effects frame/paint p95={_series(summary, 'effects.frame_ms'):.3f}/"
                f"{_series(summary, 'effects.paint_ms'):.3f}ms · "
                f"card_fx p95={_series(summary, 'card_fx.tick_ms'):.3f}ms"
            ),
            *["diagnosis: " + item for item in diagnosis],
        ]

    def _emit(self, line: str) -> None:
        runner = getattr(self.window, "runner", None)
        signal = getattr(runner, "log", None)
        if signal is not None:
            signal.emit(line)
        else:
            print(line)

    def _cleanup(self) -> None:
        if self.recorder.active:
            self.stop_capture()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        for restore in reversed(self._restorers):
            try:
                restore()
            except (RuntimeError, TypeError):
                pass
        self._restorers.clear()


def QtShortcutContext(window: QMainWindow) -> Any:
    # Import lazily to keep the diagnostics module easy to source-compile in CI.
    from PySide6.QtCore import Qt

    return Qt.ShortcutContext.ApplicationShortcut


def install_visual_perf_hooks(
    window: QMainWindow,
    visual: Any,
    effects: Any,
    card_fx: Any,
    buffered_logs: Any,
    recorder: VisualPerfRecorder,
) -> VisualPerfHooks:
    hooks = VisualPerfHooks(window, visual, effects, card_fx, buffered_logs, recorder)
    window._visual_perf_hooks = hooks  # type: ignore[attr-defined]
    return hooks
