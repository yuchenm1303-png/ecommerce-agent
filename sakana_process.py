from __future__ import annotations

import argparse
import base64
import ctypes
import json
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QWidget


_ROOT = Path(__file__).resolve().parent
_TOY_SIZE = 200
_CANVAS_SIZE = 300
_CANVAS_INSET = 50
_LEFT_MARGIN = 24
_BOTTOM_MARGIN = 170
_OWNER_POLL_MS = 33
_CONTROL_WIDTH = 112
_CONTROL_HEIGHT = 24
_CONTROL_X = _CANVAS_INSET + (_TOY_SIZE - _CONTROL_WIDTH) // 2
_CONTROL_Y = _CANVAS_INSET + _TOY_SIZE - _CONTROL_HEIGHT
_CHARACTER_IMAGE = _ROOT / "gui" / "assets" / "sakana_character.png"
_SAKANA_JS = _ROOT / "gui" / "assets" / "sakana-widget-2.7.1.js"

# This file is intentionally outside the gui package. Starting a helper through
# `python -m gui...` executes gui/__init__.py first, which imports application
# access/telemetry code before Sakana can start. The helper must bootstrap only
# QtWebEngine and the original Sakana browser runtime.
# Exact compiled form of Sakana Widget 2.7.1 src/index.scss. Keeping structural
# CSS local avoids making widget geometry depend on a second remote stylesheet.
_SAKANA_271_CSS = """
.sakana-widget *,.sakana-widget *::before,.sakana-widget *::after{box-sizing:border-box}
.sakana-widget-wrapper{pointer-events:none;position:relative;width:100%;height:100%}
.sakana-widget-app{pointer-events:none;position:relative}
.sakana-widget-canvas{z-index:10;pointer-events:none;position:absolute;top:50%;left:50%;transform:translate(-50%,-50%)}
.sakana-widget-main{z-index:20;pointer-events:none;position:absolute;display:flex;flex-direction:column;justify-content:space-between;align-items:center}
.sakana-widget-img{z-index:40;cursor:move;pointer-events:auto;position:relative;background:no-repeat 50% 50%;background-size:cover}
.sakana-widget-ctrl{z-index:30;cursor:move;pointer-events:auto;position:relative;height:24px;width:112px;display:flex;border-radius:6px;background-color:#ddd;box-shadow:0 8px 24px rgba(0,0,0,.1)}
.sakana-widget-ctrl-item{height:24px;width:28px;display:flex;justify-content:center;align-items:center;color:#555;background-color:transparent}
.sakana-widget-ctrl-item:hover{color:#555;background-color:rgba(255,255,255,.25)}
.sakana-widget-icon{height:18px;width:18px}
.sakana-widget-icon--rotate{animation:sakana-widget-spin 2s linear infinite}
@keyframes sakana-widget-spin{100%{transform:rotate(360deg)}}
""".strip()


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class _WindowsOwner:
    _HWND_TOPMOST = wintypes.HWND(-1)
    _SW_HIDE = 0
    _SWP_NOSIZE = 0x0001
    _SWP_NOACTIVATE = 0x0010
    _SWP_SHOWWINDOW = 0x0040

    def __init__(self, hwnd: int) -> None:
        if sys.platform != "win32":
            raise RuntimeError("The Sakana overlay process requires Windows")
        self.hwnd = int(hwnd)
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.IsWindow.argtypes = [wintypes.HWND]
        self.user32.IsWindow.restype = wintypes.BOOL
        self.user32.IsWindowVisible.argtypes = [wintypes.HWND]
        self.user32.IsWindowVisible.restype = wintypes.BOOL
        self.user32.IsIconic.argtypes = [wintypes.HWND]
        self.user32.IsIconic.restype = wintypes.BOOL
        self.user32.GetForegroundWindow.argtypes = []
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
        self.user32.GetClientRect.restype = wintypes.BOOL
        self.user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(_POINT)]
        self.user32.ClientToScreen.restype = wintypes.BOOL
        self.user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        self.user32.SetWindowPos.restype = wintypes.BOOL
        self.user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.ShowWindow.restype = wintypes.BOOL

    def exists(self) -> bool:
        return bool(self.user32.IsWindow(self.hwnd))

    def available(self) -> bool:
        # The toy is intentionally independent of the owner's presentation
        # state. Keep it visible while the application window still exists,
        # including while that window is minimized or not foreground.
        return self.exists()

    def active(self, child_hwnd: int) -> bool:
        foreground = int(self.user32.GetForegroundWindow() or 0)
        return foreground in {self.hwnd, int(child_hwnd)}

    def client_geometry(self) -> tuple[int, int, int, int] | None:
        rect = _RECT()
        origin = _POINT(0, 0)
        if not self.user32.GetClientRect(self.hwnd, ctypes.byref(rect)):
            return None
        if not self.user32.ClientToScreen(self.hwnd, ctypes.byref(origin)):
            return None
        return (
            int(origin.x),
            int(origin.y),
            int(rect.right - rect.left),
            int(rect.bottom - rect.top),
        )

    def present(self, child_hwnd: int, x: int, y: int) -> None:
        if not self.user32.SetWindowPos(
            wintypes.HWND(int(child_hwnd)),
            self._HWND_TOPMOST,
            int(x),
            int(y),
            0,
            0,
            self._SWP_NOSIZE | self._SWP_NOACTIVATE | self._SWP_SHOWWINDOW,
        ):
            raise OSError(ctypes.get_last_error(), "Unable to present Sakana overlay")

    def hide(self, child_hwnd: int) -> None:
        self.user32.ShowWindow(wintypes.HWND(int(child_hwnd)), self._SW_HIDE)


