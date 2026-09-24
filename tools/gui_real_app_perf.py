from __future__ import annotations

import argparse
import ctypes
import json
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QCursor, QPaintEvent, QPainter
from PySide6.QtWidgets import QApplication, QFrame, QMainWindow, QWidget

import gui.native_visual_style as visual_module
import gui.presentation_clock as clock_module


VARIANTS = ("legacy-toggle", "current")


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * max(0.0, min(1.0, q))
    lo = int(pos)
    hi = min(len(ordered) - 1, lo + 1)
    frac = pos - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


@dataclass
class EffectStats:
    draw_ms: list[float] = field(default_factory=list)
    capture_ms: list[float] = field(default_factory=list)
    enable_toggles: int = 0
    bounding_updates: int = 0

    def reset(self) -> None:
        self.draw_ms.clear()
        self.capture_ms.clear()
        self.enable_toggles = 0
        self.bounding_updates = 0


_EFFECT_STATS = EffectStats()
_MEASURING = False
_BASE_EFFECT = visual_module._CardScaleEffect


class _TimedCurrentEffect(_BASE_EFFECT):
    def _current_composite(self):  # noqa: ANN202
        capture = bool(self._frozen and self._freeze_requested)
        started = time.perf_counter()
        result = super()._current_composite()
        if _MEASURING and capture and result[0] is not None:
            _EFFECT_STATS.capture_ms.append((time.perf_counter() - started) * 1000.0)
        return result

    def draw(self, painter: QPainter) -> None:  # type: ignore[override]
        started = time.perf_counter()
        super().draw(painter)
        if _MEASURING:
            _EFFECT_STATS.draw_ms.append((time.perf_counter() - started) * 1000.0)


class _TimedLegacyToggleEffect(_TimedCurrentEffect):
    """Recreate the pre-resident lifecycle inside the latest real GUI process."""

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.setEnabled(False)

    def set_frozen(self, frozen: bool) -> None:
        frozen = bool(frozen)
        if frozen == self._frozen:
            return
        self._frozen = frozen
        self._freeze_requested = frozen
        self._clear_frozen_source()
        if self.isEnabled():
            self.update()

    def updateBoundingRect(self) -> None:  # noqa: N802
        if _MEASURING:
            _EFFECT_STATS.bounding_updates += 1
        super().updateBoundingRect()

    def set_scale(self, scale: float) -> None:
        requested = max(0.96, min(visual_module._EFFECT_BOUND_SCALE, float(scale)))
        exact_rest = abs(requested - 1.0) <= visual_module._NORMAL_SCALE_EPSILON
        if exact_rest:
            requested = 1.0
        else:
            edge_delta_px = self._content_span() * abs(requested - self._scale) * 0.5
            if edge_delta_px < visual_module._CONTENT_EDGE_STEP_PX:
                return

        if abs(requested - self._scale) <= visual_module._NORMAL_SCALE_EPSILON:
            if exact_rest and self._frozen:
                self._frozen = False
                self._freeze_requested = False
                self._clear_frozen_source()
            return

        self._scale = requested
        active = abs(requested - 1.0) > 1e-4
        before = bool(self.isEnabled())
        if before != active:
            self.setEnabled(active)
            if _MEASURING:
                _EFFECT_STATS.enable_toggles += 1
            self.updateBoundingRect()
        if not active:
            self._frozen = False
            self._freeze_requested = False
            self._clear_frozen_source()
        self.update()

    def boundingRectFor(self, source_rect: QRectF) -> QRectF:  # noqa: N802
        if not self.isEnabled():
            return QRectF(source_rect)
        return super().boundingRectFor(source_rect)


@dataclass
class PaintStats:
    update_requests: int = 0
    window_ratios: list[float] = field(default_factory=list)
    card_ratios: list[float] = field(default_factory=list)

    def reset(self) -> None:
        self.update_requests = 0
        self.window_ratios.clear()
        self.card_ratios.clear()


