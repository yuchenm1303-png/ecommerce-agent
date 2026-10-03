from __future__ import annotations

import argparse
import base64
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WALLPAPER_ASSET = ROOT / "gui" / "assets" / "fuji_sakura_wallpaper.jpg.b64"
DEFAULT_WIDTH = 1600
DEFAULT_HEIGHT = 900
OVERSCAN = 1.06
TRAVEL = 0.90
WARMUP_S = 0.75


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def summarize_intervals(times: list[float]) -> dict[str, float | int]:
    if len(times) < 2:
        return {
            "frames": len(times),
            "fps": 0.0,
            "interval_p50_ms": 0.0,
            "interval_p95_ms": 0.0,
            "interval_p99_ms": 0.0,
            "interval_max_ms": 0.0,
            "jitter_std_ms": 0.0,
        }
    intervals = [(b - a) * 1000.0 for a, b in zip(times, times[1:])]
    span = times[-1] - times[0]
    return {
        "frames": len(times),
        "fps": (len(times) - 1) / span if span > 0 else 0.0,
        "interval_p50_ms": percentile(intervals, 0.50),
        "interval_p95_ms": percentile(intervals, 0.95),
        "interval_p99_ms": percentile(intervals, 0.99),
        "interval_max_ms": max(intervals),
        "jitter_std_ms": statistics.pstdev(intervals) if len(intervals) > 1 else 0.0,
    }


class ClockRecorder:
    def __init__(self, started_at: float) -> None:
        self.started_at = started_at
        self.times: list[float] = []

    def mark(self) -> None:
        now = time.perf_counter()
        if now - self.started_at >= WARMUP_S:
            self.times.append(now)


def decode_wallpaper() -> bytes:
    encoded = WALLPAPER_ASSET.read_text(encoding="ascii")
    return base64.b64decode("".join(encoded.split()), validate=True)


def source_rect_for_phase(image_size: tuple[int, int], viewport_size: tuple[int, int], phase: float):
    from PySide6.QtCore import QRectF

    iw, ih = image_size
    vw, vh = viewport_size
    aspect = vw / max(1.0, float(vh))
    image_aspect = iw / max(1.0, float(ih))
    if image_aspect >= aspect:
        cover_h = float(ih)
        cover_w = cover_h * aspect
    else:
        cover_w = float(iw)
        cover_h = cover_w / aspect

    view_w = cover_w / OVERSCAN
    view_h = cover_h / OVERSCAN
    margin_x = (cover_w - view_w) * 0.5
    margin_y = (cover_h - view_h) * 0.5
    center_x = iw * 0.5 + math.sin(phase * 1.37) * margin_x * TRAVEL
    center_y = ih * 0.5 + math.sin(phase * 0.91 + 0.7) * margin_y * TRAVEL
    return QRectF(center_x - view_w * 0.5, center_y - view_h * 0.5, view_w, view_h)


def common_result(mode: str, metric_kind: str, recorder: ClockRecorder, app, window) -> dict[str, Any]:
    screen = window.screen() or app.primaryScreen()
    result: dict[str, Any] = {
        "mode": mode,
        "metric_kind": metric_kind,
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "qt": None,
        "screen_refresh_hz": float(screen.refreshRate()) if screen else 0.0,
        "device_pixel_ratio": float(window.devicePixelRatio()),
        "logical_size": [int(window.width()), int(window.height())],
    }
    from PySide6.QtCore import qVersion

    result["qt"] = qVersion()
    result.update(summarize_intervals(recorder.times))
    refresh = float(result["screen_refresh_hz"] or 0.0)
    budget = 1000.0 / refresh if refresh > 1 else 0.0
    result["screen_budget_ms"] = budget
    result["p95_over_budget"] = bool(budget and float(result["interval_p95_ms"]) > budget * 1.20)
    return result