def _character_data_url() -> str:
    try:
        encoded = base64.b64encode(_CHARACTER_IMAGE.read_bytes()).decode("ascii")
    except OSError as exc:
        raise RuntimeError(f"Unable to read Sakana character image: {_CHARACTER_IMAGE}") from exc
    return f"data:image/png;base64,{encoded}"


def _sakana_js_source() -> str:
    try:
        return _SAKANA_JS.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Unable to read local Sakana runtime: {_SAKANA_JS}") from exc


def _html_source(*, image_url: str | None = None) -> str:
    image_url = image_url or _character_data_url()
    sakana_js = _sakana_js_source()
    return f'''<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
{_SAKANA_271_CSS}
html, body {{
    width: {_CANVAS_SIZE}px;
    height: {_CANVAS_SIZE}px;
    margin: 0;
    padding: 0;
    overflow: hidden;
    background: transparent;
}}
#sakana-widget {{
    position: absolute;
    left: {_CANVAS_INSET}px;
    top: {_CANVAS_INSET}px;
    width: {_TOY_SIZE}px;
    height: {_TOY_SIZE}px;
}}
.sakana-widget-ctrl {{
    background: #ffffff !important;
}}
.sakana-widget-ctrl-item {{
    visibility: hidden !important;
    pointer-events: none !important;
}}
</style>
</head>
<body>
<div id="sakana-widget"></div>
<script>{sakana_js}</script>
<script>
(() => {{
    function mountOriginalSakana() {{
        if (typeof SakanaWidget !== 'function')
            return false;
        const takina = SakanaWidget.getCharacter('takina');
        if (!takina)
            return false;
        takina.image = {json.dumps(image_url)};
        SakanaWidget.registerCharacter('__ecommerce_agent_character__', takina);
        window.__sakana = new SakanaWidget({{
            character: '__ecommerce_agent_character__'
        }}).mount('#sakana-widget');
        document.documentElement.dataset.sakanaReady = '1';
        return true;
    }}

    if (!mountOriginalSakana())
        document.documentElement.dataset.sakanaError = 'local-script';
}})();
</script>
</body>
</html>'''


class _BaseDragFilter(QObject):
    """Move only the native toy host while the white Sakana base is dragged."""

    def __init__(self, host: "SakanaProcessHost") -> None:
        super().__init__(host.view)
        self.host = host
        self._dragging = False
        self._press_global = None
        self._press_root = (0, 0)

    def _belongs_to_view(self, watched: QObject) -> bool:
        if not isinstance(watched, QWidget):
            return False
        current: QWidget | None = watched
        while current is not None:
            if current is self.host.view:
                return True
            current = current.parentWidget()
        return False

    def _base_hit(self, event: QMouseEvent) -> bool:
        point = self.host.view.mapFromGlobal(event.globalPosition().toPoint())
        return (
            _CONTROL_X <= point.x() < _CONTROL_X + _CONTROL_WIDTH
            and _CONTROL_Y <= point.y() < _CONTROL_Y + _CONTROL_HEIGHT
        )

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if not self._belongs_to_view(watched):
            return False
        event_type = event.type()
        if not isinstance(event, QMouseEvent):
            return False

        if event_type == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton and self._base_hit(event):
                self._dragging = True
                self._press_global = event.globalPosition().toPoint()
                self._press_root = self.host.root_position
                return True
            return False

        if event_type == QEvent.Type.MouseMove and self._dragging:
            if not (event.buttons() & Qt.MouseButton.LeftButton):
                self._dragging = False
                self._press_global = None
                return True
            current = event.globalPosition().toPoint()
            if self._press_global is not None:
                delta = current - self._press_global
                self.host.set_root_position(
                    self._press_root[0] + delta.x(),
                    self._press_root[1] + delta.y(),
                )
            return True

        if event_type == QEvent.Type.MouseButtonRelease and self._dragging:
            self._dragging = False
            self._press_global = None
            return True

        return False


