from __future__ import annotations

import base64
import json
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtQuick import QQuickWindow
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget


_TOY_SIZE = 200
_CANVAS_SIZE = 300
_CANVAS_INSET = 50
_LEFT_MARGIN = 24
_BOTTOM_MARGIN = 18
_CHARACTER_IMAGE = Path(__file__).resolve().parent / "assets" / "sakana_character.png"

# Run the published Sakana Widget 2.7.1 browser build itself. The original JS
# remains the sole owner of requestAnimationFrame timing, Date.now(), DOM input,
# Canvas drawing and the r/y/t/w spring state.
_SAKANA_JS_SOURCES = (
    "https://cdn.jsdelivr.net/npm/sakana-widget@2.7.1/lib/sakana.min.js",
    "https://cdnjs.cloudflare.com/ajax/libs/sakana-widget/2.7.1/sakana.min.js",
)

# Exact compiled form of Sakana Widget 2.7.1 src/index.scss. Keep this local so
# the widget structure never depends on a second remote stylesheet request.
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
/* The only intentional visual differences from upstream. */
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


class SakanaToyController(QObject):
    """Host the original Sakana browser widget without porting its motion logic."""

    def __init__(self, window: QWidget, quick: QQuickWindow) -> None:
        super().__init__(window)
        self.window = window
        self.quick = quick
        self._enabled = True
        self._loaded = False
        self._shutting_down = False

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
        self.view.page().setBackgroundColor(QColor(0, 0, 0, 0))
        self.view.loadFinished.connect(self._on_load_finished)

        self.toggle = self._install_toggle()
        self.quick.installEventFilter(self)
        self.window.destroyed.connect(self.cleanup)

        # Establish native ownership before the first WebEngine load/show. This
        # keeps the transparent tool window in the QQuickWindow's z-order group.
        self._ensure_transient_parent()
        self.view.setHtml(_html_source())
        self.set_enabled(True)

    def _install_toggle(self) -> QPushButton:
        root = self.window.centralWidget()
        outer = root.layout() if root is not None else None
        if not isinstance(outer, QVBoxLayout) or outer.count() < 1:
            raise RuntimeError("Sakana toy expected the preserved application root layout")
        header = outer.itemAt(0).layout()
        if not isinstance(header, QHBoxLayout):
            raise RuntimeError("Sakana toy expected the common application header")

        button = QPushButton("玩具 · ON")
        button.setObjectName("quietButton")
        button.setCheckable(True)
        button.setChecked(True)
        button.setMinimumWidth(98)
        button.setToolTip("显示或隐藏左下角弹簧玩具")
        button.setStyleSheet(
            "QPushButton:checked {"
            "background-color: rgba(190, 113, 157, 150);"
            "border-color: rgba(255, 220, 239, 90);"
            "font-weight: 700;"
            "}"
        )
        button.toggled.connect(self.set_enabled)
        header.addWidget(button, 0, Qt.AlignmentFlag.AlignBottom)
        return button

    def _ensure_transient_parent(self) -> None:
        if self._shutting_down:
            return
        try:
            self.view.winId()
            handle = self.view.windowHandle()
            if handle is not None:
                handle.setTransientParent(self.quick)
        except RuntimeError:
            pass

    def _on_load_finished(self, ok: bool) -> None:
        if self._shutting_down:
            return
        self._loaded = bool(ok)
        self._ensure_transient_parent()
        self._sync_overlay()

    def _sync_overlay(self) -> None:
        if self._shutting_down:
            return
        self._ensure_transient_parent()
        try:
            origin = self.quick.mapToGlobal(QPoint(0, 0))
            x = origin.x() + _LEFT_MARGIN - _CANVAS_INSET
            y = (
                origin.y()
                + self.quick.height()
                - _TOY_SIZE
                - _BOTTOM_MARGIN
                - _CANVAS_INSET
            )
            visible = bool(
                self._enabled
                and self.quick.isVisible()
                and not (self.quick.windowState() & Qt.WindowState.WindowMinimized)
            )
        except RuntimeError:
            return

        self.view.move(int(x), int(y))
        if visible:
            self.view.show()
            self.view.raise_()
        else:
            self.view.hide()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.quick and event.type() in {
            QEvent.Type.Move,
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.Hide,
            QEvent.Type.Expose,
            QEvent.Type.WindowActivate,
            QEvent.Type.WindowStateChange,
        }:
            self._sync_overlay()
        return False

    def set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        self._enabled = enabled
        if self.toggle.isChecked() != enabled:
            self.toggle.blockSignals(True)
            self.toggle.setChecked(enabled)
            self.toggle.blockSignals(False)
        self.toggle.setText("玩具 · ON" if enabled else "玩具 · OFF")

        # Sakana Widget 2.7.1 exposes unmount(), not show()/hide(). The app toggle
        # therefore controls only the native host visibility and never mutates the
        # upstream animation/runtime state.
        self._sync_overlay()

    def raise_overlay(self) -> None:
        if self._enabled and self.view.isVisible():
            self._ensure_transient_parent()
            self.view.raise_()

    def cleanup(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        try:
            self.quick.removeEventFilter(self)
        except RuntimeError:
            pass
        try:
            if self._loaded:
                self.view.page().runJavaScript(
                    "if (window.__sakana) window.__sakana.unmount();"
                )
        except RuntimeError:
            pass
        self.view.hide()
        self.view.close()
        self.view.deleteLater()


def install_sakana_toy(window: QWidget) -> SakanaToyController:
    existing = getattr(window, "_sakana_toy_controller", None)
    if isinstance(existing, SakanaToyController):
        return existing

    visual = getattr(window, "_visual_style", None)
    background = getattr(visual, "background", None)
    quick = getattr(background, "quick_window", None)
    if not isinstance(quick, QQuickWindow):
        raise RuntimeError("Sakana toy requires the existing unified QQuickWindow")

    controller = SakanaToyController(window, quick)
    window._sakana_toy_controller = controller  # type: ignore[attr-defined]
    return controller
