from __future__ import annotations

import argparse
import base64
import ctypes
import json
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication


_TOY_SIZE = 200
_CANVAS_SIZE = 300
_CANVAS_INSET = 50
_LEFT_MARGIN = 24
_BOTTOM_MARGIN = 18
_OWNER_POLL_MS = 33
_CHARACTER_IMAGE = Path(__file__).resolve().parent / "assets" / "sakana_character.png"

# Sakana Widget 2.7.1 remains the sole animation/physics owner. Its browser
# requestAnimationFrame loop, Date.now(), DOM input, Canvas drawing and spring
# state execute only in this dedicated process.
_SAKANA_JS_SOURCES = (
    "https://cdn.jsdelivr.net/npm/sakana-widget@2.7.1/lib/sakana.min.js",
    "https://cdnjs.cloudflare.com/ajax/libs/sakana-widget/2.7.1/sakana.min.js",
)

# Exact compiled form of Sakana Widget 2.7.1 src/index.scss. Keeping structural
# CSS local avoids making widget geometry depend on a second network request.
_SAKANA_271_CSS = """
.sakana-widget *,.sakana-widget *::before,.sakana-widget *::after{box-sizing:border-box}
.sakana-widget-wrapper{pointer-events:none;position:relative;width:100%;height:100%}
.sakana-widget-app{pointer-events:none;position:relative}
.sakana-widget-canvas{z-index:10;pointer-events:none;position:absolute;top:50%;left:50%;transform:translate(-50%,-50%)}
.sakana-widget-main{z-index:20;pointer-events:none;position:absolute;display:flex;flex-direction:column;justify-content:space-between;align-items:center}
.sakana-widget-img{z-index:40;cursor:move;pointer-events:auto;position:relative;background:no-repeat 50% 50%;background-size:cover}
.sakana-widget-ctrl{z-index:30;cursor:pointer;pointer-events:auto;position:relative;height:24px;width:112px;display:flex;border-radius:6px;background-color:#ddd;box-shadow:0 8px 24px rgba(0,0,0,.1)}
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
    GWLP_HWNDPARENT = -8

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
        self.user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
        self.user32.GetClientRect.restype = wintypes.BOOL
        self.user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(_POINT)]
        self.user32.ClientToScreen.restype = wintypes.BOOL

        set_owner = getattr(self.user32, "SetWindowLongPtrW", None)
        if set_owner is None:
            set_owner = self.user32.SetWindowLongW
        set_owner.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        set_owner.restype = ctypes.c_void_p
        self._set_owner = set_owner

    def exists(self) -> bool:
        return bool(self.user32.IsWindow(self.hwnd))

    def visible(self) -> bool:
        return bool(self.user32.IsWindowVisible(self.hwnd)) and not bool(
            self.user32.IsIconic(self.hwnd)
        )

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

    def own(self, child_hwnd: int) -> None:
        ctypes.set_last_error(0)
        self._set_owner(
            wintypes.HWND(int(child_hwnd)),
            self.GWLP_HWNDPARENT,
            ctypes.c_void_p(self.hwnd),
        )
        error = ctypes.get_last_error()
        if error:
            raise OSError(error, "Unable to assign Sakana native window owner")


def _character_data_url() -> str:
    try:
        encoded = base64.b64encode(_CHARACTER_IMAGE.read_bytes()).decode("ascii")
    except OSError as exc:
        raise RuntimeError(f"Unable to read Sakana character image: {_CHARACTER_IMAGE}") from exc
    return f"data:image/png;base64,{encoded}"


def _html_source() -> str:
    image_url = _character_data_url()
    js_sources = json.dumps(_SAKANA_JS_SOURCES)
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
<script>
(() => {{
    const sources = {js_sources};

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

    function loadOriginalSakana(index) {{
        if (index >= sources.length) {{
            document.documentElement.dataset.sakanaError = 'script-load';
            return;
        }}
        const script = document.createElement('script');
        script.src = sources[index];
        script.async = false;
        script.onload = () => {{
            if (!mountOriginalSakana()) {{
                script.remove();
                loadOriginalSakana(index + 1);
            }}
        }};
        script.onerror = () => {{
            script.remove();
            loadOriginalSakana(index + 1);
        }};
        document.head.appendChild(script);
    }}

    loadOriginalSakana(0);
}})();
</script>
</body>
</html>'''


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

        # An off-the-record profile belongs only to this process and never shares
        # browser cache/session state with the listing application.
        self.profile = QWebEngineProfile(self.view)
        self.page = QWebEnginePage(self.profile, self.view)
        self.page.setBackgroundColor(QColor(0, 0, 0, 0))
        self.view.setPage(self.page)

        child_hwnd = int(self.view.winId())
        self.owner.own(child_hwnd)

        self._last_position: tuple[int, int] | None = None
        self._last_visible: bool | None = None
        self._sync_owner()
        self.view.setHtml(_html_source())

        # This timer only follows native owner geometry/lifetime. It never drives
        # Sakana animation or spring state; Chromium requestAnimationFrame does.
        self.owner_timer = QTimer(self.view)
        self.owner_timer.setTimerType(Qt.TimerType.CoarseTimer)
        self.owner_timer.setInterval(_OWNER_POLL_MS)
        self.owner_timer.timeout.connect(self._sync_owner)
        self.owner_timer.start()

    def _sync_owner(self) -> None:
        if not self.owner.exists():
            self.app.quit()
            return

        geometry = self.owner.client_geometry()
        should_show = self.owner.visible() and geometry is not None
        if geometry is not None:
            left, top, _width, height = geometry
            position = (
                left + _LEFT_MARGIN - _CANVAS_INSET,
                top + height - _TOY_SIZE - _BOTTOM_MARGIN - _CANVAS_INSET,
            )
            if position != self._last_position:
                self.view.move(*position)
                self._last_position = position

        if should_show != self._last_visible:
            if should_show:
                self.view.show()
                self.view.raise_()
            else:
                self.view.hide()
            self._last_visible = should_show

    def cleanup(self) -> None:
        try:
            self.owner_timer.stop()
        except (AttributeError, RuntimeError):
            pass
        try:
            self.page.runJavaScript(
                "if (window.__sakana) window.__sakana.unmount();"
            )
        except RuntimeError:
            pass
        self.view.hide()
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
