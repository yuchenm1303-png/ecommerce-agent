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

from PySide6.QtCore import QEventLoop, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QApplication, QFrame, QMainWindow

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


POLICIES = ("dual_full", "incoming_priority", "incoming_only")


@dataclass
class EffectStats:
    draw_ms: list[float] = field(default_factory=list)
    capture_ms: list[float] = field(default_factory=list)


class _TimedCardScaleEffect(_CardScaleEffect):
    """Production renderer with measurement only; rendering semantics are unchanged."""

    def __init__(self, parent: QObject, stats: EffectStats) -> None:
        super().__init__(parent)
        self._stats = stats

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


class _TimedStrategy:
    def __init__(self, window: QMainWindow, card: QFrame, stats: EffectStats) -> None:
        self.window = window
        self.card = card
        self.stats = stats
        self.effect = _TimedCardScaleEffect(card, stats)
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
class _Motion:
    start: float
    target: float
    started: float
    role: str


@dataclass
class ProbeResult:
    policy: str
    round_index: int
    profile: str
    target_hz: float
    frame_budget_ms: float
    samples: int
    frame_p95_ms: float
    frame_p99_ms: float
    frame_max_ms: float
    long_1_5x_rate: float
    cpu_core_percent: float
    draw_count: int
    draw_p95_ms: float
    draw_p99_ms: float
    capture_count: int
    capture_p95_ms: float
    capture_p99_ms: float
    overlap_tick_rate: float
    crossover_events: int


