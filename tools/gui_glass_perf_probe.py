from __future__ import annotations

import argparse
import csv
import json
import math
import os
import platform
import statistics
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6 import __version__ as PYSIDE_VERSION
from PySide6.QtCore import QEventLoop, QObject, Qt, QTimer, QUrl, Signal, qVersion
from PySide6.QtGui import QImage
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtWidgets import QApplication

from gui.native_background import _blur_wallpaper, _decode_wallpaper


MODES = ("production_glass", "glass_overlay_only", "static_background")
PATH = (0, 1, 2, 3, 4, 5, 4, 3, 2, 1)
CROSSOVER_DWELL_MS = 70
TRANSITION_MS = 300

_QML = r'''import QtQuick
import QtQuick.Window
import QtQuick.Effects

Window {
    id: root
    visible: false
    color: "#17263a"

    property url sharpUrl
    property url blurUrl
    property bool glassEnabled: true
    property bool parallaxEnabled: true
    property real pointerX: 0.0
    property real pointerY: 0.0
    property real offsetX: 0.0
    property real offsetY: 0.0
    property bool animationRunning: false
    property int activeIndex: 0
    property int previousIndex: 0
    property real motionProgress: 1.0
    property int geometryRevision: 0

    readonly property real maxX: width * 0.027
    readonly property real maxY: height * 0.027
    readonly property real targetX: -pointerX * maxX
    readonly property real targetY: -pointerY * maxY
    readonly property real imageX: (width - width * 1.06) * 0.5 + offsetX
    readonly property real imageY: (height - height * 1.06) * 0.5 + offsetY
    readonly property real cardW: Math.min(520, width * 0.40)
    readonly property real cardH: Math.min(240, height * 0.24)
    readonly property real eased: motionProgress * motionProgress * (3.0 - 2.0 * motionProgress)

    function cardX(i) { return width * 0.08 + (i % 2) * (cardW + width * 0.04) }
    function cardY(i) { return height * 0.08 + Math.floor(i / 2) * (cardH + height * 0.035) }

    Image {
        width: root.width * 1.06
        height: root.height * 1.06
        x: root.imageX
        y: root.imageY
        source: root.sharpUrl
        fillMode: Image.PreserveAspectCrop
        smooth: true
        cache: true
    }

    Item {
        id: blurSource
        anchors.fill: parent
        clip: true
        visible: false
        layer.enabled: root.glassEnabled
        layer.smooth: true

        Image {
            width: root.width * 1.06
            height: root.height * 1.06
            x: root.imageX
            y: root.imageY
            source: root.blurUrl
            fillMode: Image.PreserveAspectCrop
            smooth: true
            cache: true
        }
    }

    Item {
        id: glassMaskScene
        anchors.fill: parent

        Repeater {
            model: 6
            delegate: Rectangle {
                x: root.cardX(index)
                y: root.cardY(index)
                width: root.cardW
                height: root.cardH
                radius: 6
                antialiasing: true
                color: "white"
            }
        }
    }

    ShaderEffectSource {
        id: glassMaskTexture
        anchors.fill: parent
        sourceItem: glassMaskScene
        hideSource: true
        live: false
        smooth: true
        visible: false
    }

    onGeometryRevisionChanged: glassMaskTexture.scheduleUpdate()

    MultiEffect {
        anchors.fill: parent
        visible: root.glassEnabled
        source: blurSource
        maskEnabled: true
        maskSource: glassMaskTexture
        autoPaddingEnabled: false
    }

    Repeater {
        model: 6
        delegate: Rectangle {
            x: root.cardX(index)
            y: root.cardY(index)
            width: root.cardW
            height: root.cardH
            radius: 6
            antialiasing: true
            transformOrigin: Item.Center
            scale: index === root.activeIndex
                ? 1.0 + 0.02 * root.eased
                : (index === root.previousIndex ? 1.02 - 0.02 * root.eased : 1.0)
            color: Qt.rgba(
                0,
                0,
                0,
                (index === root.activeIndex || index === root.previousIndex ? 102.0 : 64.0) / 255.0
            )
        }
    }

    FrameAnimation {
        running: root.parallaxEnabled && root.animationRunning
        onTriggered: {
            var dt = Math.max(0.0, Math.min(frameTime, 0.05))
            var gain = 1.0 - Math.pow(0.88, dt * 60.0)
            root.offsetX += (root.targetX - root.offsetX) * gain
            root.offsetY += (root.targetY - root.offsetY) * gain
            if (Math.abs(root.targetX - root.offsetX) < 0.02 &&
                    Math.abs(root.targetY - root.offsetY) < 0.02) {
                root.offsetX = root.targetX
                root.offsetY = root.targetY
                root.animationRunning = false
            }
        }
    }
}
'''


