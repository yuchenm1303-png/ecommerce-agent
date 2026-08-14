from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from PySide6 import __version__ as PYSIDE_VERSION
from PySide6.QtCore import QEasingCurve, QEventLoop, QObject, QPoint, QPointF, QRectF, Qt, QTimer, Signal, qVersion
from PySide6.QtGui import QPainter, QPixmap, QTransform
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGraphicsEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui.native_visual_style import _CardScaleEffect


TRANSITION_MS = 300
HOLD_MS = 60
CROSSOVER_DWELL_MS = 70
NORMAL_SCALE = 1.00
HOVER_SCALE = 1.02
MAX_SCALE = 1.04
_CONTENT_EDGE_STEP_PX = 0.18
_EPSILON = 1e-5


@dataclass(frozen=True)
class Profile:
    name: str
    width: int
    height: int
    field_rows: int
    table_rows: int
    table_cols: int


PROFILES = {
    "medium": Profile("medium", 760, 430, 6, 12, 5),
    "large": Profile("large", 980, 560, 9, 20, 6),
    "huge": Profile("huge", 1180, 680, 12, 32, 7),
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


def _ease() -> QEasingCurve:
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(QPointF(0.25, 0.10), QPointF(0.25, 1.00), QPointF(1.00, 1.00))
    return curve


def _wait_ms(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(max(0, int(ms)), loop.quit)
    loop.exec()


_CARD_STYLE = (
    "QFrame#benchmarkCard { background: #14263a; border-radius: 8px; }"
    "QLabel { color: #e7f0fa; }"
    "QLineEdit, QComboBox, QPlainTextEdit, QTableWidget {"
    " background: #0d1c2d; color: #e7f0fa; border: 1px solid #29415a; border-radius: 5px; }"
    "QPushButton { background: #203b57; color: white; border: 0; border-radius: 5px; padding: 5px 10px; }"
)


def _card(profile: Profile, title_text: str) -> QFrame:
    card = QFrame()
    card.setObjectName("benchmarkCard")
    card.setFixedSize(profile.width, profile.height)
    card.setStyleSheet(_CARD_STYLE)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(8)

    title = QLabel(title_text)
    title.setStyleSheet("font-size: 17px; font-weight: 700;")
    layout.addWidget(title)

    form = QGridLayout()
    form.setHorizontalSpacing(8)
    form.setVerticalSpacing(5)
    for row in range(profile.field_rows):
        form.addWidget(QLabel(f"Field {row + 1}"), row, 0)
        form.addWidget(QLineEdit(f"benchmark value {row + 1} · Makro product metadata"), row, 1)
        combo = QComboBox()
        combo.addItems(["READY", "MISSING", "CONFLICT", "BLOCKED"])
        form.addWidget(combo, row, 2)
    layout.addLayout(form)

    buttons = QHBoxLayout()
    for text in ("Resolve", "Preview", "Run Section", "Save", "Diagnostics"):
        buttons.addWidget(QPushButton(text))
    buttons.addStretch(1)
    layout.addLayout(buttons)

    table = QTableWidget(profile.table_rows, profile.table_cols)
    table.setHorizontalHeaderLabels([f"Column {i + 1}" for i in range(profile.table_cols)])
    for row in range(profile.table_rows):
        for col in range(profile.table_cols):
            table.setItem(row, col, QTableWidgetItem(f"R{row + 1} C{col + 1} · value"))
    layout.addWidget(table, 1)

    log = QPlainTextEdit()
    log.setMaximumHeight(76)
    log.setPlainText("\n".join(f"[{i:02d}] listing telemetry · stable synthetic workload" for i in range(8)))
    layout.addWidget(log)
    return card


def _single_window(profile: Profile) -> tuple[QMainWindow, QFrame]:
    window = QMainWindow()
    central = QWidget()
    window.setCentralWidget(central)
    outer = QVBoxLayout(central)
    outer.setContentsMargins(28, 28, 28, 28)
    outer.addStretch(1)
    card = _card(profile, "Representative Listing Card · Inputs / Buttons / Table / Log")
    outer.addWidget(card, 0, Qt.AlignmentFlag.AlignHCenter)
    outer.addStretch(1)
    window.resize(max(1280, profile.width + 100), max(820, profile.height + 100))
    return window, card


def _crossover_window(profile: Profile) -> tuple[QMainWindow, list[QFrame]]:
    compact = Profile(
        f"{profile.name}-crossover",
        min(540, max(420, profile.width // 2)),
        min(280, max(235, profile.height // 2)),
        min(3, profile.field_rows),
        min(5, profile.table_rows),
        min(4, profile.table_cols),
    )
    window = QMainWindow()
    central = QWidget()
    window.setCentralWidget(central)
    grid = QGridLayout(central)
    grid.setContentsMargins(22, 22, 22, 22)
    grid.setHorizontalSpacing(18)
    grid.setVerticalSpacing(16)
    cards: list[QFrame] = []
    for index in range(6):
        card = _card(compact, f"Listing Card {index + 1} · crossover workload")
        grid.addWidget(card, index // 2, index % 2)
        cards.append(card)
    window.resize(compact.width * 2 + 90, compact.height * 3 + 110)
    return window, cards


class _FrozenEffect(QGraphicsEffect):
    mode = "target_rect"
    smooth = True

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.scale = NORMAL_SCALE
        self.frozen = False
        self.capture_requested = False
        self.pixmap: QPixmap | None = None
        self.offset = QPoint()
        self.center: QPointF | None = None
        self.setEnabled(False)

    def _clear(self) -> None:
        self.pixmap = None
        self.offset = QPoint()
        self.center = None

    def set_frozen(self, frozen: bool) -> None:
        frozen = bool(frozen)
        if frozen == self.frozen:
            return
        self.frozen = frozen
        self.capture_requested = frozen
        self._clear()
        if self.isEnabled():
            self.update()

    def set_scale(self, scale: float) -> None:
        requested = max(0.96, min(MAX_SCALE, float(scale)))
        exact_rest = abs(requested - NORMAL_SCALE) <= _EPSILON
        if exact_rest:
            requested = NORMAL_SCALE
        else:
            frame = self.parent()
            span = max(1.0, float(frame.width()), float(frame.height()))
            if span * abs(requested - self.scale) * 0.5 < _CONTENT_EDGE_STEP_PX:
                return
        if abs(requested - self.scale) <= _EPSILON:
            return
        self.scale = requested
        active = abs(requested - NORMAL_SCALE) > 1e-4
        if self.isEnabled() != active:
            self.setEnabled(active)
            self.updateBoundingRect()
        if not active:
            self.frozen = False
            self.capture_requested = False
            self._clear()
        self.update()

    def boundingRectFor(self, source_rect: QRectF) -> QRectF:  # noqa: N802
        if not self.isEnabled():
            return QRectF(source_rect)
        center = source_rect.center()
        half_w = source_rect.width() * MAX_SCALE * 0.5
        half_h = source_rect.height() * MAX_SCALE * 0.5
        return QRectF(center.x() - half_w, center.y() - half_h, half_w * 2.0, half_h * 2.0)

    def _composite(self) -> tuple[QPixmap | None, QPoint, QPointF | None]:
        if self.frozen and not self.capture_requested and self.pixmap is not None and self.center is not None:
            return self.pixmap, self.offset, self.center
        offset = QPoint()
        pixmap = self.sourcePixmap(Qt.CoordinateSystem.LogicalCoordinates, offset, QGraphicsEffect.PixmapPadMode.NoPad)
        if pixmap.isNull():
            return None, QPoint(), None
        center = self.sourceBoundingRect(Qt.CoordinateSystem.LogicalCoordinates).center()
        if self.frozen:
            self.pixmap = pixmap
            self.offset = QPoint(offset)
            self.center = QPointF(center)
            self.capture_requested = False
        return pixmap, offset, center

    def draw(self, painter: QPainter) -> None:  # type: ignore[override]
        if abs(self.scale - NORMAL_SCALE) <= 1e-4:
            self.drawSource(painter)
            return
        pixmap, offset, center = self._composite()
        if pixmap is None or center is None:
            self.drawSource(painter)
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.smooth)
        if self.mode == "transform":
            transform = QTransform()
            transform.translate(center.x(), center.y())
            transform.scale(self.scale, self.scale)
            transform.translate(-center.x(), -center.y())
            painter.setWorldTransform(transform, True)
            painter.drawPixmap(offset, pixmap)
        else:
            dpr = max(1e-6, float(pixmap.devicePixelRatio()))
            width = float(pixmap.width()) / dpr * self.scale
            height = float(pixmap.height()) / dpr * self.scale
            dest = QRectF(center.x() - width * 0.5, center.y() - height * 0.5, width, height)
            painter.drawPixmap(dest, pixmap, QRectF(pixmap.rect()))
        painter.restore()


class _TransformEffect(_FrozenEffect):
    mode = "transform"


class _FastEffect(_FrozenEffect):
    smooth = False


class Strategy:
    name = "base"
    eligible_default = True
    preferred_hz: float | None = None

    def __init__(self, window: QMainWindow, card: QFrame) -> None:
        self.window = window
        self.card = card

    def setup(self) -> None:
        pass

    def begin_transition(self, _current: float, _target: float) -> None:
        pass

    def set_scale(self, _scale: float) -> None:
        pass

    def end_transition(self, _scale: float) -> None:
        pass

    def teardown(self) -> None:
        self.card.setGraphicsEffect(None)


class BaselineFrozen(Strategy):
    name = "baseline_frozen"

    def setup(self) -> None:
        self.effect = _CardScaleEffect(self.card)
        self.card.setGraphicsEffect(self.effect)

    def begin_transition(self, _current: float, _target: float) -> None:
        self.effect.set_frozen(False)
        self.effect.set_frozen(True)

    def set_scale(self, scale: float) -> None:
        self.effect.set_scale(scale)

    def end_transition(self, scale: float) -> None:
        self.effect.set_scale(scale)
        self.effect.set_frozen(False)


class _LocalFrozenStrategy(Strategy):
    effect_type = _FrozenEffect

    def setup(self) -> None:
        self.effect = self.effect_type(self.card)
        self.card.setGraphicsEffect(self.effect)

    def begin_transition(self, _current: float, _target: float) -> None:
        self.effect.set_frozen(False)
        self.effect.set_frozen(True)

    def set_scale(self, scale: float) -> None:
        self.effect.set_scale(scale)

    def end_transition(self, scale: float) -> None:
        self.effect.set_scale(scale)
        self.effect.set_frozen(False)


class FrozenTargetRect(_LocalFrozenStrategy):
    name = "frozen_target_rect"


class FrozenTransform(_LocalFrozenStrategy):
    name = "frozen_transform"
    effect_type = _TransformEffect


class FrozenFast(_LocalFrozenStrategy):
    name = "frozen_fast"
    eligible_default = False
    effect_type = _FastEffect


class Quantized12(BaselineFrozen):
    name = "quantized_12"

    def set_scale(self, scale: float) -> None:
        levels = 12
        normalized = (scale - NORMAL_SCALE) / (HOVER_SCALE - NORMAL_SCALE)
        step = round(max(0.0, min(1.0, normalized)) * (levels - 1)) / (levels - 1)
        super().set_scale(NORMAL_SCALE + (HOVER_SCALE - NORMAL_SCALE) * step)


class Baseline60(BaselineFrozen):
    name = "baseline_60hz"
    preferred_hz = 60.0


class Baseline72(BaselineFrozen):
    name = "baseline_72hz"
    preferred_hz = 72.0


class Baseline90(BaselineFrozen):
    name = "baseline_90hz"
    preferred_hz = 90.0


class NoScale(Strategy):
    name = "no_scale_control"
    eligible_default = False


STRATEGIES: dict[str, type[Strategy]] = {
    cls.name: cls
    for cls in (BaselineFrozen, FrozenTargetRect, FrozenTransform, FrozenFast, Quantized12, Baseline60, Baseline72, Baseline90, NoScale)
}


@dataclass
class RunResult:
    strategy: str
    eligible_default: bool
    scenario: str
    round_index: int
    profile: str
    target_hz: float
    timer_interval_ms: int
    frame_budget_ms: float
    setup_ms: float
    wall_seconds: float
    cpu_seconds: float
    cpu_core_percent: float
    samples: int
    frame_median_ms: float
    frame_p95_ms: float
    frame_p99_ms: float
    frame_max_ms: float
    long_1_5x_count: int
    long_1_5x_rate: float
    long_2x_count: int
    long_2x_rate: float
    tick_work_median_ms: float
    tick_work_p95_ms: float
    transition_prepare_median_ms: float
    transition_prepare_p95_ms: float
    transition_start_gap_median_ms: float
    transition_start_gap_p95_ms: float
    event_count: int


def _display_hz(app: QApplication) -> float:
    screen = app.primaryScreen()
    if screen is None:
        return 60.0
    try:
        value = float(screen.refreshRate())
        return value if 30.0 <= value <= 500.0 else 60.0
    except (RuntimeError, TypeError, ValueError):
        return 60.0


def _strategy_hz(app: QApplication, strategy: Strategy) -> float:
    display = _display_hz(app)
    if strategy.preferred_hz is None:
        return max(60.0, min(90.0, display))
    return max(30.0, min(float(strategy.preferred_hz), max(60.0, display)))


def _result(strategy: Strategy, scenario: str, round_index: int, profile: Profile, target_hz: float, interval_ms: int,
            frames: list[float], work: list[float], prepare: list[float], gaps: list[float], wall: float, cpu: float,
            events: int, setup_ms: float) -> RunResult:
    budget = 1000.0 / target_hz
    long_1_5 = sum(value > budget * 1.5 for value in frames)
    long_2 = sum(value > budget * 2.0 for value in frames)
    count = max(1, len(frames))
    return RunResult(
        strategy.name, strategy.eligible_default, scenario, round_index, profile.name, target_hz, interval_ms, budget,
        setup_ms, wall, cpu, cpu / max(wall, 1e-9) * 100.0, len(frames),
        statistics.median(frames) if frames else 0.0, _percentile(frames, 0.95), _percentile(frames, 0.99),
        max(frames, default=0.0), int(long_1_5), long_1_5 / count, int(long_2), long_2 / count,
        statistics.median(work) if work else 0.0, _percentile(work, 0.95),
        statistics.median(prepare) if prepare else 0.0, _percentile(prepare, 0.95),
        statistics.median(gaps) if gaps else 0.0, _percentile(gaps, 0.95), events,
    )


class SingleRun(QObject):
    finished = Signal(object)

    def __init__(self, strategy: Strategy, round_index: int, profile: Profile, target_hz: float, warmup: int, cycles: int) -> None:
        super().__init__(strategy.window)
        self.strategy = strategy
        self.round_index = round_index
        self.profile = profile
        self.target_hz = target_hz
        self.interval_ms = max(4, int(1000.0 / target_hz))
        self.warmup_left = max(0, warmup * 2)
        self.measure_total = max(2, cycles * 2)
        self.measured = 0
        self.phase = "warmup" if self.warmup_left else "measure"
        self.ease = _ease()
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(self.interval_ms)
        self.timer.timeout.connect(self._tick)
        self.current = NORMAL_SCALE
        self.start_scale = NORMAL_SCALE
        self.target = HOVER_SCALE
        self.started = 0.0
        self.next_start = 0.0
        self.moving = False
        self.last_tick: float | None = None
        self.mark_gap = False
        self.frames: list[float] = []
        self.work: list[float] = []
        self.prepare: list[float] = []
        self.gaps: list[float] = []
        self.wall = 0.0
        self.cpu = 0.0

    def start(self) -> None:
        if self.phase == "measure":
            self._begin_measure()
        else:
            self._begin_transition()
            self.timer.start()

    def _begin_measure(self) -> None:
        self.frames.clear(); self.work.clear(); self.prepare.clear(); self.gaps.clear()
        self.phase = "measure"; self.measured = 0; self.last_tick = None
        self.wall = time.perf_counter(); self.cpu = time.process_time()
        self._begin_transition(); self.timer.start()

    def _begin_transition(self) -> None:
        self.start_scale = self.current
        self.target = HOVER_SCALE if self.current <= 1.001 else NORMAL_SCALE
        t0 = time.perf_counter(); self.strategy.begin_transition(self.start_scale, self.target)
        if self.phase == "measure":
            self.prepare.append((time.perf_counter() - t0) * 1000.0); self.mark_gap = True
        self.started = time.perf_counter(); self.moving = True

    def _tick(self) -> None:
        now = time.perf_counter()
        if self.phase == "measure" and self.last_tick is not None:
            gap = (now - self.last_tick) * 1000.0; self.frames.append(gap)
            if self.mark_gap: self.gaps.append(gap); self.mark_gap = False
        self.last_tick = now
        if not self.moving:
            if now >= self.next_start: self._begin_transition()
            return
        progress = min(1.0, (now - self.started) / (TRANSITION_MS / 1000.0))
        scale = self.start_scale + (self.target - self.start_scale) * float(self.ease.valueForProgress(progress))
        t0 = time.perf_counter(); self.strategy.set_scale(scale)
        if progress >= 1.0:
            self.current = self.target; self.strategy.end_transition(self.current); self.moving = False
            self.next_start = now + HOLD_MS / 1000.0
            if self.phase == "warmup": self.warmup_left -= 1
            else: self.measured += 1
        if self.phase == "measure": self.work.append((time.perf_counter() - t0) * 1000.0)
        if self.phase == "warmup" and self.warmup_left <= 0 and not self.moving:
            self.timer.stop(); QTimer.singleShot(120, self._begin_measure); return
        if self.phase == "measure" and self.measured >= self.measure_total and not self.moving:
            self.timer.stop()
            wall = max(1e-9, time.perf_counter() - self.wall); cpu = max(0.0, time.process_time() - self.cpu)
            self.finished.emit(_result(self.strategy, "single", self.round_index, self.profile, self.target_hz, self.interval_ms,
                                       self.frames, self.work, self.prepare, self.gaps, wall, cpu, self.measured,
                                       float(getattr(self.strategy, "_setup_ms", 0.0))))


@dataclass
class _Motion:
    start: float
    target: float
    started: float


class CrossoverRun(QObject):
    finished = Signal(object)
    PATH = (0, 1, 2, 3, 4, 5, 4, 3, 2, 1)

    def __init__(self, strategies: list[Strategy], round_index: int, profile: Profile, target_hz: float, warmup: int, cycles: int) -> None:
        super().__init__(strategies[0].window)
        self.strategies = strategies; self.round_index = round_index; self.profile = profile; self.target_hz = target_hz
        self.interval_ms = max(4, int(1000.0 / target_hz)); self.ease = _ease()
        self.timer = QTimer(self); self.timer.setTimerType(Qt.TimerType.PreciseTimer); self.timer.setInterval(self.interval_ms)
        self.timer.timeout.connect(self._tick)
        self.warmup_left = max(0, warmup * len(self.PATH)); self.measure_total = max(len(self.PATH), cycles * len(self.PATH))
        self.phase = "warmup" if self.warmup_left else "measure"; self.measured = 0; self.path_pos = 0; self.active = 0
        self.current = [NORMAL_SCALE] * len(strategies); self.motions: dict[int, _Motion] = {}; self.next_cross = 0.0
        self.last_tick: float | None = None; self.mark_gap = False
        self.frames: list[float] = []; self.work: list[float] = []; self.prepare: list[float] = []; self.gaps: list[float] = []
        self.wall = 0.0; self.cpu = 0.0

    def start(self) -> None:
        if self.phase == "measure": self._begin_measure()
        else: self.next_cross = time.perf_counter(); self.timer.start()

    def _begin_measure(self) -> None:
        self.frames.clear(); self.work.clear(); self.prepare.clear(); self.gaps.clear(); self.last_tick = None
        self.phase = "measure"; self.measured = 0; self.wall = time.perf_counter(); self.cpu = time.process_time()
        self.next_cross = time.perf_counter(); self.timer.start()

    def _retire(self, keep: set[int]) -> None:
        for index in tuple(self.motions):
            if index in keep: continue
            self.strategies[index].set_scale(NORMAL_SCALE); self.strategies[index].end_transition(NORMAL_SCALE)
            self.current[index] = NORMAL_SCALE; self.motions.pop(index, None)

    def _start_motion(self, index: int, target: float, now: float) -> float:
        t0 = time.perf_counter(); self.strategies[index].begin_transition(self.current[index], target)
        elapsed = (time.perf_counter() - t0) * 1000.0
        self.motions[index] = _Motion(self.current[index], target, now); return elapsed

    def _cross(self, now: float) -> None:
        previous = self.active; self.path_pos = (self.path_pos + 1) % len(self.PATH); current = self.PATH[self.path_pos]
        self._retire({previous, current}); prepare = 0.0
        if previous != current:
            prepare += self._start_motion(previous, NORMAL_SCALE, now); prepare += self._start_motion(current, HOVER_SCALE, now)
        self.active = current; self.next_cross = now + CROSSOVER_DWELL_MS / 1000.0
        if self.phase == "warmup": self.warmup_left -= 1
        else: self.measured += 1; self.prepare.append(prepare); self.mark_gap = True

    def _tick(self) -> None:
        now = time.perf_counter()
        if self.phase == "measure" and self.last_tick is not None:
            gap = (now - self.last_tick) * 1000.0; self.frames.append(gap)
            if self.mark_gap: self.gaps.append(gap); self.mark_gap = False
        self.last_tick = now; t0 = time.perf_counter()
        for index, motion in tuple(self.motions.items()):
            progress = min(1.0, (now - motion.started) / (TRANSITION_MS / 1000.0))
            scale = motion.start + (motion.target - motion.start) * float(self.ease.valueForProgress(progress))
            self.strategies[index].set_scale(scale); self.current[index] = scale
            if progress >= 1.0:
                self.strategies[index].end_transition(motion.target); self.current[index] = motion.target; self.motions.pop(index, None)
        if now >= self.next_cross: self._cross(now)
        if self.phase == "measure": self.work.append((time.perf_counter() - t0) * 1000.0)
        if self.phase == "warmup" and self.warmup_left <= 0:
            self.timer.stop(); self._retire(set()); QTimer.singleShot(120, self._begin_measure); return
        if self.phase == "measure" and self.measured >= self.measure_total:
            self.timer.stop(); self._retire(set())
            wall = max(1e-9, time.perf_counter() - self.wall); cpu = max(0.0, time.process_time() - self.cpu)
            setup = sum(float(getattr(item, "_setup_ms", 0.0)) for item in self.strategies)
            self.finished.emit(_result(self.strategies[0], "crossover", self.round_index, self.profile, self.target_hz,
                                       self.interval_ms, self.frames, self.work, self.prepare, self.gaps, wall, cpu,
                                       self.measured, setup))


class DemoAnimator(QObject):
    def __init__(self, strategies: list[Strategy], hz: float) -> None:
        super().__init__(strategies[0].window); self.strategies = strategies; self.ease = _ease(); self.current = NORMAL_SCALE
        self.start_scale = NORMAL_SCALE; self.target = HOVER_SCALE; self.started = 0.0; self.moving = False; self.next_start = 0.0
        self.timer = QTimer(self); self.timer.setTimerType(Qt.TimerType.PreciseTimer); self.timer.setInterval(max(4, int(1000.0 / hz)))
        self.timer.timeout.connect(self._tick)

    def start(self) -> None: self._begin(); self.timer.start()

    def _begin(self) -> None:
        self.start_scale = self.current; self.target = HOVER_SCALE if self.current <= 1.001 else NORMAL_SCALE
        for strategy in self.strategies: strategy.begin_transition(self.start_scale, self.target)
        self.started = time.perf_counter(); self.moving = True

    def _tick(self) -> None:
        now = time.perf_counter()
        if not self.moving:
            if now >= self.next_start: self._begin()
            return
        progress = min(1.0, (now - self.started) / (TRANSITION_MS / 1000.0))
        scale = self.start_scale + (self.target - self.start_scale) * float(self.ease.valueForProgress(progress))
        for strategy in self.strategies: strategy.set_scale(scale)
        if progress >= 1.0:
            self.current = self.target
            for strategy in self.strategies: strategy.end_transition(self.current)
            self.moving = False; self.next_start = now + 0.35


def _run_single(app: QApplication, name: str, profile: Profile, round_index: int, warmup: int, cycles: int) -> RunResult:
    window, card = _single_window(profile); window.show(); _wait_ms(180)
    strategy = STRATEGIES[name](window, card); t0 = time.perf_counter(); strategy.setup(); strategy._setup_ms = (time.perf_counter() - t0) * 1000.0
    _wait_ms(80); loop = QEventLoop(); holder: list[RunResult] = []
    runner = SingleRun(strategy, round_index, profile, _strategy_hz(app, strategy), warmup, cycles)
    runner.finished.connect(lambda result: (holder.append(result), loop.quit())); runner.start(); loop.exec()
    strategy.teardown(); window.close(); window.deleteLater(); app.processEvents()
    if not holder: raise RuntimeError(f"no result for {name}")
    return holder[0]


def _run_crossover(app: QApplication, name: str, profile: Profile, round_index: int, warmup: int, cycles: int) -> RunResult:
    window, cards = _crossover_window(profile); window.show(); _wait_ms(180); strategies: list[Strategy] = []
    for card in cards:
        strategy = STRATEGIES[name](window, card); t0 = time.perf_counter(); strategy.setup(); strategy._setup_ms = (time.perf_counter() - t0) * 1000.0
        strategies.append(strategy)
    _wait_ms(80); loop = QEventLoop(); holder: list[RunResult] = []
    runner = CrossoverRun(strategies, round_index, profile, _strategy_hz(app, strategies[0]), warmup, cycles)
    runner.finished.connect(lambda result: (holder.append(result), loop.quit())); runner.start(); loop.exec()
    for strategy in strategies: strategy.teardown()
    window.close(); window.deleteLater(); app.processEvents()
    if not holder: raise RuntimeError(f"no crossover result for {name}")
    return holder[0]


def _demo(app: QApplication, name: str, profile: Profile) -> int:
    window, card = _single_window(profile); strategy = STRATEGIES[name](window, card); strategy.setup(); window.show()
    animator = DemoAnimator([strategy], _strategy_hz(app, strategy)); animator.start()
    window.setWindowTitle(f"DEMO · {name} · interact while 1.00↔1.02 loops")
    rc = app.exec(); animator.timer.stop(); strategy.teardown(); return rc


def _compare_demo(app: QApplication, candidate: str, profile: Profile) -> int:
    p = Profile(f"{profile.name}-compare", min(760, profile.width), min(520, profile.height), min(7, profile.field_rows), min(14, profile.table_rows), min(5, profile.table_cols))
    window = QMainWindow(); central = QWidget(); window.setCentralWidget(central); row = QHBoxLayout(central); row.setContentsMargins(20, 20, 20, 20)
    strategies: list[Strategy] = []
    for label_text, name in (("PRODUCTION BASELINE", "baseline_frozen"), ("CANDIDATE", candidate)):
        column = QVBoxLayout(); label = QLabel(f"{label_text} · {name}"); label.setStyleSheet("font-size: 16px; font-weight: 700;")
        column.addWidget(label); card = _card(p, f"{name} · synchronized parity card"); column.addWidget(card); row.addLayout(column)
        strategy = STRATEGIES[name](window, card); strategy.setup(); strategies.append(strategy)
    window.resize(p.width * 2 + 90, p.height + 110); window.setWindowTitle(f"SIDE-BY-SIDE PARITY · baseline_frozen vs {candidate}")
    window.show(); animator = DemoAnimator(strategies, min(_strategy_hz(app, item) for item in strategies)); animator.start()
    rc = app.exec(); animator.timer.stop()
    for strategy in strategies: strategy.teardown()
    return rc


def _system_info(app: QApplication) -> dict[str, object]:
    screen = app.primaryScreen()
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(), "platform": platform.platform(), "python": sys.version.split()[0],
        "pyside": PYSIDE_VERSION, "qt": qVersion(), "qsg_render_loop": os.environ.get("QSG_RENDER_LOOP", ""),
        "screen_name": screen.name() if screen else "", "screen_refresh_hz": float(screen.refreshRate()) if screen else 0.0,
        "device_pixel_ratio": float(screen.devicePixelRatio()) if screen else 1.0,
        "screen_size": [screen.size().width(), screen.size().height()] if screen else [0, 0],
    }


def _write(output_dir: Path, payload: dict[str, object]) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True); stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = output_dir / f"gui-card-perf-{stamp}.json"; csv_path = output_dir / f"gui-card-perf-{stamp}.csv"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = payload["runs"]; assert isinstance(rows, list); fields = list(rows[0].keys()) if rows else []
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    return json_path, csv_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Listing Studio card motion microarchitecture benchmark")
    parser.add_argument("--strategies", default="all", help="all or comma-separated strategy names")
    parser.add_argument("--scenario", choices=("single", "crossover", "both"), default="both")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="large")
    parser.add_argument("--rounds", type=int, default=5); parser.add_argument("--warmup-cycles", type=int, default=2); parser.add_argument("--cycles", type=int, default=8)
    parser.add_argument("--output-dir", default="perf_results")
    parser.add_argument("--demo", choices=sorted(STRATEGIES)); parser.add_argument("--compare-demo", choices=sorted(STRATEGIES))
    args = parser.parse_args(); app = QApplication.instance() or QApplication(sys.argv); profile = PROFILES[args.profile]
    if args.demo: return _demo(app, args.demo, profile)
    if args.compare_demo: return _compare_demo(app, args.compare_demo, profile)
    names = list(STRATEGIES) if args.strategies.lower() == "all" else [part.strip() for part in args.strategies.split(",") if part.strip()]
    unknown = [name for name in names if name not in STRATEGIES]
    if unknown: raise SystemExit(f"unknown strategies: {', '.join(unknown)}")
    scenarios = ("single", "crossover") if args.scenario == "both" else (args.scenario,); results: list[RunResult] = []
    for round_index in range(1, max(1, args.rounds) + 1):
        offset = (round_index - 1) % len(names); ordered = names[offset:] + names[:offset]
        for scenario in scenarios:
            for name in ordered:
                runner = _run_single if scenario == "single" else _run_crossover
                result = runner(app, name, profile, round_index, max(0, args.warmup_cycles), max(1, args.cycles)); results.append(result)
                print(f"[{round_index:02d}] {scenario:<9} {name:<19} hz={result.target_hz:5.1f} p95={result.frame_p95_ms:6.2f} p99={result.frame_p99_ms:6.2f} long={result.long_1_5x_rate*100:5.1f}% cpu={result.cpu_core_percent:5.1f}% start-gap={result.transition_start_gap_p95_ms:6.2f}")
    payload = {"schema_version": 2, "system": _system_info(app), "config": {"scenario": args.scenario, "profile": profile.name, "profile_geometry": asdict(profile), "rounds": max(1, args.rounds), "warmup_cycles": max(0, args.warmup_cycles), "cycles": max(1, args.cycles), "transition_ms": TRANSITION_MS, "hold_ms": HOLD_MS, "crossover_dwell_ms": CROSSOVER_DWELL_MS, "normal_scale": NORMAL_SCALE, "hover_scale": HOVER_SCALE, "strategies": names}, "runs": [asdict(item) for item in results]}
    json_path, csv_path = _write(Path(args.output_dir), payload); print(f"\nJSON: {json_path}\nCSV : {csv_path}")
    print("Next: analyzer first; only parity-check candidates with a meaningful baseline win, preferably via --compare-demo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