def run_widget(mode: str, seconds: float, output: Path, width: int, height: int) -> int:
    from PySide6.QtCore import QRectF, Qt, QTimer
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication, QWidget

    image = QImage.fromData(decode_wallpaper()).convertToFormat(QImage.Format.Format_RGBA8888)
    if image.isNull():
        raise RuntimeError("wallpaper decode failed")

    app = QApplication(sys.argv[:1])
    started = time.perf_counter()
    recorder = ClockRecorder(started)

    class RasterWindow(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle(f"Parallax benchmark · {mode}")
            self.resize(width, height)
            self._phase = 0.0
            self._timer = QTimer(self)
            self._timer.setTimerType(Qt.TimerType.PreciseTimer)
            refresh = max(60.0, float(self.screen().refreshRate()))
            if mode == "widget60":
                interval = 16
            else:
                interval = max(1, round(1000.0 / refresh))
            self._timer.setInterval(interval)
            self._timer.timeout.connect(self._tick)
            self._timer.start()

        def _tick(self) -> None:
            self._phase = time.perf_counter() - started
            self.update()

        def paintEvent(self, event) -> None:  # type: ignore[override]
            del event
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            source = source_rect_for_phase(
                (image.width(), image.height()),
                (max(1, self.width()), max(1, self.height())),
                self._phase,
            )
            painter.drawImage(QRectF(self.rect()), image, source)
            painter.end()
            recorder.mark()

    window = RasterWindow()
    window.show()
    QTimer.singleShot(int(seconds * 1000), app.quit)
    app.exec()
    result = common_result(mode, "paint_cadence", recorder, app, window)
    result["timer_interval_ms"] = int(window._timer.interval())
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def run_gl(seconds: float, output: Path, width: int, height: int) -> int:
    from PySide6.QtCore import QRect, QRectF, QTimer
    from PySide6.QtGui import QGuiApplication, QImage, QSurfaceFormat
    from PySide6.QtOpenGL import QOpenGLTexture, QOpenGLTextureBlitter, QOpenGLWindow

    image = QImage.fromData(decode_wallpaper()).convertToFormat(QImage.Format.Format_RGBA8888)
    if image.isNull():
        raise RuntimeError("wallpaper decode failed")

    app = QGuiApplication(sys.argv[:1])
    started = time.perf_counter()
    recorder = ClockRecorder(started)

    class NativeGLWindow(QOpenGLWindow):
        def __init__(self) -> None:
            super().__init__(QOpenGLWindow.UpdateBehavior.NoPartialUpdate)
            fmt = QSurfaceFormat()
            fmt.setSwapInterval(1)
            self.setFormat(fmt)
            self.setTitle("Parallax benchmark · gl · native QOpenGLWindow")
            self.resize(width, height)
            self._texture = None
            self._blitter = None
            self._phase = 0.0
            self.frameSwapped.connect(self._frame_swapped)

        def initializeGL(self) -> None:
            self._texture = QOpenGLTexture(image, QOpenGLTexture.MipMapGeneration.DontGenerateMipMaps)
            self._texture.setMinificationFilter(QOpenGLTexture.Filter.Linear)
            self._texture.setMagnificationFilter(QOpenGLTexture.Filter.Linear)
            self._texture.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)
            self._blitter = QOpenGLTextureBlitter()
            if not self._blitter.create():
                raise RuntimeError("QOpenGLTextureBlitter.create() failed")

        def paintGL(self) -> None:
            if self._texture is None or self._blitter is None:
                return
            self._phase = time.perf_counter() - started
            dpr = float(self.devicePixelRatio())
            pw = max(1, round(self.width() * dpr))
            ph = max(1, round(self.height() * dpr))
            functions = self.context().functions()
            functions.glViewport(0, 0, pw, ph)
            source = source_rect_for_phase(
                (image.width(), image.height()),
                (max(1, self.width()), max(1, self.height())),
                self._phase,
            )
            target_transform = QOpenGLTextureBlitter.targetTransform(
                QRectF(0.0, 0.0, float(pw), float(ph)),
                QRect(0, 0, pw, ph),
            )
            source_transform = QOpenGLTextureBlitter.sourceTransform(
                source,
                image.size(),
                QOpenGLTextureBlitter.Origin.OriginTopLeft,
            )
            self._blitter.bind()
            self._blitter.blit(self._texture.textureId(), target_transform, source_transform)
            self._blitter.release()

        def _frame_swapped(self) -> None:
            recorder.mark()
            self.update()

    window = NativeGLWindow()
    window.show()
    window.update()
    QTimer.singleShot(int(seconds * 1000), app.quit)
    app.exec()
    result = common_result("gl", "present_cadence", recorder, app, window)
    fmt = window.format()
    result["swap_interval"] = int(fmt.swapInterval())
    result["opengl_renderable_type"] = str(fmt.renderableType())
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def run_quick(seconds: float, output: Path, width: int, height: int) -> int:
    os.environ.setdefault("QSG_RENDER_LOOP", "threaded")
    from PySide6.QtCore import QObject, QTimer, QUrl, Qt, Slot
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine

    app = QGuiApplication(sys.argv[:1])
    started = time.perf_counter()
    recorder = ClockRecorder(started)

    class FrameSink(QObject):
        @Slot()
        def on_frame(self) -> None:
            recorder.mark()

    sink = FrameSink()
    wallpaper_path = output.parent / "wallpaper.jpg"
    if not wallpaper_path.exists():
        wallpaper_path.write_bytes(decode_wallpaper())

    qml = f'''import QtQuick\nimport QtQuick.Window\nWindow {{\n    id: root\n    width: {width}\n    height: {height}\n    visible: true\n    title: "Parallax benchmark · quick · QQuickWindow"\n    color: "#17263a"\n    property real t: 0\n    readonly property real maxX: width * ({OVERSCAN} - 1.0) * 0.5 * {TRAVEL}\n    readonly property real maxY: height * ({OVERSCAN} - 1.0) * 0.5 * {TRAVEL}\n    Image {{\n        width: root.width * {OVERSCAN}\n        height: root.height * {OVERSCAN}\n        x: (root.width - width) * 0.5 + Math.sin(root.t * 1.37) * root.maxX\n        y: (root.height - height) * 0.5 + Math.sin(root.t * 0.91 + 0.7) * root.maxY\n        source: {json.dumps(QUrl.fromLocalFile(str(wallpaper_path)).toString())}\n        fillMode: Image.PreserveAspectCrop\n        smooth: true\n        cache: true\n    }}\n    FrameAnimation {{\n        running: true\n        onTriggered: root.t = elapsedTime\n    }}\n}}\n'''
    qml_path = output.parent / "quick_benchmark.qml"
    qml_path.write_text(qml, encoding="utf-8")

    engine = QQmlApplicationEngine()
    engine.load(QUrl.fromLocalFile(str(qml_path)))
    roots = engine.rootObjects()
    if not roots:
        raise RuntimeError("QML benchmark failed to load")
    window = roots[0]
    window.frameSwapped.connect(sink.on_frame, Qt.ConnectionType.DirectConnection)
    QTimer.singleShot(int(seconds * 1000), app.quit)
    app.exec()

    result = common_result("quick", "present_cadence", recorder, app, window)
    try:
        result["graphics_api"] = str(window.rendererInterface().graphicsApi())
    except Exception:
        result["graphics_api"] = "unknown"
    result["qsg_render_loop"] = os.environ.get("QSG_RENDER_LOOP", "")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def run_child(args: argparse.Namespace) -> int:
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if args.mode in {"widget60", "widget165"}:
        return run_widget(args.mode, args.seconds, output, args.width, args.height)
    if args.mode == "gl":
        return run_gl(args.seconds, output, args.width, args.height)
    if args.mode == "quick":
        return run_quick(args.seconds, output, args.width, args.height)
    raise ValueError(args.mode)