@dataclass
class ProbeResult:
    mode: str
    round_index: int
    target_hz: float
    update_interval_ms: int
    samples_tick: int
    tick_p95_ms: float
    tick_p99_ms: float
    samples_swap: int
    swap_p95_ms: float
    swap_p99_ms: float
    swap_max_ms: float
    swap_long_1_5x_rate: float
    cpu_core_percent: float
    crossover_events: int


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


def _wait_ms(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(max(0, int(ms)), loop.quit)
    loop.exec()


def _display_hz(app: QApplication) -> float:
    screen = app.primaryScreen()
    if screen is None:
        return 60.0
    try:
        value = float(screen.refreshRate())
        return value if 30.0 <= value <= 500.0 else 60.0
    except (RuntimeError, TypeError, ValueError):
        return 60.0


def _prepare_assets(temp_dir: Path) -> tuple[Path, Path]:
    data = _decode_wallpaper()
    sharp = temp_dir / "wallpaper.jpg"
    blur = temp_dir / "wallpaper_blurred.jpg"
    sharp.write_bytes(data)
    image = QImage.fromData(data)
    if image.isNull():
        raise RuntimeError("Qt could not decode bundled wallpaper")
    blurred = _blur_wallpaper(image)
    if blurred.isNull() or not blurred.save(str(blur), "JPG", 92):
        raise RuntimeError("Could not create pre-blurred wallpaper")
    return sharp, blur


class GlassRun(QObject):
    finished = Signal(object)
    POINTERS = (
        (-0.62, -0.56),
        (0.62, -0.56),
        (-0.62, 0.00),
        (0.62, 0.00),
        (-0.62, 0.56),
        (0.62, 0.56),
    )

    def __init__(
        self,
        root: QQuickWindow,
        *,
        mode: str,
        round_index: int,
        target_hz: float,
        warmup_cycles: int,
        cycles: int,
    ) -> None:
        super().__init__(root)
        self.root = root
        self.mode = mode
        self.round_index = round_index
        self.target_hz = target_hz
        self.interval_ms = max(4, int(1000.0 / target_hz))
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(self.interval_ms)
        self.timer.timeout.connect(self._tick)
        self.root.frameSwapped.connect(self._frame_swapped)

        self.warmup_left = max(0, warmup_cycles * len(PATH))
        self.measure_total = max(len(PATH), cycles * len(PATH))
        self.phase = "warmup" if self.warmup_left else "measure"
        self.measured = 0
        self.path_pos = 0
        self.active = PATH[0]
        self.previous = PATH[0]
        self.transition_started = time.perf_counter()
        self.next_cross = time.perf_counter()
        self.last_tick: float | None = None
        self.last_swap: float | None = None
        self.tick_intervals: list[float] = []
        self.swap_intervals: list[float] = []
        self.wall_started = 0.0
        self.cpu_started = 0.0

    def start(self) -> None:
        self.next_cross = time.perf_counter()
        if self.phase == "measure":
            self._begin_measurement()
        self.timer.start()

    def _begin_measurement(self) -> None:
        self.phase = "measure"
        self.measured = 0
        self.last_tick = None
        self.last_swap = None
        self.tick_intervals.clear()
        self.swap_intervals.clear()
        self.wall_started = time.perf_counter()
        self.cpu_started = time.process_time()

    def _frame_swapped(self) -> None:
        if self.phase != "measure":
            return
        now = time.perf_counter()
        if self.last_swap is not None:
            self.swap_intervals.append((now - self.last_swap) * 1000.0)
        self.last_swap = now

    def _cross(self, now: float) -> None:
        self.previous = self.active
        self.path_pos = (self.path_pos + 1) % len(PATH)
        self.active = PATH[self.path_pos]
        self.transition_started = now
        self.next_cross = now + CROSSOVER_DWELL_MS / 1000.0
        if self.phase == "warmup":
            self.warmup_left -= 1
            if self.warmup_left <= 0:
                self._begin_measurement()
        else:
            self.measured += 1

    def _publish(self, now: float) -> None:
        progress = min(1.0, max(0.0, (now - self.transition_started) / (TRANSITION_MS / 1000.0)))
        px, py = self.POINTERS[self.active]
        wobble = 0.035 * math.sin(now * 8.0)
        self.root.setProperty("activeIndex", self.active)
        self.root.setProperty("previousIndex", self.previous)
        self.root.setProperty("motionProgress", progress)
        self.root.setProperty("pointerX", max(-1.0, min(1.0, px + wobble)))
        self.root.setProperty("pointerY", max(-1.0, min(1.0, py - wobble)))
        if self.mode != "static_background":
            self.root.setProperty("animationRunning", True)

    def _tick(self) -> None:
        now = time.perf_counter()
        if self.phase == "measure" and self.last_tick is not None:
            self.tick_intervals.append((now - self.last_tick) * 1000.0)
        self.last_tick = now

        if now >= self.next_cross:
            self._cross(now)
        self._publish(now)

        if self.phase == "measure" and self.measured >= self.measure_total:
            self.timer.stop()
            QTimer.singleShot(120, self._finish)

    def _finish(self) -> None:
        wall = max(1e-9, time.perf_counter() - self.wall_started)
        cpu = max(0.0, time.process_time() - self.cpu_started)
        budget = 1000.0 / self.target_hz
        long_count = sum(value > budget * 1.5 for value in self.swap_intervals)
        result = ProbeResult(
            mode=self.mode,
            round_index=self.round_index,
            target_hz=self.target_hz,
            update_interval_ms=self.interval_ms,
            samples_tick=len(self.tick_intervals),
            tick_p95_ms=_percentile(self.tick_intervals, 0.95),
            tick_p99_ms=_percentile(self.tick_intervals, 0.99),
            samples_swap=len(self.swap_intervals),
            swap_p95_ms=_percentile(self.swap_intervals, 0.95),
            swap_p99_ms=_percentile(self.swap_intervals, 0.99),
            swap_max_ms=max(self.swap_intervals, default=0.0),
            swap_long_1_5x_rate=long_count / max(1, len(self.swap_intervals)),
            cpu_core_percent=cpu / wall * 100.0,
            crossover_events=self.measured,
        )
        self.finished.emit(result)


def _mode_flags(mode: str) -> tuple[bool, bool]:
    if mode == "production_glass":
        return True, True
    if mode == "glass_overlay_only":
        return False, True
    if mode == "static_background":
        return True, False
    raise ValueError(mode)


def _run_one(
    app: QApplication,
    *,
    mode: str,
    round_index: int,
    target_hz: float,
    warmup_cycles: int,
    cycles: int,
    qml_path: Path,
    sharp_path: Path,
    blur_path: Path,
) -> ProbeResult:
    engine = QQmlApplicationEngine()
    engine.load(QUrl.fromLocalFile(str(qml_path)))
    roots = engine.rootObjects()
    if not roots or not isinstance(roots[0], QQuickWindow):
        raise RuntimeError("Glass probe QQuickWindow failed to load")
    root = roots[0]
    screen = app.primaryScreen()
    width = min(1600, max(1180, screen.availableGeometry().width() if screen else 1440))
    height = min(1000, max(760, screen.availableGeometry().height() if screen else 900))
    root.resize(width, height)
    root.setTitle(f"Glass Performance Probe · {mode}")
    root.setProperty("sharpUrl", QUrl.fromLocalFile(str(sharp_path)))
    root.setProperty("blurUrl", QUrl.fromLocalFile(str(blur_path)))
    glass_enabled, parallax_enabled = _mode_flags(mode)
    root.setProperty("glassEnabled", glass_enabled)
    root.setProperty("parallaxEnabled", parallax_enabled)
    root.setPersistentGraphics(True)
    root.setPersistentSceneGraph(True)
    root.show()
    _wait_ms(350)
    root.setProperty("geometryRevision", 1)
    _wait_ms(100)

    loop = QEventLoop()
    holder: list[ProbeResult] = []
    runner = GlassRun(
        root,
        mode=mode,
        round_index=round_index,
        target_hz=target_hz,
        warmup_cycles=warmup_cycles,
        cycles=cycles,
    )
    runner.finished.connect(lambda result: (holder.append(result), loop.quit()))
    runner.start()
    loop.exec()

    root.hide()
    root.releaseResources()
    root.close()
    root.deleteLater()
    engine.clearComponentCache()
    engine.deleteLater()
    app.processEvents()
    if not holder:
        raise RuntimeError(f"glass probe produced no result for {mode}")
    return holder[0]


def _median(rows: list[ProbeResult], field_name: str) -> float:
    values = [float(getattr(row, field_name)) for row in rows]
    return statistics.median(values) if values else 0.0


def _summary(results: list[ProbeResult]) -> dict[str, dict[str, float]]:
    output: dict[str, dict[str, float]] = {}
    for mode in MODES:
        rows = [row for row in results if row.mode == mode]
        if not rows:
            continue
        output[mode] = {
            "runs": float(len(rows)),
            "swap_p95_ms": _median(rows, "swap_p95_ms"),
            "swap_p99_ms": _median(rows, "swap_p99_ms"),
            "swap_long_1_5x_rate": _median(rows, "swap_long_1_5x_rate"),
            "tick_p95_ms": _median(rows, "tick_p95_ms"),
            "tick_p99_ms": _median(rows, "tick_p99_ms"),
            "cpu_core_percent": _median(rows, "cpu_core_percent"),
            "samples_swap": _median(rows, "samples_swap"),
        }
    return output


def _gain(base: float, candidate: float) -> float:
    return (base - candidate) / max(1e-6, base) * 100.0


def _verdict(summary: dict[str, dict[str, float]]) -> dict[str, object]:
    base = summary.get("production_glass")
    overlay = summary.get("glass_overlay_only")
    static = summary.get("static_background")
    if not base or not overlay or not static:
        return {"classification": "insufficient-data"}

    base_p99 = max(1e-6, base["swap_p99_ms"])
    overlay_gain = _gain(base_p99, overlay["swap_p99_ms"])
    static_gain = _gain(base_p99, static["swap_p99_ms"])
    base_p95 = max(1e-6, base["swap_p95_ms"])
    overlay_p95_gain = _gain(base_p95, overlay["swap_p95_ms"])
    static_p95_gain = _gain(base_p95, static["swap_p95_ms"])

    overlay_signal = (overlay_gain + overlay_p95_gain) * 0.5
    static_signal = (static_gain + static_p95_gain) * 0.5

    if overlay_signal < 10.0 and static_signal < 10.0:
        classification = "glass-stack-not-dominant"
    elif overlay_signal >= 15.0 and static_signal < 10.0:
        classification = "multieffect-dominant"
    elif static_signal >= 15.0 and overlay_signal < 10.0:
        classification = "parallax-source-motion-dominant"
    elif overlay_signal >= 10.0 and static_signal >= 10.0:
        if overlay_signal > static_signal * 1.35:
            classification = "multieffect-dominant"
        elif static_signal > overlay_signal * 1.35:
            classification = "parallax-source-motion-dominant"
        else:
            classification = "combined-glass-parallax-cost"
    else:
        classification = "mixed-quick-compositor-cost"

    return {
        "classification": classification,
        "overlay_only_p99_gain_percent": overlay_gain,
        "static_background_p99_gain_percent": static_gain,
        "overlay_only_p95_gain_percent": overlay_p95_gain,
        "static_background_p95_gain_percent": static_p95_gain,
    }


def _system_info(app: QApplication) -> dict[str, object]:
    screen = app.primaryScreen()
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "pyside": PYSIDE_VERSION,
        "qt": qVersion(),
        "qsg_render_loop": os.environ.get("QSG_RENDER_LOOP", ""),
        "screen_name": screen.name() if screen else "",
        "screen_refresh_hz": float(screen.refreshRate()) if screen else 0.0,
        "device_pixel_ratio": float(screen.devicePixelRatio()) if screen else 1.0,
    }