class _PaintProbe(QObject):
    def __init__(self, window: QMainWindow, cards: list[QFrame], stats: PaintStats) -> None:
        super().__init__(window)
        self.window = window
        self.central = window.centralWidget()
        self.cards = set(cards)
        self.stats = stats
        window.installEventFilter(self)
        if self.central is not None:
            self.central.installEventFilter(self)
        for card in cards:
            card.installEventFilter(self)

    @staticmethod
    def _ratio(widget: QWidget, event: QPaintEvent) -> float:
        total = max(1, int(widget.width()) * int(widget.height()))
        rect = event.region().boundingRect()
        area = max(0, int(rect.width())) * max(0, int(rect.height()))
        return min(1.0, float(area) / float(total))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if not _MEASURING:
            return False
        event_type = event.type()
        if watched is self.window and event_type == QEvent.Type.UpdateRequest:
            self.stats.update_requests += 1
        elif event_type == QEvent.Type.Paint and isinstance(watched, QWidget):
            ratio = self._ratio(watched, event)  # type: ignore[arg-type]
            if watched is self.window or watched is self.central:
                self.stats.window_ratios.append(ratio)
            elif watched in self.cards:
                self.stats.card_ratios.append(ratio)
        return False


@dataclass
class RealGuiSummary:
    variant: str
    git_head: str
    duration_s: float
    visible_cards: int
    sweep_points: int
    presentation_tick_p95_ms: float
    presentation_tick_p99_ms: float
    quick_swap_p95_ms: float
    quick_swap_p99_ms: float
    effect_draw_p95_ms: float
    effect_draw_p99_ms: float
    capture_p95_ms: float
    capture_p99_ms: float
    capture_count: int
    enable_toggles: int
    bounding_updates: int
    update_requests: int
    window_paint_ratio_p95: float
    card_paint_ratio_p95: float
    cpu_core_percent: float


