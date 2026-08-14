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
from typing import Callable

from PySide6 import __version__ as PYSIDE_VERSION
from PySide6.QtCore import QEasingCurve, QEventLoop, QObject, QPoint, QPointF, QRectF, Qt, QTimer, Signal, qVersion
from PySide6.QtGui import QColor, QPainter, QPixmap, QSurfaceFormat
from PySide6.QtOpenGLWidgets import QOpenGLWidget
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
NORMAL_SCALE = 1.00
HOVER_SCALE = 1.02
MAX_SCALE = 1.04


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
    curve.addCubicBezierSegment(
        QPointF(0.25, 0.10),
        QPointF(0.25, 1.00),
        QPointF(1.00, 1.00),
    )
    return curve


def _wait_ms(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(max(0, int(ms)), loop.quit)
    loop.exec()


def _build_card(profile: Profile) -> tuple[QMainWindow, QFrame]:
    window = QMainWindow()
    window.setWindowTitle(f"GUI Card Performance Lab · {profile.name}")
    central = QWidget()
    window.setCentralWidget(central)
    outer = QVBoxLayout(central)
    outer.setContentsMargins(28, 28, 28, 28)
    outer.addStretch(1)

    card = QFrame()
    card.setObjectName("benchmarkCard")
    card.setFixedSize(profile.width, profile.height)
    card.setStyleSheet(
        "QFrame#benchmarkCard { background: #14263a; border-radius: 8px; }"
        "QLabel { color: #e7f0fa; }"
        "QLineEdit, QComboBox, QPlainTextEdit, QTableWidget {"
        " background: #0d1c2d; color: #e7f0fa; border: 1px solid #29415a; border-radius: 5px; }"
        "QPushButton { background: #203b57; color: white; border: 0; border-radius: 5px; padding: 5px 10px; }"
    )
    layout = QVBoxLayout(card)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(8)

    title = QLabel("Representative Listing Card · Inputs / Buttons / Table / Log")
    title.setStyleSheet("font-size: 17px; font-weight: 700;")
    layout.addWidget(title)

    form = QGridLayout()
    form.setHorizontalSpacing(8)
    form.setVerticalSpacing(5)
    for row in range(profile.field_rows):
        label = QLabel(f"Field {row + 1}")
        edit = QLineEdit(f"benchmark value {row + 1} · Makro product metadata")
        combo = QComboBox()
        combo.addItems(["READY", "MISSING", "CONFLICT", "BLOCKED"])
        form.addWidget(label, row, 0)
        form.addWidget(edit, row, 1)
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

    outer.addWidget(card, 0, Qt.AlignmentFlag.AlignHCenter)
    outer.addStretch(1)
    window.resize(max(1280, profile.width + 100), max(820, profile.height + 100))
    return window, card


class _LiveScaleEffect(QGraphicsEffect):
    """Negative reference: re-rasterize the complete QWidget source on every draw."""

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.scale = NORMAL_SCALE

    def set_scale(self, scale: float) -> None:
        self.scale = float(scale)
        self.update()

    def boundingRectFor(self, source_rect: QRectF) -> QRectF:  # noqa: N802
        center = source_rect.center()
        half_w = source_rect.width() * MAX_SCALE * 0.5
        half_h = source_rect.height() * MAX_SCALE * 0.5
        return QRectF(center.x() - half_w, center.y() - half_h, half_w * 2.0, half_h * 2.0)

    def draw(self, painter: QPainter) -> None:  # type: ignore[override]
        offset = QPoint()
        pixmap = self.sourcePixmap(
            Qt.CoordinateSystem.LogicalCoordinates,
            offset,
            QGraphicsEffect.PixmapPadMode.NoPad,
        )
        if pixmap.isNull():
            self.drawSource(painter)
            return
        center = self.sourceBoundingRect(Qt.CoordinateSystem.LogicalCoordinates).center()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.translate(center)
        painter.scale(self.scale, self.scale)
        painter.translate(-center)
        painter.drawPixmap(offset, pixmap)
        painter.restore()


class _PaintGate(QGraphicsEffect):
    def draw(self, painter: QPainter) -> None:  # type: ignore[override]
        _ = painter
        return


class _PixmapOverlay(QWidget):
    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.pixmap = QPixmap()
        self.scale = NORMAL_SCALE
        self.base = QRectF()
        self.hide()

    def attach(self, card: QFrame, pixmap: QPixmap) -> None:
        top_left = card.mapTo(self.parentWidget(), QPoint(0, 0))
        base = QRectF(float(top_left.x()), float(top_left.y()), float(card.width()), float(card.height()))
        pad_x = int(base.width() * (MAX_SCALE - 1.0) * 0.5) + 4
        pad_y = int(base.height() * (MAX_SCALE - 1.0) * 0.5) + 4
        rect = base.adjusted(-pad_x, -pad_y, pad_x, pad_y).toAlignedRect()
        self.setGeometry(rect)
        self.base = base.translated(-float(rect.x()), -float(rect.y()))
        self.pixmap = pixmap
        self.scale = NORMAL_SCALE
        self.show()
        self.raise_()
        self.update()

    def detach(self) -> None:
        self.hide()
        self.pixmap = QPixmap()
        self.base = QRectF()

    def set_scale(self, scale: float) -> None:
        self.scale = float(scale)
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        if self.pixmap.isNull() or self.base.isEmpty():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        center = self.base.center()
        width = self.base.width() * self.scale
        height = self.base.height() * self.scale
        dest = QRectF(center.x() - width * 0.5, center.y() - height * 0.5, width, height)
        painter.drawPixmap(dest, self.pixmap, QRectF(self.pixmap.rect()))
        painter.end()


class _CachedPixmapOverlay(_PixmapOverlay):
    LEVELS = 17

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.levels: list[tuple[float, QPixmap]] = []

    def attach(self, card: QFrame, pixmap: QPixmap) -> None:
        super().attach(card, pixmap)
        self.levels.clear()
        for index in range(self.LEVELS):
            scale = NORMAL_SCALE + (HOVER_SCALE - NORMAL_SCALE) * index / max(1, self.LEVELS - 1)
            width = max(1, int(round(pixmap.width() * scale)))
            height = max(1, int(round(pixmap.height() * scale)))
            scaled = pixmap.scaled(
                width,
                height,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.levels.append((scale, scaled))

    def detach(self) -> None:
        self.levels.clear()
        super().detach()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        if not self.levels or self.base.isEmpty():
            return
        _, pixmap = min(self.levels, key=lambda item: abs(item[0] - self.scale))
        painter = QPainter(self)
        center = self.base.center()
        dest = QRectF(
            center.x() - pixmap.width() * 0.5,
            center.y() - pixmap.height() * 0.5,
            float(pixmap.width()),
            float(pixmap.height()),
        )
        painter.drawPixmap(dest.topLeft(), pixmap)
        painter.end()


class _GlPixmapOverlay(QOpenGLWidget):
    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        fmt = QSurfaceFormat()
        fmt.setAlphaBufferSize(8)
        fmt.setDepthBufferSize(0)
        fmt.setStencilBufferSize(0)
        fmt.setSamples(0)
        self.setFormat(fmt)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setUpdateBehavior(QOpenGLWidget.UpdateBehavior.NoPartialUpdate)
        self.pixmap = QPixmap()
        self.scale = NORMAL_SCALE
        self.base = QRectF()
        self.hide()

    def attach(self, card: QFrame, pixmap: QPixmap) -> None:
        top_left = card.mapTo(self.parentWidget(), QPoint(0, 0))
        base = QRectF(float(top_left.x()), float(top_left.y()), float(card.width()), float(card.height()))
        pad_x = int(base.width() * (MAX_SCALE - 1.0) * 0.5) + 4
        pad_y = int(base.height() * (MAX_SCALE - 1.0) * 0.5) + 4
        rect = base.adjusted(-pad_x, -pad_y, pad_x, pad_y).toAlignedRect()
        self.setGeometry(rect)
        self.base = base.translated(-float(rect.x()), -float(rect.y()))
        self.pixmap = pixmap
        self.scale = NORMAL_SCALE
        self.show()
        self.raise_()
        self.update()

    def detach(self) -> None:
        self.hide()
        self.pixmap = QPixmap()
        self.base = QRectF()

    def set_scale(self, scale: float) -> None:
        self.scale = float(scale)
        self.update()

    def paintGL(self) -> None:  # noqa: N802
        context = self.context()
        if context is not None:
            functions = context.functions()
            functions.glClearColor(0.0, 0.0, 0.0, 0.0)
            functions.glClear(0x00004000)
        if self.pixmap.isNull() or self.base.isEmpty():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        center = self.base.center()
        width = self.base.width() * self.scale
        height = self.base.height() * self.scale
        dest = QRectF(center.x() - width * 0.5, center.y() - height * 0.5, width, height)
        painter.drawPixmap(dest, self.pixmap, QRectF(self.pixmap.rect()))
        painter.end()


class Strategy:
    name = "base"
    final_candidate = True

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


class BaselineFrozenStrategy(Strategy):
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


class LiveEffectStrategy(Strategy):
    name = "live_effect"
    final_candidate = False

    def setup(self) -> None:
        self.effect = _LiveScaleEffect(self.card)
        self.card.setGraphicsEffect(self.effect)

    def set_scale(self, scale: float) -> None:
        self.effect.set_scale(scale)


class _SnapshotStrategy(Strategy):
    overlay_factory: Callable[[QWidget], QWidget]

    def setup(self) -> None:
        host = self.window.centralWidget()
        if host is None:
            raise RuntimeError("benchmark window has no central widget")
        self.overlay = self.overlay_factory(host)
        self.gate: _PaintGate | None = None

    def begin_transition(self, _current: float, _target: float) -> None:
        pixmap = self.card.grab()
        if pixmap.isNull():
            raise RuntimeError(f"{self.name}: frame.grab() returned an empty pixmap")
        self.overlay.attach(self.card, pixmap)  # type: ignore[attr-defined]
        self.gate = _PaintGate(self.card)
        self.card.setGraphicsEffect(self.gate)

    def set_scale(self, scale: float) -> None:
        self.overlay.set_scale(scale)  # type: ignore[attr-defined]

    def end_transition(self, _scale: float) -> None:
        if self.gate is not None and self.card.graphicsEffect() is self.gate:
            self.card.setGraphicsEffect(None)
        if self.gate is not None:
            self.gate.deleteLater()
        self.gate = None
        self.overlay.detach()  # type: ignore[attr-defined]
        self.card.update()

    def teardown(self) -> None:
        self.end_transition(NORMAL_SCALE)
        self.overlay.deleteLater()  # type: ignore[attr-defined]
        super().teardown()


class SnapshotCpuStrategy(_SnapshotStrategy):
    name = "snapshot_cpu"
    overlay_factory = _PixmapOverlay


class SnapshotGlStrategy(_SnapshotStrategy):
    name = "snapshot_gl"
    overlay_factory = _GlPixmapOverlay


class CachedLevelsStrategy(_SnapshotStrategy):
    name = "cached_levels"
    overlay_factory = _CachedPixmapOverlay


class NoScaleControlStrategy(Strategy):
    name = "no_scale_control"
    final_candidate = False


STRATEGIES: dict[str, type[Strategy]] = {
    cls.name: cls
    for cls in (
        BaselineFrozenStrategy,
        LiveEffectStrategy,
        SnapshotCpuStrategy,
        SnapshotGlStrategy,
        CachedLevelsStrategy,
        NoScaleControlStrategy,
    )
}


@dataclass
class RunResult:
    strategy: str
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


class AnimationRun(QObject):
    finished = Signal(object)

    def __init__(
        self,
        strategy: Strategy,
        *,
        round_index: int,
        profile: Profile,
        target_hz: float,
        warmup_cycles: int,
        cycles: int,
    ) -> None:
        super().__init__(strategy.window)
        self.strategy = strategy
        self.round_index = round_index
        self.profile = profile
        self.target_hz = target_hz
        self.frame_budget_ms = 1000.0 / target_hz
        self.timer_interval_ms = max(4, int(1000.0 / target_hz))
        self.warmup_transitions = max(0, warmup_cycles * 2)
        self.measure_transitions = max(2, cycles * 2)
        self.ease = _ease()
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(self.timer_interval_ms)
        self.timer.timeout.connect(self._tick)

        self.current_scale = NORMAL_SCALE
        self.from_scale = NORMAL_SCALE
        self.target_scale = HOVER_SCALE
        self.transition_started = 0.0
        self.next_transition_s = 0.0
        self.in_transition = False
        self.phase = "warmup" if self.warmup_transitions else "measure"
        self.phase_transitions = 0
        self.last_tick_s: float | None = None
        self.mark_next_gap = False

        self.frame_intervals: list[float] = []
        self.tick_work: list[float] = []
        self.prepare_ms: list[float] = []
        self.start_gap_ms: list[float] = []
        self.wall_started = 0.0
        self.cpu_started = 0.0

    def start(self) -> None:
        if self.phase == "measure":
            self._begin_measurement()
        else:
            self._start_transition()
            self.timer.start()

    def _begin_measurement(self) -> None:
        self.frame_intervals.clear()
        self.tick_work.clear()
        self.prepare_ms.clear()
        self.start_gap_ms.clear()
        self.phase = "measure"
        self.phase_transitions = 0
        self.last_tick_s = None
        self.wall_started = time.perf_counter()
        self.cpu_started = time.process_time()
        self._start_transition()
        self.timer.start()

    def _start_transition(self) -> None:
        self.from_scale = self.current_scale
        self.target_scale = HOVER_SCALE if self.current_scale <= 1.001 else NORMAL_SCALE
        started = time.perf_counter()
        self.strategy.begin_transition(self.from_scale, self.target_scale)
        prepared = (time.perf_counter() - started) * 1000.0
        if self.phase == "measure":
            self.prepare_ms.append(prepared)
            self.mark_next_gap = True
        self.transition_started = time.perf_counter()
        self.in_transition = True

    def _tick(self) -> None:
        now = time.perf_counter()
        if self.phase == "measure" and self.last_tick_s is not None:
            interval = (now - self.last_tick_s) * 1000.0
            self.frame_intervals.append(interval)
            if self.mark_next_gap:
                self.start_gap_ms.append(interval)
                self.mark_next_gap = False
        self.last_tick_s = now

        if not self.in_transition:
            if now >= self.next_transition_s:
                self._start_transition()
            return

        progress = min(1.0, max(0.0, (now - self.transition_started) / (TRANSITION_MS / 1000.0)))
        eased = float(self.ease.valueForProgress(progress))
        scale = self.from_scale + (self.target_scale - self.from_scale) * eased
        work_started = time.perf_counter()
        self.strategy.set_scale(scale)
        if progress >= 1.0:
            self.current_scale = self.target_scale
            self.strategy.end_transition(self.current_scale)
            self.in_transition = False
            self.phase_transitions += 1
            self.next_transition_s = now + HOLD_MS / 1000.0
        if self.phase == "measure":
            self.tick_work.append((time.perf_counter() - work_started) * 1000.0)

        if progress < 1.0:
            return

        if self.phase == "warmup" and self.phase_transitions >= self.warmup_transitions:
            self.timer.stop()
            QTimer.singleShot(120, self._begin_measurement)
            return

        if self.phase == "measure" and self.phase_transitions >= self.measure_transitions:
            self.timer.stop()
            wall_seconds = max(1e-9, time.perf_counter() - self.wall_started)
            cpu_seconds = max(0.0, time.process_time() - self.cpu_started)
            QTimer.singleShot(80, lambda: self._finish(wall_seconds, cpu_seconds))

    def _finish(self, wall_seconds: float, cpu_seconds: float) -> None:
        budget = self.frame_budget_ms
        long_1_5 = sum(1 for value in self.frame_intervals if value > budget * 1.5)
        long_2 = sum(1 for value in self.frame_intervals if value > budget * 2.0)
        count = max(1, len(self.frame_intervals))
        result = RunResult(
            strategy=self.strategy.name,
            round_index=self.round_index,
            profile=self.profile.name,
            target_hz=self.target_hz,
            timer_interval_ms=self.timer_interval_ms,
            frame_budget_ms=budget,
            setup_ms=float(getattr(self.strategy, "_setup_ms", 0.0)),
            wall_seconds=wall_seconds,
            cpu_seconds=cpu_seconds,
            cpu_core_percent=cpu_seconds / wall_seconds * 100.0,
            samples=len(self.frame_intervals),
            frame_median_ms=statistics.median(self.frame_intervals) if self.frame_intervals else 0.0,
            frame_p95_ms=_percentile(self.frame_intervals, 0.95),
            frame_p99_ms=_percentile(self.frame_intervals, 0.99),
            frame_max_ms=max(self.frame_intervals, default=0.0),
            long_1_5x_count=long_1_5,
            long_1_5x_rate=long_1_5 / count,
            long_2x_count=long_2,
            long_2x_rate=long_2 / count,
            tick_work_median_ms=statistics.median(self.tick_work) if self.tick_work else 0.0,
            tick_work_p95_ms=_percentile(self.tick_work, 0.95),
            transition_prepare_median_ms=statistics.median(self.prepare_ms) if self.prepare_ms else 0.0,
            transition_prepare_p95_ms=_percentile(self.prepare_ms, 0.95),
            transition_start_gap_median_ms=statistics.median(self.start_gap_ms) if self.start_gap_ms else 0.0,
            transition_start_gap_p95_ms=_percentile(self.start_gap_ms, 0.95),
        )
        self.finished.emit(result)


class DemoAnimator(QObject):
    def __init__(self, strategy: Strategy, target_hz: float) -> None:
        super().__init__(strategy.window)
        self.strategy = strategy
        self.ease = _ease()
        self.current = NORMAL_SCALE
        self.from_scale = NORMAL_SCALE
        self.target = HOVER_SCALE
        self.started = time.perf_counter()
        self.in_transition = False
        self.next_start = 0.0
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(max(4, int(1000.0 / target_hz)))
        self.timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._begin()
        self.timer.start()

    def _begin(self) -> None:
        self.from_scale = self.current
        self.target = HOVER_SCALE if self.current <= 1.001 else NORMAL_SCALE
        self.strategy.begin_transition(self.from_scale, self.target)
        self.started = time.perf_counter()
        self.in_transition = True

    def _tick(self) -> None:
        now = time.perf_counter()
        if not self.in_transition:
            if now >= self.next_start:
                self._begin()
            return
        progress = min(1.0, (now - self.started) / (TRANSITION_MS / 1000.0))
        eased = float(self.ease.valueForProgress(progress))
        scale = self.from_scale + (self.target - self.from_scale) * eased
        self.strategy.set_scale(scale)
        if progress >= 1.0:
            self.current = self.target
            self.strategy.end_transition(self.current)
            self.in_transition = False
            self.next_start = now + 0.35


def _target_hz(app: QApplication) -> float:
    refresh = 60.0
    screen = app.primaryScreen()
    if screen is not None:
        try:
            candidate = float(screen.refreshRate())
            if 30.0 <= candidate <= 500.0:
                refresh = candidate
        except (RuntimeError, TypeError, ValueError):
            pass
    return max(60.0, min(90.0, refresh))


def _system_info(app: QApplication) -> dict[str, object]:
    screen = app.primaryScreen()
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "pyside": PYSIDE_VERSION,
        "qt": qVersion(),
        "qsg_render_loop": os.environ.get("QSG_RENDER_LOOP", ""),
        "screen_name": screen.name() if screen is not None else "",
        "screen_refresh_hz": float(screen.refreshRate()) if screen is not None else 0.0,
        "device_pixel_ratio": float(screen.devicePixelRatio()) if screen is not None else 1.0,
        "screen_size": [screen.size().width(), screen.size().height()] if screen is not None else [0, 0],
    }


def _run_one(
    app: QApplication,
    strategy_name: str,
    profile: Profile,
    round_index: int,
    warmup_cycles: int,
    cycles: int,
) -> RunResult:
    window, card = _build_card(profile)
    window.show()
    _wait_ms(180)
    strategy = STRATEGIES[strategy_name](window, card)
    setup_started = time.perf_counter()
    strategy.setup()
    strategy._setup_ms = (time.perf_counter() - setup_started) * 1000.0  # type: ignore[attr-defined]
    _wait_ms(80)

    loop = QEventLoop()
    runner = AnimationRun(
        strategy,
        round_index=round_index,
        profile=profile,
        target_hz=_target_hz(app),
        warmup_cycles=warmup_cycles,
        cycles=cycles,
    )
    holder: list[RunResult] = []

    def done(result: RunResult) -> None:
        holder.append(result)
        loop.quit()

    runner.finished.connect(done)
    runner.start()
    loop.exec()
    strategy.teardown()
    window.close()
    window.deleteLater()
    app.processEvents()
    if not holder:
        raise RuntimeError(f"benchmark produced no result for {strategy_name}")
    return holder[0]


def _write_outputs(output_dir: Path, payload: dict[str, object]) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = output_dir / f"gui-card-perf-{stamp}.json"
    csv_path = output_dir / f"gui-card-perf-{stamp}.csv"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    rows = payload["runs"]
    assert isinstance(rows, list)
    fieldnames = list(rows[0].keys()) if rows else []
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return json_path, csv_path


def _parse_strategy_arg(value: str) -> list[str]:
    if value.strip().lower() == "all":
        return list(STRATEGIES)
    names = [part.strip() for part in value.split(",") if part.strip()]
    unknown = [name for name in names if name not in STRATEGIES]
    if unknown:
        raise SystemExit(f"unknown strategies: {', '.join(unknown)}")
    return names


def _print_result(result: RunResult) -> None:
    print(
        f"[{result.round_index:02d}] {result.strategy:<18} "
        f"p95={result.frame_p95_ms:6.2f}ms p99={result.frame_p99_ms:6.2f}ms "
        f"long={result.long_1_5x_rate * 100:5.1f}% cpu={result.cpu_core_percent:5.1f}% "
        f"start-gap-p95={result.transition_start_gap_p95_ms:6.2f}ms"
    )


def _demo(app: QApplication, strategy_name: str, profile: Profile) -> int:
    window, card = _build_card(profile)
    strategy = STRATEGIES[strategy_name](window, card)
    strategy.setup()
    window.show()
    animator = DemoAnimator(strategy, _target_hz(app))
    animator.start()
    window.setWindowTitle(
        f"DEMO · {strategy_name} · {profile.name} · interact with inputs/buttons while animation loops"
    )
    rc = app.exec()
    animator.timer.stop()
    strategy.teardown()
    return rc


def main() -> int:
    parser = argparse.ArgumentParser(description="Isolated QWidget card animation architecture benchmark")
    parser.add_argument("--strategies", default="all", help="all or comma-separated strategy names")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="large")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--warmup-cycles", type=int, default=2)
    parser.add_argument("--cycles", type=int, default=10)
    parser.add_argument("--output-dir", default="perf_results")
    parser.add_argument("--demo", choices=sorted(STRATEGIES), help="loop one strategy for visual/input parity checking")
    args = parser.parse_args()

    app = QApplication.instance() or QApplication(sys.argv)
    profile = PROFILES[args.profile]
    if args.demo:
        return _demo(app, args.demo, profile)

    names = _parse_strategy_arg(args.strategies)
    results: list[RunResult] = []
    for round_index in range(1, max(1, args.rounds) + 1):
        offset = (round_index - 1) % max(1, len(names))
        ordered = names[offset:] + names[:offset]
        for name in ordered:
            result = _run_one(
                app,
                name,
                profile,
                round_index,
                max(0, args.warmup_cycles),
                max(1, args.cycles),
            )
            results.append(result)
            _print_result(result)

    payload = {
        "schema_version": 1,
        "system": _system_info(app),
        "config": {
            "profile": profile.name,
            "profile_geometry": asdict(profile),
            "rounds": max(1, args.rounds),
            "warmup_cycles": max(0, args.warmup_cycles),
            "cycles": max(1, args.cycles),
            "transition_ms": TRANSITION_MS,
            "hold_ms": HOLD_MS,
            "normal_scale": NORMAL_SCALE,
            "hover_scale": HOVER_SCALE,
            "strategies": names,
        },
        "runs": [asdict(result) for result in results],
    }
    json_path, csv_path = _write_outputs(Path(args.output_dir), payload)
    print(f"\nJSON: {json_path}")
    print(f"CSV : {csv_path}")
    print("Next: run tools/analyze_gui_card_perf.py on the JSON, then parity-check the top candidates with --demo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