def _write_outputs(output_dir: Path, payload: dict[str, object]) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = output_dir / f"gui-glass-perf-probe-{stamp}.json"
    csv_path = output_dir / f"gui-glass-perf-probe-{stamp}.csv"
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
    parser = argparse.ArgumentParser(description="Focused Qt Quick glass/parallax performance probe")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--warmup-cycles", type=int, default=2)
    parser.add_argument("--cycles", type=int, default=8)
    parser.add_argument("--modes", default="all", help="all or comma-separated mode names")
    parser.add_argument("--output-dir", default="perf_results")
    args = parser.parse_args()

    app = QApplication.instance() or QApplication(sys.argv)
    target_hz = max(60.0, min(90.0, _display_hz(app)))
    modes = list(MODES) if args.modes.lower() == "all" else [part.strip() for part in args.modes.split(",") if part.strip()]
    unknown = [mode for mode in modes if mode not in MODES]
    if unknown:
        raise SystemExit(f"unknown modes: {', '.join(unknown)}")

    results: list[ProbeResult] = []
    with tempfile.TemporaryDirectory(prefix="ecommerce-agent-glass-probe-") as temp_name:
        temp_dir = Path(temp_name)
        sharp_path, blur_path = _prepare_assets(temp_dir)
        qml_path = temp_dir / "glass_probe.qml"
        qml_path.write_text(_QML, encoding="utf-8")

        for round_index in range(1, max(1, args.rounds) + 1):
            offset = (round_index - 1) % len(modes)
            ordered = modes[offset:] + modes[:offset]
            for mode in ordered:
                result = _run_one(
                    app,
                    mode=mode,
                    round_index=round_index,
                    target_hz=target_hz,
                    warmup_cycles=max(0, args.warmup_cycles),
                    cycles=max(1, args.cycles),
                    qml_path=qml_path,
                    sharp_path=sharp_path,
                    blur_path=blur_path,
                )
                results.append(result)
                print(
                    f"[{round_index:02d}] {mode:<20} "
                    f"swap-p95={result.swap_p95_ms:6.2f} "
                    f"swap-p99={result.swap_p99_ms:6.2f} "
                    f"long={result.swap_long_1_5x_rate*100:5.1f}% "
                    f"cpu={result.cpu_core_percent:5.1f}% "
                    f"swaps={result.samples_swap}"
                )

    summary = _summary(results)
    verdict = _verdict(summary)
    payload = {
        "schema_version": 1,
        "system": _system_info(app),
        "config": {
            "rounds": max(1, args.rounds),
            "warmup_cycles": max(0, args.warmup_cycles),
            "cycles": max(1, args.cycles),
            "target_hz": target_hz,
            "crossover_dwell_ms": CROSSOVER_DWELL_MS,
            "transition_ms": TRANSITION_MS,
            "modes": modes,
        },
        "summary": summary,
        "verdict": verdict,
        "runs": [asdict(row) for row in results],
    }
    json_path, csv_path = _write_outputs(Path(args.output_dir), payload)

    print("\nGLASS PERFORMANCE PROBE SUMMARY")
    print("mode                  swap-p95  swap-p99  long%   CPU%")
    print("-" * 62)
    for mode in MODES:
        row = summary.get(mode)
        if row is None:
            continue
        print(
            f"{mode:<21} {row['swap_p95_ms']:8.2f} {row['swap_p99_ms']:9.2f} "
            f"{row['swap_long_1_5x_rate']*100:6.1f} {row['cpu_core_percent']:6.1f}"
        )
    print(f"\nVERDICT: {verdict.get('classification')}")
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    print(f"\nJSON: {json_path}\nCSV : {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