class RealGuiProfiler(QObject):
    def __init__(
        self,
        window: QMainWindow,
        visual: object,
        *,
        variant: str,
        settle_s: float,
        duration_s: float,
        sweep_ms: int,
        output_dir: Path,
        manual: bool,
    ) -> None:
        super().__init__(window)
        self.window = window
        self.visual = visual
        self.variant = variant
        self.settle_s = max(1.0, settle_s)
        self.duration_s = max(3.0, duration_s)
        self.sweep_ms = max(40, sweep_ms)
        self.output_dir = output_dir
        self.manual = manual
        self.paint_stats = PaintStats()
        self.paint_probe: _PaintProbe | None = None
        self.presentation_times: list[float] = []
        self.swap_times: list[float] = []
        self._last_tick: float | None = None
        self._last_swap: float | None = None
        self._wall_started = 0.0
        self._cpu_started = 0.0
        self._original_cursor = QCursor.pos()
        self._points: list[QPoint] = []
        self._path: list[int] = []
        self._path_index = 0
        self._foreground_hwnd: int | None = None
        self._sweep_timer = QTimer(self)
        self._sweep_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._sweep_timer.setInterval(self.sweep_ms)
        self._sweep_timer.timeout.connect(self._sweep_once)

        quick = getattr(getattr(visual, "background", None), "quick_window", None)
        if quick is not None:
            quick.frameSwapped.connect(self._on_swap)

        QTimer.singleShot(int(self.settle_s * 1000.0), self._try_start)

    def attach_clock(self, clock: object) -> None:
        timer = getattr(clock, "timer", None)
        if timer is not None:
            timer.timeout.connect(self._on_presentation_tick)

    def _visible_cards(self) -> list[QFrame]:
        cards: list[QFrame] = []
        for frame in self.window.findChildren(QFrame):
            if frame.objectName() not in visual_module._GLASS_NAMES:
                continue
            try:
                if not frame.isVisibleTo(self.window) or not frame.isEnabled():
                    continue
                if frame.width() < 100 or frame.height() < 60:
                    continue
            except RuntimeError:
                continue
            cards.append(frame)
        return cards

    @staticmethod
    def _dedupe_points(cards: list[QFrame]) -> list[QPoint]:
        candidates: list[tuple[int, int, int, QPoint]] = []
        for frame in cards:
            try:
                point = frame.mapToGlobal(frame.rect().center())
                area = int(frame.width()) * int(frame.height())
            except RuntimeError:
                continue
            candidates.append((int(point.y()), int(point.x()), -area, point))
        candidates.sort()
        points: list[QPoint] = []
        for _y, _x, _area, point in candidates:
            if any(abs(point.x() - old.x()) < 55 and abs(point.y() - old.y()) < 55 for old in points):
                continue
            points.append(QPoint(point))
            if len(points) >= 6:
                break
        return points

    def _quick_window(self):  # noqa: ANN202
        return getattr(getattr(self.visual, "background", None), "quick_window", None)

    def _set_foreground(self, *, topmost: bool) -> None:
        """Keep the real app visible during deterministic cursor sweeps on Windows."""

        quick = self._quick_window()
        try:
            if quick is not None:
                quick.show()
                quick.raise_()
                quick.requestActivate()
            self.window.show()
            self.window.raise_()
            self.window.activateWindow()
        except RuntimeError:
            pass

        if sys.platform != "win32":
            return
        try:
            target = quick if quick is not None else self.window
            hwnd = int(target.winId())
            self._foreground_hwnd = hwnd
            user32 = ctypes.windll.user32
            hwnd_topmost = -1
            hwnd_notopmost = -2
            flags = 0x0001 | 0x0002 | 0x0040  # NOSIZE | NOMOVE | SHOWWINDOW
            user32.SetWindowPos(
                hwnd,
                hwnd_topmost if topmost else hwnd_notopmost,
                0,
                0,
                0,
                0,
                flags,
            )
            if topmost:
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            pass

    def _try_start(self) -> None:
        try:
            if not self.window.isVisible() or self.window.isMinimized():
                QTimer.singleShot(500, self._try_start)
                return
        except RuntimeError:
            return
        self._start()

    def _start(self) -> None:
        global _MEASURING
        self._set_foreground(topmost=True)
        cards = self._visible_cards()
        self._points = self._dedupe_points(cards)
        self.paint_probe = _PaintProbe(self.window, cards, self.paint_stats)
        self.presentation_times.clear()
        self.swap_times.clear()
        self._last_tick = None
        self._last_swap = None
        self.paint_stats.reset()
        _EFFECT_STATS.reset()
        self._wall_started = time.perf_counter()
        self._cpu_started = time.process_time()
        _MEASURING = True

        if not self.manual and len(self._points) >= 2:
            forward = list(range(len(self._points)))
            backward = list(range(len(self._points) - 2, 0, -1))
            self._path = forward + backward
            self._path_index = 0
            self._sweep_timer.start()
        elif not self.manual:
            print("[real-gui-perf] fewer than two visible glass cards; move the mouse manually during capture")

        print(
            f"[real-gui-perf] START variant={self.variant} duration={self.duration_s:.1f}s "
            f"visible_cards={len(cards)} sweep_points={len(self._points)}"
        )
        QTimer.singleShot(int(self.duration_s * 1000.0), self._finish)

    def _sweep_once(self) -> None:
        if not self._path:
            return
        index = self._path[self._path_index % len(self._path)]
        self._path_index += 1
        QCursor.setPos(self._points[index])

    def _on_presentation_tick(self) -> None:
        if not _MEASURING:
            return
        now = time.perf_counter()
        if self._last_tick is not None:
            self.presentation_times.append((now - self._last_tick) * 1000.0)
        self._last_tick = now

    def _on_swap(self) -> None:
        if not _MEASURING:
            return
        now = time.perf_counter()
        if self._last_swap is not None:
            self.swap_times.append((now - self._last_swap) * 1000.0)
        self._last_swap = now

    @staticmethod
    def _git_head() -> str:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return "unknown"

    def _shutdown_app(self) -> None:
        self._set_foreground(topmost=False)
        quick = self._quick_window()
        try:
            self.window.close()
        except RuntimeError:
            pass
        if quick is not None:
            try:
                quick.close()
            except RuntimeError:
                pass
        app = QApplication.instance()
        if app is not None:
            try:
                app.closeAllWindows()
            except RuntimeError:
                pass
            QTimer.singleShot(0, lambda: app.exit(0))

    def _finish(self) -> None:
        global _MEASURING
        _MEASURING = False
        self._sweep_timer.stop()
        try:
            QCursor.setPos(self._original_cursor)
        except RuntimeError:
            pass

        wall = max(1e-9, time.perf_counter() - self._wall_started)
        cpu = max(0.0, time.process_time() - self._cpu_started)
        summary = RealGuiSummary(
            variant=self.variant,
            git_head=self._git_head(),
            duration_s=wall,
            visible_cards=len(self._visible_cards()),
            sweep_points=len(self._points),
            presentation_tick_p95_ms=_percentile(self.presentation_times, 0.95),
            presentation_tick_p99_ms=_percentile(self.presentation_times, 0.99),
            quick_swap_p95_ms=_percentile(self.swap_times, 0.95),
            quick_swap_p99_ms=_percentile(self.swap_times, 0.99),
            effect_draw_p95_ms=_percentile(_EFFECT_STATS.draw_ms, 0.95),
            effect_draw_p99_ms=_percentile(_EFFECT_STATS.draw_ms, 0.99),
            capture_p95_ms=_percentile(_EFFECT_STATS.capture_ms, 0.95),
            capture_p99_ms=_percentile(_EFFECT_STATS.capture_ms, 0.99),
            capture_count=len(_EFFECT_STATS.capture_ms),
            enable_toggles=_EFFECT_STATS.enable_toggles,
            bounding_updates=_EFFECT_STATS.bounding_updates,
            update_requests=self.paint_stats.update_requests,
            window_paint_ratio_p95=_percentile(self.paint_stats.window_ratios, 0.95),
            card_paint_ratio_p95=_percentile(self.paint_stats.card_ratios, 0.95),
            cpu_core_percent=cpu / wall * 100.0,
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = self.output_dir / f"real-gui-perf-{self.variant}-{stamp}.json"
        payload = {
            "schema_version": 2,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "summary": asdict(summary),
            "samples": {
                "presentation_tick": len(self.presentation_times),
                "quick_swap": len(self.swap_times),
                "effect_draw": len(_EFFECT_STATS.draw_ms),
                "effect_capture": len(_EFFECT_STATS.capture_ms),
                "window_paint": len(self.paint_stats.window_ratios),
                "card_paint": len(self.paint_stats.card_ratios),
            },
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        print("\nREAL GUI PERF SUMMARY")
        print(f"variant              {summary.variant}")
        print(f"presentation p95/p99 {summary.presentation_tick_p95_ms:.2f} / {summary.presentation_tick_p99_ms:.2f} ms")
        print(f"Quick swap p95/p99   {summary.quick_swap_p95_ms:.2f} / {summary.quick_swap_p99_ms:.2f} ms")
        print(f"effect draw p95/p99  {summary.effect_draw_p95_ms:.2f} / {summary.effect_draw_p99_ms:.2f} ms")
        print(f"capture p95/p99      {summary.capture_p95_ms:.2f} / {summary.capture_p99_ms:.2f} ms")
        print(f"toggles / bounds     {summary.enable_toggles} / {summary.bounding_updates}")
        print(f"window paint p95     {summary.window_paint_ratio_p95 * 100:.1f}%")
        print(f"CPU core             {summary.cpu_core_percent:.1f}%")
        print(f"JSON                  {path}")

        # Close the real GUI after the JSON is safely persisted. This makes each
        # variant a normal exit-0 process instead of requiring an external kill.
        QTimer.singleShot(0, self._shutdown_app)


def _compare(paths: list[Path]) -> int:
    if len(paths) != 2:
        raise SystemExit("--compare requires LEGACY_JSON CURRENT_JSON")
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    old = payloads[0]["summary"]
    new = payloads[1]["summary"]
    if old.get("variant") != "legacy-toggle" or new.get("variant") != "current":
        raise SystemExit("compare order must be legacy-toggle JSON then current JSON")

    def improvement(key: str) -> float:
        before = max(1e-9, float(old[key]))
        return (before - float(new[key])) / before * 100.0

    print("\nREAL GUI A/B · current vs legacy-toggle")
    for key, label in (
        ("presentation_tick_p99_ms", "presentation p99"),
        ("quick_swap_p99_ms", "Quick swap p99"),
        ("effect_draw_p99_ms", "effect draw p99"),
        ("window_paint_ratio_p95", "window paint p95"),
        ("cpu_core_percent", "CPU core"),
    ):
        print(
            f"{label:<20} {float(old[key]):8.2f} -> {float(new[key]):8.2f}  "
            f"({improvement(key):+6.1f}%)"
        )
    print(f"legacy toggles/bounds  {old['enable_toggles']} / {old['bounding_updates']}")
    print(f"current toggles/bounds {new['enable_toggles']} / {new['bounding_updates']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure the real Listing Studio GUI with deterministic card sweeps"
    )
    parser.add_argument("--variant", choices=VARIANTS, default="current")
    parser.add_argument(
        "--settle",
        type=float,
        default=5.0,
        help="seconds after real GUI construction before measuring",
    )
    parser.add_argument("--duration", type=float, default=18.0)
    parser.add_argument("--sweep-ms", type=int, default=70)
    parser.add_argument("--output-dir", type=Path, default=Path("perf_results"))
    parser.add_argument("--manual", action="store_true", help="do not move the cursor automatically")
    parser.add_argument(
        "--compare",
        nargs=2,
        type=Path,
        metavar=("LEGACY_JSON", "CURRENT_JSON"),
    )
    args = parser.parse_args()
    if args.compare:
        return _compare(args.compare)

    visual_module._CardScaleEffect = (
        _TimedLegacyToggleEffect if args.variant == "legacy-toggle" else _TimedCurrentEffect
    )

    profiler_holder: dict[str, RealGuiProfiler] = {}
    original_visual_install = visual_module.install_native_visual_style
    original_clock_install = clock_module.install_presentation_clock

    def install_visual(window: QMainWindow):  # noqa: ANN202
        visual = original_visual_install(window)
        profiler = RealGuiProfiler(
            window,
            visual,
            variant=args.variant,
            settle_s=args.settle,
            duration_s=args.duration,
            sweep_ms=args.sweep_ms,
            output_dir=args.output_dir,
            manual=args.manual,
        )
        profiler_holder["profiler"] = profiler
        window._real_gui_perf_profiler = profiler  # type: ignore[attr-defined]
        return visual

    def install_clock(window: QMainWindow, *, background, card_fx, effects):  # noqa: ANN001, ANN202
        clock = original_clock_install(
            window,
            background=background,
            card_fx=card_fx,
            effects=effects,
        )
        profiler = profiler_holder.get("profiler")
        if profiler is not None:
            profiler.attach_clock(clock)
        return clock

    visual_module.install_native_visual_style = install_visual
    clock_module.install_presentation_clock = install_clock

    import run_local_gui

    rc = run_local_gui.main()
    if "profiler" not in profiler_holder:
        print(
            "[real-gui-perf] profiler never attached; application access may have "
            "exited before GUI construction"
        )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
