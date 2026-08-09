from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import PySide6
from PySide6.QtCore import QEvent, QObject, QPoint, QTimer, Qt, qVersion
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget


_GWL_STYLE = -16
_GWL_EXSTYLE = -20
_GW_HWNDNEXT = 2
_GW_HWNDPREV = 3
_GW_OWNER = 4
_GA_ROOT = 2
_GA_ROOTOWNER = 3


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _rect_tuple(rect: Any) -> list[int]:
    return [int(rect.x()), int(rect.y()), int(rect.width()), int(rect.height())]


def _point_tuple(point: Any) -> list[int]:
    return [int(point.x()), int(point.y())]


def _qwindow_snapshot(window: Any) -> dict[str, Any]:
    if window is None:
        return {}
    screen = window.screen()
    frame_margins = window.frameMargins()
    return {
        "win_id": int(window.winId()),
        "geometry": _rect_tuple(window.geometry()),
        "frame_geometry": _rect_tuple(window.frameGeometry()),
        "position": _point_tuple(window.position()),
        "size": [int(window.width()), int(window.height())],
        "visible": bool(window.isVisible()),
        "exposed": bool(window.isExposed()),
        "active": bool(window.isActive()),
        "state": int(window.windowState()),
        "flags": int(window.flags()),
        "dpr": float(window.devicePixelRatio()),
        "frame_margins": [
            int(frame_margins.left()),
            int(frame_margins.top()),
            int(frame_margins.right()),
            int(frame_margins.bottom()),
        ],
        "screen": None
        if screen is None
        else {
            "name": screen.name(),
            "geometry": _rect_tuple(screen.geometry()),
            "available_geometry": _rect_tuple(screen.availableGeometry()),
            "dpr": float(screen.devicePixelRatio()),
            "refresh_hz": float(screen.refreshRate()),
        },
    }


def _qwidget_snapshot(widget: QWidget) -> dict[str, Any]:
    handle = widget.windowHandle()
    screen = widget.screen()
    return {
        "win_id": int(widget.winId()),
        "geometry": _rect_tuple(widget.geometry()),
        "frame_geometry": _rect_tuple(widget.frameGeometry()),
        "rect": _rect_tuple(widget.rect()),
        "global_origin": _point_tuple(widget.mapToGlobal(QPoint(0, 0))),
        "visible": bool(widget.isVisible()),
        "active": bool(widget.isActiveWindow()),
        "minimized": bool(widget.isMinimized()),
        "maximized": bool(widget.isMaximized()),
        "window_state": int(widget.windowState()),
        "flags": int(widget.windowFlags()),
        "dpr": float(widget.devicePixelRatioF()),
        "handle": _qwindow_snapshot(handle),
        "screen": None
        if screen is None
        else {
            "name": screen.name(),
            "geometry": _rect_tuple(screen.geometry()),
            "available_geometry": _rect_tuple(screen.availableGeometry()),
            "dpr": float(screen.devicePixelRatio()),
            "refresh_hz": float(screen.refreshRate()),
        },
    }


def _win32_snapshot(hwnd: int) -> dict[str, Any]:
    if sys.platform != "win32" or not hwnd:
        return {}

    user32 = ctypes.windll.user32
    get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    get_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
    get_long.restype = ctypes.c_ssize_t

    user32.GetWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    user32.GetWindow.restype = ctypes.c_void_p
    user32.GetParent.argtypes = [ctypes.c_void_p]
    user32.GetParent.restype = ctypes.c_void_p
    user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    user32.GetAncestor.restype = ctypes.c_void_p

    handle = ctypes.c_void_p(hwnd)
    window_rect = _RECT()
    client_rect = _RECT()
    client_origin = _POINT(0, 0)
    user32.GetWindowRect(handle, ctypes.byref(window_rect))
    user32.GetClientRect(handle, ctypes.byref(client_rect))
    user32.ClientToScreen(handle, ctypes.byref(client_origin))

    def as_int(value: Any) -> int:
        return int(value or 0)

    return {
        "hwnd": hwnd,
        "window_rect": [
            int(window_rect.left),
            int(window_rect.top),
            int(window_rect.right - window_rect.left),
            int(window_rect.bottom - window_rect.top),
        ],
        "client_rect": [
            int(client_origin.x),
            int(client_origin.y),
            int(client_rect.right - client_rect.left),
            int(client_rect.bottom - client_rect.top),
        ],
        "style_hex": hex(int(get_long(handle, _GWL_STYLE)) & 0xFFFFFFFFFFFFFFFF),
        "exstyle_hex": hex(int(get_long(handle, _GWL_EXSTYLE)) & 0xFFFFFFFFFFFFFFFF),
        "owner": as_int(user32.GetWindow(handle, _GW_OWNER)),
        "parent": as_int(user32.GetParent(handle)),
        "root": as_int(user32.GetAncestor(handle, _GA_ROOT)),
        "root_owner": as_int(user32.GetAncestor(handle, _GA_ROOTOWNER)),
        "z_prev": as_int(user32.GetWindow(handle, _GW_HWNDPREV)),
        "z_next": as_int(user32.GetWindow(handle, _GW_HWNDNEXT)),
        "visible": bool(user32.IsWindowVisible(handle)),
        "enabled": bool(user32.IsWindowEnabled(handle)),
        "foreground": as_int(user32.GetForegroundWindow()),
    }


