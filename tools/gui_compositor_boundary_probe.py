from __future__ import annotations

import argparse
import bisect
import json
import math
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QCursor, QPaintEvent
from PySide6.QtWidgets import QApplication, QFrame, QMainWindow, QWidget

from tools import gui_real_app_perf as real_perf


PHASES = ("background-only", "card-crossover")
_GUARD_S = 0.25
_CONFIG: dict[str, float | int | Path] = {
    "block_s": 3.0,
    "sweep_ms": 70,
    "output_dir": Path("perf_results"),
}


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


def _interval_stats(values: list[float], budget_ms: float) -> dict[str, float | int]:
    return {
        "samples": len(values),
        "p50_ms": _percentile(values, 0.50),
        "p95_ms": _percentile(values, 0.95),
        "p99_ms": _percentile(values, 0.99),
        "max_ms": max(values, default=0.0),
        "long_1_5x_rate": (
            sum(value > budget_ms * 1.5 for value in values) / max(1, len(values))
        ),
    }


@dataclass
class _StampedEvent:
    at: float
    phase: str
    guarded: bool
    kind: str
    ratio: float = 0.0


class _BoundaryEventProbe(QObject):
    def __init__(self, owner: "BoundaryProfiler") -> None:
        super().__init__(owner)
        self.owner = owner
        self.events: list[_StampedEvent] = []
        self.window = owner.window
        self.central = owner.window.centralWidget()
        self.cards = set(owner.window.findChildren(QFrame))
        owner.window.installEventFilter(self)
        if self.central is not None:
            self.central.installEventFilter(self)
        for card in self.cards:
            card.installEventFilter(self)

    @staticmethod
    def _paint_ratio(widget: QWidget, event: QPaintEvent) -> float:
        total = max(1, int(widget.width()) * int(widget.height()))
        rect = event.region().boundingRect()
        area = max(0, int(rect.width())) * max(0, int(rect.height()))
        return min(1.0, float(area) / float(total))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if not real_perf._MEASURING:
            return False
        now = time.perf_counter()
        phase, guarded = self.owner.phase_at(now)
        event_type = event.type()
        if event_type == QEvent.Type.UpdateRequest and watched in {self.window, self.central}:
            self.events.append(_StampedEvent(now, phase, guarded, "update-request"))
        elif event_type == QEvent.Type.Paint and isinstance(watched, QWidget):
            kind = "card-paint" if watched in self.cards else "window-paint"
            ratio = self._paint_ratio(watched, event)  # type: ignore[arg-type]
            self.events.append(_StampedEvent(now, phase, guarded, kind, ratio))
        return False


