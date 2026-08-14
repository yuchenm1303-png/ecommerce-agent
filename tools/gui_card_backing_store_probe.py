from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEvent, QEventLoop, QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QPaintEvent, QPainter
from PySide6.QtWidgets import QApplication, QFrame, QMainWindow, QWidget

from gui.native_visual_style import _CardScaleEffect
from tools.gui_card_perf_lab import (
    CROSSOVER_DWELL_MS,
    HOVER_SCALE,
    NORMAL_SCALE,
    PROFILES,
    TRANSITION_MS,
    Profile,
    _crossover_window,
    _display_hz,
    _ease,
    _percentile,
    _wait_ms,
)


POLICIES = ("production_toggle", "stable_enabled")
_EFFECT_BOUND_SCALE = 1.04
_CONTENT_EDGE_STEP_PX = 0.18
_EPSILON = 1e-5


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


@dataclass
class PaintStats:
    window_paint_ratios: list[float] = field(default_factory=list)
    card_paint_ratios: list[float] = field(default_factory=list)
    update_requests: int = 0

    def reset(self) -> None:
        self.window_paint_ratios.clear()
        self.card_paint_ratios.clear()
        self.update_requests = 0


class PaintProbe(QObject):
    def __init__(self, window: QMainWindow, cards: list[QFrame], stats: PaintStats) -> None:
        super().__init__(window)
        self.window = window
        self.cards = set(cards)
        self.stats = stats
        window.installEventFilter(self)
        central = window.centralWidget()
        if central is not None:
            central.installEventFilter(self)
        for card in cards:
            card.installEventFilter(self)

    @staticmethod
    def _paint_ratio(widget: QWidget, event: QPaintEvent) -> float:
        total = max(1, int(widget.width()) * int(widget.height()))
        rect = event.region().boundingRect()
        area = max(0, int(rect.width())) * max(0, int(rect.height()))
        return min(1.0, float(area) / float(total))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()
        if watched is self.window and event_type == QEvent.Type.UpdateRequest:
            self.stats.update_requests += 1
        elif event_type == QEvent.Type.Paint and isinstance(watched, QWidget):
            paint_event = event  # type: ignore[assignment]
            ratio = self._paint_ratio(watched, paint_event)  # type: ignore[arg-type]
            if watched is self.window or watched is self.window.centralWidget():
                self.stats.window_paint_ratios.append(ratio)
            elif watched in self.cards:
                self.stats.card_paint_ratios.append(ratio)
        return False


class _TimedProductionEffect(_CardScaleEffect):
    def __init__(self, parent: QObject, stats: EffectStats) -> None:
        super().__init__(parent)
        self._stats = stats

    def set_scale(self, scale: float) -> None:
        before = bool(self.isEnabled())
        super().set_scale(scale)
        after = bool(self.isEnabled())
        if before != after:
            self._stats.enable_toggles += 1

    def updateBoundingRect(self) -> None:  # noqa: N802
        self._stats.bounding_updates += 1
        super().updateBoundingRect()

    def _current_composite(self):  # noqa: ANN202
        capture = bool(self._frozen and self._freeze_requested)
        started = time.perf_counter()
        result = super()._current_composite()
        elapsed = (time.perf_counter() - started) * 1000.0
        if capture and result[0] is not None:
            self._stats.capture_ms.append(elapsed)
        return result

    def draw(self, painter: QPainter) -> None:  # type: ignore[override]
        started = time.perf_counter()
        super().draw(painter)
        self._stats.draw_ms.append((time.perf_counter() - started) * 1000.0)


class _StableEnabledEffect(_TimedProductionEffect):
    """Diagnostic: keep the effect enabled; remove transition-time enable/bounds churn."""

    def __init__(self, parent: QObject, stats: EffectStats) -> None:
        super().__init__(parent, stats)
        self.setEnabled(True)

    def set_scale(self, scale: float) -> None:
        requested = max(0.96, min(_EFFECT_BOUND_SCALE, float(scale)))
        exact_rest = abs(requested - NORMAL_SCALE) <= _EPSILON
        if exact_rest:
            requested = NORMAL_SCALE
        else:
            edge_delta_px = self._content_span() * abs(requested - self._scale) * 0.5
            if edge_delta_px < _CONTENT_EDGE_STEP_PX:
                return

        if abs(requested - self._scale) <= _EPSILON:
            if exact_rest and self._frozen:
                self._frozen = False
                self._freeze_requested = False
                self._clear_frozen_source()
            return

        self._scale = requested
        if exact_rest:
            self._frozen = False
            self._freeze_requested = False
            self._clear_frozen_source()
        self.update()