class TailProbeRun(QObject):
    finished = Signal(object)
    PATH = (0, 1, 2, 3, 4, 5, 4, 3, 2, 1)

    def __init__(
        self,
        strategies: list[_TimedStrategy],
        stats: EffectStats,
        *,
        policy: str,
        round_index: int,
        profile: Profile,
        target_hz: float,
        warmup_cycles: int,
        cycles: int,
    ) -> None:
        super().__init__(strategies[0].window)
        self.strategies = strategies
        self.stats = stats
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
        self.motions: dict[int, _Motion] = {}
        self.next_cross = 0.0
        self.last_tick: float | None = None
        self.tick_index = 0
        self.frames: list[float] = []
        self.overlap_ticks = 0
        self.measured_ticks = 0
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
        self.stats.draw_ms.clear()
        self.stats.capture_ms.clear()
        self.overlap_ticks = 0
        self.measured_ticks = 0
        self.last_tick = None
        self.phase = "measure"
        self.measured = 0
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

    def _start_motion(self, index: int, target: float, role: str, now: float) -> None:
        self.strategies[index].begin_transition()
        self.motions[index] = _Motion(self.current[index], target, now, role)

    def _cross(self, now: float) -> None:
        previous = self.active
        self.path_pos = (self.path_pos + 1) % len(self.PATH)
        current = self.PATH[self.path_pos]
        self._retire({previous, current})

        if previous != current:
            if self.policy == "incoming_only":
                self._finish_motion(previous, NORMAL_SCALE) if previous in self.motions else None
                if previous not in self.motions:
                    self.strategies[previous].set_scale(NORMAL_SCALE)
                    self.strategies[previous].end_transition(NORMAL_SCALE)
                    self.current[previous] = NORMAL_SCALE
            else:
                self._start_motion(previous, NORMAL_SCALE, "outgoing", now)
            self._start_motion(current, HOVER_SCALE, "incoming", now)

        self.active = current
        self.next_cross = now + CROSSOVER_DWELL_MS / 1000.0
        if self.phase == "warmup":
            self.warmup_left -= 1
        else:
            self.measured += 1

    def _tick(self) -> None:
        now = time.perf_counter()
        self.tick_index += 1
        if self.phase == "measure":
            if self.last_tick is not None:
                self.frames.append((now - self.last_tick) * 1000.0)
            self.measured_ticks += 1
            if len(self.motions) >= 2:
                self.overlap_ticks += 1
        self.last_tick = now

        for index, motion in tuple(self.motions.items()):
            progress = min(1.0, (now - motion.started) / (TRANSITION_MS / 1000.0))
            should_update = True
            if (
                self.policy == "incoming_priority"
                and motion.role == "outgoing"
                and progress < 1.0
                and self.tick_index % 2
            ):
                should_update = False

            if should_update or progress >= 1.0:
                scale = motion.start + (motion.target - motion.start) * float(
                    self.ease.valueForProgress(progress)
                )
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
            count = max(1, len(self.frames))
            result = ProbeResult(
                policy=self.policy,
                round_index=self.round_index,
                profile=self.profile.name,
                target_hz=self.target_hz,
                frame_budget_ms=budget,
                samples=len(self.frames),
                frame_p95_ms=_percentile(self.frames, 0.95),
                frame_p99_ms=_percentile(self.frames, 0.99),
                frame_max_ms=max(self.frames, default=0.0),
                long_1_5x_rate=long_count / count,
                cpu_core_percent=cpu / wall * 100.0,
                draw_count=len(self.stats.draw_ms),
                draw_p95_ms=_percentile(self.stats.draw_ms, 0.95),
                draw_p99_ms=_percentile(self.stats.draw_ms, 0.99),
                capture_count=len(self.stats.capture_ms),
                capture_p95_ms=_percentile(self.stats.capture_ms, 0.95),
                capture_p99_ms=_percentile(self.stats.capture_ms, 0.99),
                overlap_tick_rate=self.overlap_ticks / max(1, self.measured_ticks),
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
    window.setWindowTitle(f"Card Tail Probe · {policy} · {profile.name}")
    window.show()
    _wait_ms(160)
    stats = EffectStats()
    strategies = [_TimedStrategy(window, card, stats) for card in cards]
    target_hz = max(60.0, min(90.0, _display_hz(app)))
    loop = QEventLoop()
    holder: list[ProbeResult] = []
    runner = TailProbeRun(
        strategies,
        stats,
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
        raise RuntimeError(f"tail probe produced no result for {policy}")
    return holder[0]


def _median(rows: list[ProbeResult], field_name: str) -> float:
    values = [float(getattr(row, field_name)) for row in rows]
    return statistics.median(values) if values else 0.0


def _summary(results: list[ProbeResult]) -> dict[str, dict[str, float]]:
    summary: dict[str, dict[str, float]] = {}
    for policy in POLICIES:
        rows = [row for row in results if row.policy == policy]
        if not rows:
            continue
        summary[policy] = {
            "runs": float(len(rows)),
            "frame_p95_ms": _median(rows, "frame_p95_ms"),
            "frame_p99_ms": _median(rows, "frame_p99_ms"),
            "long_1_5x_rate": _median(rows, "long_1_5x_rate"),
            "cpu_core_percent": _median(rows, "cpu_core_percent"),
            "draw_p95_ms": _median(rows, "draw_p95_ms"),
            "draw_p99_ms": _median(rows, "draw_p99_ms"),
            "capture_p95_ms": _median(rows, "capture_p95_ms"),
            "capture_p99_ms": _median(rows, "capture_p99_ms"),
            "overlap_tick_rate": _median(rows, "overlap_tick_rate"),
        }
    return summary


def _verdict(summary: dict[str, dict[str, float]]) -> dict[str, object]:
    base = summary.get("dual_full")
    priority = summary.get("incoming_priority")
    single = summary.get("incoming_only")
    if not base or not priority or not single:
        return {"classification": "insufficient-data"}

    base_p99 = max(1e-6, base["frame_p99_ms"])
    priority_gain = (base_p99 - priority["frame_p99_ms"]) / base_p99 * 100.0
    single_gain = (base_p99 - single["frame_p99_ms"]) / base_p99 * 100.0
    capture_share = base["capture_p95_ms"] / base_p99 * 100.0

    if priority_gain >= 10.0 and single_gain >= 15.0:
        classification = "concurrent-effect-redraw-dominant"
    elif capture_share >= 25.0 or base["capture_p95_ms"] >= 10.0:
        classification = "capture-significant"
    else:
        classification = "mixed-or-backing-store"
    return {
        "classification": classification,
        "incoming_priority_p99_gain_percent": priority_gain,
        "incoming_only_p99_gain_percent": single_gain,
        "baseline_capture_p95_share_of_frame_p99_percent": capture_share,
    }


def _write_outputs(output_dir: Path, payload: dict[str, object]) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = output_dir / f"gui-card-tail-probe-{stamp}.json"
    csv_path = output_dir / f"gui-card-tail-probe-{stamp}.csv"
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
    parser = argparse.ArgumentParser(
        description="Focused probe for multi-card QGraphicsEffect tail latency"
    )
    parser.add_argument("--profile", choices=sorted(PROFILES), default="large")
    parser.add_argument("--rounds", type=int, default=6)
    parser.add_argument("--warmup-cycles", type=int, default=2)
    parser.add_argument("--cycles", type=int, default=8)
    parser.add_argument("--policies", default="all", help="all or comma-separated policy names")
    parser.add_argument("--output-dir", default="perf_results")
    args = parser.parse_args()

    names = list(POLICIES) if args.policies.lower() == "all" else [
        part.strip() for part in args.policies.split(",") if part.strip()
    ]
    unknown = [name for name in names if name not in POLICIES]
    if unknown:
        raise SystemExit(f"unknown policies: {', '.join(unknown)}")

    app = QApplication.instance() or QApplication(sys.argv)
    profile = PROFILES[args.profile]
    results: list[ProbeResult] = []
    for round_index in range(1, max(1, args.rounds) + 1):
        offset = (round_index - 1) % len(names)
        ordered = names[offset:] + names[:offset]
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
                f"[{round_index:02d}] {policy:<18} "
                f"p95={result.frame_p95_ms:6.2f} p99={result.frame_p99_ms:6.2f} "
                f"draw-p99={result.draw_p99_ms:6.2f} capture-p95={result.capture_p95_ms:6.2f} "
                f"overlap={result.overlap_tick_rate*100:5.1f}% cpu={result.cpu_core_percent:5.1f}%"
            )

    summary = _summary(results)
    verdict = _verdict(summary)
    payload = {
        "schema_version": 1,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "profile": profile.name,
        "config": {
            "rounds": max(1, args.rounds),
            "warmup_cycles": max(0, args.warmup_cycles),
            "cycles": max(1, args.cycles),
            "policies": names,
            "transition_ms": TRANSITION_MS,
            "crossover_dwell_ms": CROSSOVER_DWELL_MS,
        },
        "runs": [asdict(row) for row in results],
        "summary": summary,
        "verdict": verdict,
    }
    json_path, csv_path = _write_outputs(Path(args.output_dir), payload)

    print("\nTAIL-LATENCY SUMMARY")
    for policy, row in summary.items():
        print(
            f"{policy:<18} p95={row['frame_p95_ms']:6.2f} p99={row['frame_p99_ms']:6.2f} "
            f"draw-p99={row['draw_p99_ms']:6.2f} capture-p95={row['capture_p95_ms']:6.2f} "
            f"long={row['long_1_5x_rate']*100:5.1f}%"
        )
    print(f"VERDICT: {verdict.get('classification')}")
    print(f"JSON: {json_path}\nCSV : {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