class BoundaryProfiler(real_perf.RealGuiProfiler):
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        super().__init__(*args, **kwargs)
        self._boundary_probe = _BoundaryEventProbe(self)
        self._swap_stamps: list[tuple[float, str, bool]] = []
        self._tick_stamps: list[tuple[float, str, bool]] = []
        self._phase_timer = QTimer(self)
        self._phase_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._phase_timer.setInterval(int(_CONFIG["sweep_ms"]))
        self._phase_timer.timeout.connect(self._drive_phase)
        self._background_points: list[QPoint] = []
        self._background_index = 0
        self._card_index = 0
        self._card_path: list[int] = []

    @property
    def block_s(self) -> float:
        return max(1.0, float(_CONFIG["block_s"]))

    def phase_at(self, now: float) -> tuple[str, bool]:
        if self._wall_started <= 0.0:
            return PHASES[0], True
        elapsed = max(0.0, now - self._wall_started)
        block_index = int(elapsed // self.block_s)
        phase = PHASES[block_index % 2]
        block_pos = elapsed - block_index * self.block_s
        guarded = block_pos < _GUARD_S
        return phase, guarded

    def _visible_card_rects_global(self) -> list[QRect]:
        rects: list[QRect] = []
        for frame in self._visible_cards():
            try:
                top_left = frame.mapToGlobal(QPoint(0, 0))
                rect = QRect(top_left, frame.size()).adjusted(-28, -28, 28, 28)
            except RuntimeError:
                continue
            rects.append(rect)
        return rects

    def _make_background_points(self) -> list[QPoint]:
        try:
            width = max(1, int(self.window.width()))
            height = max(1, int(self.window.height()))
        except RuntimeError:
            return []
        card_rects = self._visible_card_rects_global()
        fractions = (
            (0.08, 0.10), (0.92, 0.10), (0.08, 0.90), (0.92, 0.90),
            (0.50, 0.08), (0.50, 0.92), (0.12, 0.50), (0.88, 0.50),
            (0.30, 0.18), (0.70, 0.82), (0.30, 0.82), (0.70, 0.18),
        )
        candidates: list[QPoint] = []
        for fx, fy in fractions:
            local = QPoint(int(width * fx), int(height * fy))
            try:
                point = self.window.mapToGlobal(local)
            except RuntimeError:
                continue
            if any(rect.contains(point) for rect in card_rects):
                continue
            candidates.append(point)
        if len(candidates) < 2:
            try:
                candidates = [
                    self.window.mapToGlobal(QPoint(24, 24)),
                    self.window.mapToGlobal(QPoint(max(24, width - 24), max(24, height - 24))),
                ]
            except RuntimeError:
                return []
        return candidates[:6]

    def _start(self) -> None:
        super()._start()
        self._background_points = self._make_background_points()
        if len(self._points) >= 2:
            forward = list(range(len(self._points)))
            backward = list(range(len(self._points) - 2, 0, -1))
            self._card_path = forward + backward
        if len(self._background_points) < 2:
            print("[compositor-probe] WARNING: fewer than two clean background points")
        if len(self._card_path) < 2:
            print("[compositor-probe] WARNING: fewer than two card crossover points")
        self._phase_timer.start()
        print(
            f"[compositor-probe] alternating every {self.block_s:.1f}s: "
            "background-only <-> card-crossover"
        )

    def _drive_phase(self) -> None:
        now = time.perf_counter()
        phase, _guarded = self.phase_at(now)
        if phase == "background-only":
            if not self._background_points:
                return
            point = self._background_points[self._background_index % len(self._background_points)]
            self._background_index += 1
        else:
            if not self._card_path:
                return
            card = self._card_path[self._card_index % len(self._card_path)]
            self._card_index += 1
            point = self._points[card]
        QCursor.setPos(point)

    def _on_swap(self) -> None:
        if real_perf._MEASURING:
            now = time.perf_counter()
            phase, guarded = self.phase_at(now)
            self._swap_stamps.append((now, phase, guarded))
        super()._on_swap()

    def _on_presentation_tick(self) -> None:
        if real_perf._MEASURING:
            now = time.perf_counter()
            phase, guarded = self.phase_at(now)
            self._tick_stamps.append((now, phase, guarded))
        super()._on_presentation_tick()

    @staticmethod
    def _phase_intervals(
        stamps: list[tuple[float, str, bool]], phase: str
    ) -> list[tuple[float, float, float]]:
        output: list[tuple[float, float, float]] = []
        for previous, current in zip(stamps, stamps[1:]):
            start, start_phase, start_guarded = previous
            end, end_phase, end_guarded = current
            if start_phase != phase or end_phase != phase or start_guarded or end_guarded:
                continue
            output.append((start, end, (end - start) * 1000.0))
        return output

    def _write_boundary_report(self) -> Path:
        app = QApplication.instance()
        screen = app.primaryScreen() if app is not None else None
        try:
            refresh_hz = float(screen.refreshRate()) if screen is not None else 60.0
        except (RuntimeError, TypeError, ValueError):
            refresh_hz = 60.0
        if not 30.0 <= refresh_hz <= 500.0:
            refresh_hz = 60.0
        budget_ms = 1000.0 / refresh_hz

        phase_rows: dict[str, dict[str, object]] = {}
        intervals_by_phase: dict[str, list[tuple[float, float, float]]] = {}
        tick_by_phase: dict[str, list[tuple[float, float, float]]] = {}
        for phase in PHASES:
            swaps = self._phase_intervals(self._swap_stamps, phase)
            ticks = self._phase_intervals(self._tick_stamps, phase)
            intervals_by_phase[phase] = swaps
            tick_by_phase[phase] = ticks
            phase_events = [
                event for event in self._boundary_probe.events
                if event.phase == phase and not event.guarded
            ]
            card_paints = [event for event in phase_events if event.kind == "card-paint"]
            window_paints = [event for event in phase_events if event.kind == "window-paint"]
            updates = [event for event in phase_events if event.kind == "update-request"]
            phase_seconds = max(
                1e-6,
                sum((end - start) for start, end, _delta in swaps),
            )
            phase_rows[phase] = {
                "swap": _interval_stats([delta for _s, _e, delta in swaps], budget_ms),
                "presentation_tick": _interval_stats(
                    [delta for _s, _e, delta in ticks], budget_ms
                ),
                "card_paints": len(card_paints),
                "window_paints": len(window_paints),
                "update_requests": len(updates),
                "card_paints_per_s": len(card_paints) / phase_seconds,
                "window_paints_per_s": len(window_paints) / phase_seconds,
                "mean_card_paint_ratio": (
                    sum(event.ratio for event in card_paints) / max(1, len(card_paints))
                ),
                "mean_window_paint_ratio": (
                    sum(event.ratio for event in window_paints) / max(1, len(window_paints))
                ),
            }

        paint_times = sorted(
            event.at for event in self._boundary_probe.events
            if not event.guarded and event.kind in {"card-paint", "window-paint"}
        )
        paint_associated: list[float] = []
        paint_clean: list[float] = []
        for phase in PHASES:
            for start, end, delta in intervals_by_phase[phase]:
                index = bisect.bisect_left(paint_times, start)
                has_paint = index < len(paint_times) and paint_times[index] <= end
                (paint_associated if has_paint else paint_clean).append(delta)

        associated_stats = _interval_stats(paint_associated, budget_ms)
        clean_stats = _interval_stats(paint_clean, budget_ms)
        background = phase_rows["background-only"]
        cards = phase_rows["card-crossover"]
        background_p99 = float(background["swap"]["p99_ms"])  # type: ignore[index]
        card_p99 = float(cards["swap"]["p99_ms"])  # type: ignore[index]
        phase_penalty = (card_p99 - background_p99) / max(1e-6, background_p99) * 100.0
        bg_paint_rate = float(background["card_paints_per_s"])
        card_paint_rate = float(cards["card_paints_per_s"])
        paint_rate_ratio = card_paint_rate / max(0.1, bg_paint_rate)
        clean_p99 = float(clean_stats["p99_ms"])
        associated_p99 = float(associated_stats["p99_ms"])
        paint_penalty = (associated_p99 - clean_p99) / max(1e-6, clean_p99) * 100.0

        background_samples = int(background["swap"]["samples"])  # type: ignore[index]
        card_samples = int(cards["swap"]["samples"])  # type: ignore[index]
        associated_samples = int(associated_stats["samples"])
        clean_samples = int(clean_stats["samples"])
        if min(background_samples, card_samples) < 60:
            classification = "insufficient-data"
        elif paint_rate_ratio < 1.5:
            classification = "phase-separation-insufficient"
        elif (
            phase_penalty >= 20.0
            and associated_samples >= 20
            and clean_samples >= 20
            and paint_penalty >= 15.0
        ):
            classification = "widget-quick-boundary-contention-likely"
        elif phase_penalty >= 10.0 or paint_penalty >= 10.0:
            classification = "widget-quick-boundary-contention-moderate"
        else:
            classification = "quick-frame-scheduling-not-widget-paint-bound"

        payload = {
            "schema_version": 1,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "git_head": self._git_head(),
            "config": {
                "duration_s": self.duration_s,
                "block_s": self.block_s,
                "guard_s": _GUARD_S,
                "sweep_ms": int(_CONFIG["sweep_ms"]),
                "refresh_hz": refresh_hz,
                "budget_ms": budget_ms,
                "background_points": len(self._background_points),
                "card_points": len(self._points),
            },
            "phases": phase_rows,
            "paint_association": {
                "paint_associated_swap": associated_stats,
                "paint_clean_swap": clean_stats,
                "paint_associated_p99_penalty_percent": paint_penalty,
            },
            "verdict": {
                "classification": classification,
                "card_phase_p99_penalty_percent": phase_penalty,
                "card_paint_rate_ratio": paint_rate_ratio,
                "paint_associated_p99_penalty_percent": paint_penalty,
            },
        }

        output_dir = Path(_CONFIG["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = output_dir / f"real-gui-compositor-boundary-{stamp}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        print("\nREAL GUI COMPOSITOR BOUNDARY SUMMARY")
        print("phase              swap-p95  swap-p99  card-paint/s  window-paint/s")
        print("-" * 74)
        for phase in PHASES:
            row = phase_rows[phase]
            swap = row["swap"]
            print(
                f"{phase:<19} {float(swap['p95_ms']):8.2f} {float(swap['p99_ms']):9.2f} "
                f"{float(row['card_paints_per_s']):12.1f} {float(row['window_paints_per_s']):14.1f}"
            )
        print(
            f"paint-associated p99 {associated_p99:.2f} ms | "
            f"clean p99 {clean_p99:.2f} ms | penalty {paint_penalty:+.1f}%"
        )
        print(f"card-phase p99 penalty {phase_penalty:+.1f}%")
        print(f"VERDICT: {classification}")
        print(f"JSON: {path}")
        return path

    def _finish(self) -> None:
        self._phase_timer.stop()
        if real_perf._MEASURING:
            self._write_boundary_report()
        super()._finish()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure QWidget paint / QQuickWindow swap coupling in the real Listing Studio"
    )
    parser.add_argument("--settle", type=float, default=5.0)
    parser.add_argument("--duration", type=float, default=24.0)
    parser.add_argument("--block", type=float, default=3.0)
    parser.add_argument("--sweep-ms", type=int, default=70)
    parser.add_argument("--output-dir", type=Path, default=Path("perf_results"))
    args = parser.parse_args()

    _CONFIG["block_s"] = max(1.0, args.block)
    _CONFIG["sweep_ms"] = max(40, args.sweep_ms)
    _CONFIG["output_dir"] = args.output_dir
    real_perf.RealGuiProfiler = BoundaryProfiler
    sys.argv = [
        "gui_real_app_perf.py",
        "--variant",
        "current",
        "--settle",
        str(max(1.0, args.settle)),
        "--duration",
        str(max(12.0, args.duration)),
        "--sweep-ms",
        str(max(40, args.sweep_ms)),
        "--output-dir",
        str(args.output_dir),
        "--manual",
    ]
    return int(real_perf.main())


if __name__ == "__main__":
    raise SystemExit(main())