def _delta(a: list[int] | None, b: list[int] | None) -> list[int] | None:
    if not a or not b or len(a) != 4 or len(b) != 4:
        return None
    return [a[i] - b[i] for i in range(4)]


class WindowDiagnostics(QObject):
    """Low-overhead, opt-in geometry / Win32 / frame-cadence recorder."""

    _SAMPLE_MS = 1000

    def __init__(
        self,
        overlay: QMainWindow,
        background: Any,
        shell: Any,
        project_root: Path,
    ) -> None:
        super().__init__(overlay)
        self.overlay = overlay
        self.background = background
        self.shell = shell
        self.quick = background.quick_window
        if self.quick is None:
            raise RuntimeError("Window diagnostics require the Quick owner window")

        log_dir = project_root / "logs" / "gui"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.path = log_dir / "window-diagnostics-latest.jsonl"
        self._stream = self.path.open("w", encoding="utf-8", buffering=1)
        self._closed = False
        self._mouse_moves = 0
        self._event_reasons: set[str] = set()
        self._event_flush_pending = False
        self._last_sample = time.perf_counter()
        self._last_frame_count = 0

        self.quick.setProperty("diagnosticsEnabled", True)
        self._last_frame_count = int(self.quick.property("diagFrameCount") or 0)

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            app.aboutToQuit.connect(self.close)

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(self._SAMPLE_MS)
        self._timer.timeout.connect(self._periodic_sample)
        self._timer.start()

        self._write(
            "session_start",
            {
                "python": sys.version,
                "pyside": PySide6.__version__,
                "qt": qVersion(),
                "platform": sys.platform,
                "qsg_render_loop": os.environ.get("QSG_RENDER_LOOP"),
                "pid": os.getpid(),
                "log_path": str(self.path),
            },
        )
        QTimer.singleShot(500, lambda: self.snapshot("startup_500ms"))
        QTimer.singleShot(1500, lambda: self.snapshot("startup_1500ms"))
        print(f"[GUI diagnostics] {self.path}", flush=True)

    def _write(self, event: str, payload: dict[str, Any]) -> None:
        if self._closed:
            return
        record = {
            "t_wall": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "t_mono": round(time.perf_counter(), 6),
            "event": event,
            **payload,
        }
        self._stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    def _card_snapshot(self, owner_client: list[int] | None) -> list[dict[str, Any]]:
        cards: list[dict[str, Any]] = []
        quick_qt_origin = self.quick.mapToGlobal(QPoint(0, 0))
        for index, frame in enumerate(getattr(self.background, "_cards", [])):
            top_left = frame.mapToGlobal(QPoint(0, 0))
            global_rect = [top_left.x(), top_left.y(), frame.width(), frame.height()]
            row: dict[str, Any] = {
                "index": index,
                "object": frame.objectName(),
                "visible": bool(frame.isVisibleTo(self.overlay)),
                "global_rect": global_rect,
                "quick_relative_qt": [
                    top_left.x() - quick_qt_origin.x(),
                    top_left.y() - quick_qt_origin.y(),
                    frame.width(),
                    frame.height(),
                ],
            }
            if owner_client:
                row["quick_relative_win32"] = [
                    top_left.x() - owner_client[0],
                    top_left.y() - owner_client[1],
                    frame.width(),
                    frame.height(),
                ]
            cards.append(row)
        return cards

    def snapshot(self, reason: str) -> None:
        if self._closed:
            return

        owner_qt = _qwindow_snapshot(self.quick)
        overlay_qt = _qwidget_snapshot(self.overlay)
        owner_win32 = _win32_snapshot(int(self.quick.winId()))
        overlay_win32 = _win32_snapshot(int(self.overlay.winId()))
        owner_client = owner_win32.get("client_rect")
        overlay_window = overlay_win32.get("window_rect")

        quick_props = {
            "pointer_x": float(self.quick.property("pointerX") or 0.0),
            "pointer_y": float(self.quick.property("pointerY") or 0.0),
            "offset_x": float(self.quick.property("offsetX") or 0.0),
            "offset_y": float(self.quick.property("offsetY") or 0.0),
            "target_x": float(self.quick.property("targetX") or 0.0),
            "target_y": float(self.quick.property("targetY") or 0.0),
            "animation_running": bool(self.quick.property("animationRunning")),
            "diag_frame_count": int(self.quick.property("diagFrameCount") or 0),
            "diag_frame_time_ms": float(self.quick.property("diagFrameTimeMs") or 0.0),
            "mask_revision": int(getattr(self.background, "_mask_revision", 0)),
        }

        self._write(
            "snapshot",
            {
                "reason": reason,
                "owner_qt": owner_qt,
                "overlay_qt": overlay_qt,
                "owner_win32": owner_win32,
                "overlay_win32": overlay_win32,
                "overlay_minus_owner_client": _delta(overlay_window, owner_client),
                "quick": quick_props,
                "cards": self._card_snapshot(owner_client),
            },
        )

    def _periodic_sample(self) -> None:
        if self._closed:
            return
        now = time.perf_counter()
        elapsed = max(1e-6, now - self._last_sample)
        ui_timer_lag_ms = (elapsed * 1000.0) - self._SAMPLE_MS
        frame_count = int(self.quick.property("diagFrameCount") or 0)
        frame_delta = frame_count - self._last_frame_count
        frame_hz = frame_delta / elapsed
        mouse_hz = self._mouse_moves / elapsed

        self._write(
            "cadence",
            {
                "sample_elapsed_ms": round(elapsed * 1000.0, 3),
                "ui_timer_lag_ms": round(ui_timer_lag_ms, 3),
                "quick_frames": frame_delta,
                "quick_frame_hz": round(frame_hz, 3),
                "quick_last_frame_ms": round(float(self.quick.property("diagFrameTimeMs") or 0.0), 4),
                "mouse_moves": self._mouse_moves,
                "mouse_hz": round(mouse_hz, 3),
                "animation_running": bool(self.quick.property("animationRunning")),
                "offset": [
                    round(float(self.quick.property("offsetX") or 0.0), 4),
                    round(float(self.quick.property("offsetY") or 0.0), 4),
                ],
                "target": [
                    round(float(self.quick.property("targetX") or 0.0), 4),
                    round(float(self.quick.property("targetY") or 0.0), 4),
                ],
            },
        )
        self._last_sample = now
        self._last_frame_count = frame_count
        self._mouse_moves = 0

    def _queue_event_snapshot(self, reason: str) -> None:
        self._event_reasons.add(reason)
        if self._event_flush_pending:
            return
        self._event_flush_pending = True
        QTimer.singleShot(120, self._flush_event_snapshot)

    def _flush_event_snapshot(self) -> None:
        self._event_flush_pending = False
        reasons = sorted(self._event_reasons)
        self._event_reasons.clear()
        self.snapshot("events:" + ",".join(reasons))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()
        if event_type == QEvent.Type.MouseMove:
            self._mouse_moves += 1

        if watched is self.overlay or watched is self.quick:
            if event_type in {
                QEvent.Type.Move,
                QEvent.Type.Resize,
                QEvent.Type.Show,
                QEvent.Type.Hide,
                QEvent.Type.WindowStateChange,
                QEvent.Type.WindowActivate,
                QEvent.Type.WindowDeactivate,
                QEvent.Type.ActivationChange,
                QEvent.Type.Expose,
                QEvent.Type.ZOrderChange,
            }:
                name = getattr(event_type, "name", str(int(event_type)))
                who = "overlay" if watched is self.overlay else "quick"
                self._queue_event_snapshot(f"{who}:{name}")
        return False

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.snapshot("session_end")
        except Exception as exc:  # diagnostics must never block shutdown
            self._write("diagnostic_error", {"where": "close", "error": repr(exc)})
        self._closed = True
        self._timer.stop()
        try:
            self.quick.setProperty("diagnosticsEnabled", False)
        except RuntimeError:
            pass
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._stream.close()


def install_window_diagnostics(
    overlay: QMainWindow,
    background: Any,
    shell: Any,
    project_root: Path,
) -> WindowDiagnostics | None:
    if os.environ.get("ECOM_GUI_DIAGNOSTICS", "").strip() != "1":
        return None
    diagnostics = WindowDiagnostics(overlay, background, shell, project_root)
    overlay._window_diagnostics = diagnostics  # type: ignore[attr-defined]
    return diagnostics