class Strategy:
    def __init__(self, card: QFrame, stats: EffectStats, policy: str) -> None:
        self.card = card
        effect_type = _TimedProductionEffect if policy == "production_toggle" else _StableEnabledEffect
        self.effect = effect_type(card, stats)
        card.setGraphicsEffect(self.effect)

    def begin_transition(self) -> None:
        self.effect.set_frozen(False)
        self.effect.set_frozen(True)

    def set_scale(self, scale: float) -> None:
        self.effect.set_scale(scale)

    def end_transition(self, scale: float) -> None:
        self.effect.set_scale(scale)
        self.effect.set_frozen(False)

    def teardown(self) -> None:
        self.effect.set_frozen(False)
        self.effect.set_scale(NORMAL_SCALE)
        self.card.setGraphicsEffect(None)


@dataclass
class Motion:
    start: float
    target: float
    started: float


@dataclass
class ProbeResult:
    policy: str
    round_index: int
    profile: str
    target_hz: float
    samples: int
    frame_p95_ms: float
    frame_p99_ms: float
    frame_max_ms: float
    long_1_5x_rate: float
    cpu_core_percent: float
    draw_p99_ms: float
    capture_p95_ms: float
    enable_toggles: int
    bounding_updates: int
    update_requests: int
    window_paint_count: int
    window_paint_ratio_p95: float
    card_paint_count: int
    card_paint_ratio_p95: float
    crossover_events: int


class BackingStoreRun(QObject):
    finished = Signal(object)
    PATH = (0, 1, 2, 3, 4, 5, 4, 3, 2, 1)

    def __init__(
        self,
        strategies: list[Strategy],
        effect_stats: EffectStats,
        paint_stats: PaintStats,
        *,
        policy: str,
        round_index: int,
        profile: Profile,
        target_hz: float,
        warmup_cycles: int,
        cycles: int,
    ) -> None:
        super().__init__(strategies[0].card.window())
        self.strategies = strategies
        self.effect_stats = effect_stats
        self.paint_stats = paint_stats
        self.policy = policy
        self.round_index = round_index
        self.profile = profile
        self.target_hz = target_hz
        self.interval_ms = max(4, int(1000.0 / target_hz))
        self.ease = _ease()
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(self.interval_ms)
        self.timer.timeout.connect(self._tick)
        self.warmup_left = max(0, warmup_cycles * len(self.PATH))
        self.measure_total = max(len(self.PATH), cycles * len(self.PATH))
        self.phase = "warmup" if self.warmup_left else "measure"
        self.measured = 0
        self.path_pos = 0
        self.active = 0
        self.current = [NORMAL_SCALE] * len(strategies)
        self.motions: dict[int, Motion] = {}
        self.next_cross = 0.0
        self.last_tick: float | None = None
        self.frames: list[float] = []
        self.wall_started = 0.0
        self.cpu_started = 0.0

    def start(self) -> None:
        if self.phase == "measure":
            self._begin_measurement()
        else:
            self.next_cross = time.perf_counter()
            self.timer.start()

    def _begin_measurement(self) -> None:
        self.frames.clear()
        self.effect_stats.reset()
        self.paint_stats.reset()
        self.phase = "measure"
        self.measured = 0
        self.last_tick = None
        self.wall_started = time.perf_counter()
        self.cpu_started = time.process_time()
        self.next_cross = time.perf_counter()
        self.timer.start()

    def _finish_motion(self, index: int, target: float) -> None:
        self.strategies[index].set_scale(target)
        self.strategies[index].end_transition(target)
        self.current[index] = target
        self.motions.pop(index, None)

    def _retire(self, keep: set[int]) -> None:
        for index in tuple(self.motions):
            if index not in keep:
                self._finish_motion(index, NORMAL_SCALE)

    def _start_motion(self, index: int, target: float, now: float) -> None:
        self.strategies[index].begin_transition()
        self.motions[index] = Motion(self.current[index], target, now)

    def _cross(self, now: float) -> None:
        previous = self.active
        self.path_pos = (self.path_pos + 1) % len(self.PATH)
        current = self.PATH[self.path_pos]
        self._retire({previous, current})
        if previous != current:
            self._start_motion(previous, NORMAL_SCALE, now)
            self._start_motion(current, HOVER_SCALE, now)
        self.active = current
        self.next_cross = now + CROSSOVER_DWELL_MS / 1000.0
        if self.phase == "warmup":
            self.warmup_left -= 1
        else:
            self.measured += 1

    def _tick(self) -> None:
        now = time.perf_counter()
        if self.phase == "measure" and self.last_tick is not None:
            self.frames.append((now - self.last_tick) * 1000.0)
        self.last_tick = now

        for index, motion in tuple(self.motions.items()):
            progress = min(1.0, (now - motion.started) / (TRANSITION_MS / 1000.0))
            scale = motion.start + (motion.target - motion.start) * float(self.ease.valueForProgress(progress))
            self.strategies[index].set_scale(scale)
            self.current[index] = scale
            if progress >= 1.0:
                self._finish_motion(index, motion.target)

        if now >= self.next_cross:
            self._cross(now)

        if self.phase == "warmup" and self.warmup_left <= 0:
            self.timer.stop()
            self._retire(set())
            QTimer.singleShot(100, self._begin_measurement)
            return

        if self.phase == "measure" and self.measured >= self.measure_total:
            self.timer.stop()
            self._retire(set())
            wall = max(1e-9, time.perf_counter() - self.wall_started)
            cpu = max(0.0, time.process_time() - self.cpu_started)
            budget = 1000.0 / self.target_hz
            long_count = sum(value > budget * 1.5 for value in self.frames)
            result = ProbeResult(
                policy=self.policy,
                round_index=self.round_index,
                profile=self.profile.name,
                target_hz=self.target_hz,
                samples=len(self.frames),
                frame_p95_ms=_percentile(self.frames, 0.95),
                frame_p99_ms=_percentile(self.frames, 0.99),
                frame_max_ms=max(self.frames, default=0.0),
                long_1_5x_rate=long_count / max(1, len(self.frames)),
                cpu_core_percent=cpu / wall * 100.0,
                draw_p99_ms=_percentile(self.effect_stats.draw_ms, 0.99),
                capture_p95_ms=_percentile(self.effect_stats.capture_ms, 0.95),
                enable_toggles=self.effect_stats.enable_toggles,
                bounding_updates=self.effect_stats.bounding_updates,
                update_requests=self.paint_stats.update_requests,
                window_paint_count=len(self.paint_stats.window_paint_ratios),
                window_paint_ratio_p95=_percentile(self.paint_stats.window_paint_ratios, 0.95),
                card_paint_count=len(self.paint_stats.card_paint_ratios),
                card_paint_ratio_p95=_percentile(self.paint_stats.card_paint_ratios, 0.95),
                crossover_events=self.measured,
            )
            self.finished.emit(result)