class SakanaProcessHost:
    """Own the Sakana WebEngine surface in a process independent of the app UI."""

    def __init__(self, app: QApplication, owner_hwnd: int) -> None:
        self.app = app
        self.owner = _WindowsOwner(owner_hwnd)
        if not self.owner.exists():
            raise RuntimeError("Sakana owner window does not exist")
        if not _CHARACTER_IMAGE.is_file():
            raise RuntimeError(f"Sakana character image is missing: {_CHARACTER_IMAGE}")

        self.view = QWebEngineView()
        self.view.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.view.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.view.resize(_CANVAS_SIZE, _CANVAS_SIZE)

        self.profile = QWebEngineProfile(self.view)
        self.page = QWebEnginePage(self.profile, self.view)
        self.page.setBackgroundColor(QColor(0, 0, 0, 0))
        self.view.setPage(self.page)

        self._presented = False
        self._root_position: tuple[int, int] | None = None
        self._last_window_position: tuple[int, int] | None = None
        self.drag_filter = _BaseDragFilter(self)
        self.app.installEventFilter(self.drag_filter)
        self.view.setHtml(_html_source())
        # Materialize the final top-level native window before SetWindowPos.
        # WA_ShowWithoutActivating and WindowDoesNotAcceptFocus keep this from
        # stealing focus from the listing window.
        self.view.show()
        self.child_hwnd = int(self.view.winId())
        self._sync_owner()

        # Only native geometry/lifetime is sampled here. Sakana motion is still
        # Chromium requestAnimationFrame and never uses this timer.
        self.owner_timer = QTimer(self.view)
        self.owner_timer.setTimerType(Qt.TimerType.CoarseTimer)
        self.owner_timer.setInterval(_OWNER_POLL_MS)
        self.owner_timer.timeout.connect(self._sync_owner)
        self.owner_timer.start()

    @property
    def root_position(self) -> tuple[int, int]:
        geometry = self.owner.client_geometry()
        if geometry is None:
            return self._root_position or (_LEFT_MARGIN, 0)
        _left, _top, width, height = geometry
        if self._root_position is None:
            self._root_position = (
                min(_LEFT_MARGIN, max(0, width - _TOY_SIZE)),
                max(0, height - _TOY_SIZE - _BOTTOM_MARGIN),
            )
        return self._clamp_root(*self._root_position, width=width, height=height)

    @staticmethod
    def _clamp_root(x: int, y: int, *, width: int, height: int) -> tuple[int, int]:
        return (
            max(0, min(int(x), max(0, int(width) - _TOY_SIZE))),
            max(0, min(int(y), max(0, int(height) - _TOY_SIZE))),
        )

    def set_root_position(self, x: int, y: int) -> None:
        geometry = self.owner.client_geometry()
        if geometry is None:
            return
        _left, _top, width, height = geometry
        self._root_position = self._clamp_root(x, y, width=width, height=height)
        self._sync_owner()

    def _sync_owner(self) -> None:
        if not self.owner.exists():
            self.app.quit()
            return

        geometry = self.owner.client_geometry()
        if geometry is None or not self.owner.available():
            if self._presented:
                self.owner.hide(self.child_hwnd)
                self._presented = False
            return

        left, top, width, height = geometry
        root_x, root_y = self.root_position
        self._root_position = self._clamp_root(root_x, root_y, width=width, height=height)
        overlay_x = left + self._root_position[0] - _CANVAS_INSET
        overlay_y = top + self._root_position[1] - _CANVAS_INSET
        position = (overlay_x, overlay_y)
        if not self._presented or position != self._last_window_position:
            self.owner.present(self.child_hwnd, *position)
            self._last_window_position = position
            self._presented = True

    def cleanup(self) -> None:
        try:
            self.owner_timer.stop()
        except (AttributeError, RuntimeError):
            pass
        try:
            self.app.removeEventFilter(self.drag_filter)
        except RuntimeError:
            pass
        try:
            self.page.runJavaScript(
                "if (window.__sakana) window.__sakana.unmount();"
            )
        except RuntimeError:
            pass
        try:
            self.owner.hide(self.child_hwnd)
        except (AttributeError, OSError):
            pass
        self.view.close()


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--owner-hwnd", type=int, required=True)
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    app = QApplication([sys.argv[0], "--sakana-toy-process"])
    app.setApplicationName("ecommerce-agent Sakana Toy")
    app.setOrganizationName("ecommerce-agent")
    app.setQuitOnLastWindowClosed(False)

    host = SakanaProcessHost(app, args.owner_hwnd)
    app.aboutToQuit.connect(host.cleanup)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