def score(result: dict[str, Any]) -> tuple[float, float]:
    return (float(result.get("interval_p95_ms") or 1e9), float(result.get("jitter_std_ms") or 1e9))


def run_suite(args: argparse.Namespace) -> int:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "logs" / "parallax-benchmark" / f"run-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    modes = ["widget60", "widget165", "gl", "quick"]
    results: list[dict[str, Any]] = []

    print(f"[BENCH] output={out_dir}")
    print(f"[BENCH] {len(modes)} modes × {args.seconds:.1f}s; each window auto-moves the same wallpaper.")
    for index, mode in enumerate(modes, start=1):
        result_path = out_dir / f"{mode}.json"
        cmd = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--child",
            "--mode",
            mode,
            "--seconds",
            str(args.seconds),
            "--width",
            str(args.width),
            "--height",
            str(args.height),
            "--output",
            str(result_path),
        ]
        print(f"[BENCH] {index}/{len(modes)} {mode} ...")
        completed = subprocess.run(cmd, cwd=ROOT, text=True)
        if completed.returncode != 0 or not result_path.is_file():
            print(f"[BENCH] {mode} FAILED · exit={completed.returncode}")
            continue
        result = json.loads(result_path.read_text(encoding="utf-8"))
        results.append(result)
        print(
            "[BENCH] {mode}: fps={fps:.1f} · p50/p95/p99={p50:.2f}/{p95:.2f}/{p99:.2f}ms · jitter={jitter:.2f}ms".format(
                mode=mode,
                fps=float(result.get("fps") or 0.0),
                p50=float(result.get("interval_p50_ms") or 0.0),
                p95=float(result.get("interval_p95_ms") or 0.0),
                p99=float(result.get("interval_p99_ms") or 0.0),
                jitter=float(result.get("jitter_std_ms") or 0.0),
            )
        )

    if not results:
        print("[BENCH] no successful renderer results")
        return 2

    ranked = sorted(results, key=score)
    summary = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "seconds_per_mode": args.seconds,
        "window_size": [args.width, args.height],
        "results": results,
        "ranking": [item["mode"] for item in ranked],
        "winner": ranked[0]["mode"],
    }
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[BENCH] ranking=" + " > ".join(summary["ranking"]))
    print(f"[BENCH] winner={summary['winner']}")
    print(f"[BENCH] summary={summary_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Isolated 165Hz parallax renderer benchmark")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--mode", choices=["widget60", "widget165", "gl", "quick"], default="widget60")
    parser.add_argument("--seconds", type=float, default=6.0)
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    parser.add_argument("--output", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.child:
        if not args.output:
            raise SystemExit("--output is required with --child")
        return run_child(args)
    return run_suite(args)


if __name__ == "__main__":
    raise SystemExit(main())