def _run_one(
    app: QApplication,
    *,
    policy: str,
    profile: Profile,
    round_index: int,
    warmup_cycles: int,
    cycles: int,
) -> ProbeResult:
    window, cards = _crossover_window(profile)
    window.setWindowTitle(f"Backing Store Probe · {policy} · {profile.name}")
    window.show()
    _wait_ms(160)
    effect_stats = EffectStats()
    paint_stats = PaintStats()
    paint_probe = PaintProbe(window, cards, paint_stats)
    _ = paint_probe
    strategies = [Strategy(card, effect_stats, policy) for card in cards]
    target_hz = max(60.0, min(90.0, _display_hz(app)))
    loop = QEventLoop()
    holder: list[ProbeResult] = []
    runner = BackingStoreRun(
        strategies,
        effect_stats,
        paint_stats,
        policy=policy,
        round_index=round_index,
        profile=profile,
        target_hz=target_hz,
        warmup_cycles=warmup_cycles,
        cycles=cycles,
    )
    runner.finished.connect(lambda result: (holder.append(result), loop.quit()))
    runner.start()
    loop.exec()
    for strategy in strategies:
        strategy.teardown()
    window.close()
    window.deleteLater()
    app.processEvents()
    if not holder:
        raise RuntimeError(f"backing-store probe produced no result for {policy}")
    return holder[0]


def _median(rows: list[ProbeResult], field_name: str) -> float:
    values = [float(getattr(row, field_name)) for row in rows]
    return statistics.median(values) if values else 0.0


def _summary(results: list[ProbeResult]) -> dict[str, dict[str, float]]:
    output: dict[str, dict[str, float]] = {}
    for policy in POLICIES:
        rows = [row for row in results if row.policy == policy]
        if not rows:
            continue
        output[policy] = {
            "runs": float(len(rows)),
            "frame_p95_ms": _median(rows, "frame_p95_ms"),
            "frame_p99_ms": _median(rows, "frame_p99_ms"),
            "long_1_5x_rate": _median(rows, "long_1_5x_rate"),
            "cpu_core_percent": _median(rows, "cpu_core_percent"),
            "draw_p99_ms": _median(rows, "draw_p99_ms"),
            "capture_p95_ms": _median(rows, "capture_p95_ms"),
            "enable_toggles": _median(rows, "enable_toggles"),
            "bounding_updates": _median(rows, "bounding_updates"),
            "update_requests": _median(rows, "update_requests"),
            "window_paint_count": _median(rows, "window_paint_count"),
            "window_paint_ratio_p95": _median(rows, "window_paint_ratio_p95"),
            "card_paint_count": _median(rows, "card_paint_count"),
            "card_paint_ratio_p95": _median(rows, "card_paint_ratio_p95"),
        }
    return output


def _verdict(summary: dict[str, dict[str, float]]) -> dict[str, object]:
    base = summary.get("production_toggle")
    stable = summary.get("stable_enabled")
    if not base or not stable:
        return {"classification": "insufficient-data"}
    base_p99 = max(1e-6, base["frame_p99_ms"])
    gain = (base_p99 - stable["frame_p99_ms"]) / base_p99 * 100.0
    if gain >= 8.0:
        classification = "effect-enable-disable-churn-significant"
    elif gain <= -8.0:
        classification = "persistent-effect-more-expensive"
    else:
        classification = "effect-state-toggle-not-dominant"
    return {
        "classification": classification,
        "stable_enabled_p99_gain_percent": gain,
        "production_enable_toggles": base["enable_toggles"],
        "production_bounding_updates": base["bounding_updates"],
        "production_window_paint_ratio_p95": base["window_paint_ratio_p95"],
        "stable_window_paint_ratio_p95": stable["window_paint_ratio_p95"],
    }


def _write_outputs(output_dir: Path, payload: dict[str, object]) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = output_dir / f"gui-card-backing-store-probe-{stamp}.json"
    csv_path = output_dir / f"gui-card-backing-store-probe-{stamp}.csv"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = payload["runs"]
    assert isinstance(rows, list)
    fields = list(rows[0].keys()) if rows else []
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return json_path, csv_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe QGraphicsEffect enable/bounds churn and QWidget paint invalidation")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="large")
    parser.add_argument("--rounds", type=int, default=6)
    parser.add_argument("--warmup-cycles", type=int, default=2)
    parser.add_argument("--cycles", type=int, default=8)
    parser.add_argument("--output-dir", default="perf_results")
    args = parser.parse_args()

    app = QApplication.instance() or QApplication(sys.argv)
    profile = PROFILES[args.profile]
    results: list[ProbeResult] = []
    for round_index in range(1, max(1, args.rounds) + 1):
        ordered = POLICIES if round_index % 2 else tuple(reversed(POLICIES))
        for policy in ordered:
            result = _run_one(
                app,
                policy=policy,
                profile=profile,
                round_index=round_index,
                warmup_cycles=max(0, args.warmup_cycles),
                cycles=max(1, args.cycles),
            )
            results.append(result)
            print(
                f"[{round_index:02d}] {policy:<18} p95={result.frame_p95_ms:6.2f} "
                f"p99={result.frame_p99_ms:6.2f} long={result.long_1_5x_rate*100:5.1f}% "
                f"draw-p99={result.draw_p99_ms:6.2f} capture-p95={result.capture_p95_ms:6.2f} "
                f"toggles={result.enable_toggles:3d} window-paint-p95={result.window_paint_ratio_p95*100:5.1f}%"
            )

    summary = _summary(results)
    verdict = _verdict(summary)
    payload = {
        "schema_version": 1,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "config": {
            "profile": profile.name,
            "rounds": max(1, args.rounds),
            "warmup_cycles": max(0, args.warmup_cycles),
            "cycles": max(1, args.cycles),
            "policies": list(POLICIES),
            "crossover_dwell_ms": CROSSOVER_DWELL_MS,
            "transition_ms": TRANSITION_MS,
        },
        "runs": [asdict(row) for row in results],
        "summary": summary,
        "verdict": verdict,
    }
    json_path, csv_path = _write_outputs(Path(args.output_dir), payload)

    print("\nBACKING-STORE PROBE SUMMARY")
    print("policy             p95     p99    long%  draw-p99 capture-p95 toggles bounds window-paint-p95")
    for policy in POLICIES:
        row = summary.get(policy)
        if row is None:
            continue
        print(
            f"{policy:<18} {row['frame_p95_ms']:7.2f} {row['frame_p99_ms']:7.2f} "
            f"{row['long_1_5x_rate']*100:6.1f} {row['draw_p99_ms']:9.2f} {row['capture_p95_ms']:11.2f} "
            f"{row['enable_toggles']:7.0f} {row['bounding_updates']:6.0f} {row['window_paint_ratio_p95']*100:16.1f}%"
        )
    print(f"\nVERDICT: {verdict.get('classification')}")
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    print(f"JSON: {json_path}\nCSV : {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
