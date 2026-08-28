from __future__ import annotations

import base64
import json
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QUrl
from PySide6.QtGui import QColor
from PySide6.QtQuick import QQuickWindow
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget


_TOY_SIZE = 200
_CANVAS_SIZE = 300
_CANVAS_INSET = 50
_LEFT_MARGIN = 24
_BOTTOM_MARGIN = 18
_CHARACTER_IMAGE = Path(__file__).resolve().parent / "assets" / "sakana_character.png"

# The pcp.moe bundle supplied for this project is Sakana Widget 2.7.1. Run the
# published browser build itself so its requestAnimationFrame loop, Date.now()
# timing, DOM mouse lifecycle, Canvas drawing and CSS transforms are not
# reimplemented by Qt/Python.
_SAKANA_JS = "https://cdnjs.cloudflare.com/ajax/libs/sakana-widget/2.7.1/sakana.min.js"
_SAKANA_CSS = "https://cdnjs.cloudflare.com/ajax/libs/sakana-widget/2.7.1/sakana.min.css"


def _character_data_url() -> str:
    try:
        encoded = base64.b64encode(_CHARACTER_IMAGE.read_bytes()).decode("ascii")
    except OSError as exc:
        raise RuntimeError(f"Unable to read Sakana character image: {_CHARACTER_IMAGE}") from exc
    return f"data:image/png;base64,{encoded}"


def _html_source() -> str:
    image_url = _character_data_url()
    return f'''<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="{_SAKANA_CSS}">
<style>
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
/* Product-requested visual-only difference: keep the original controller box,
   but render it plain white without the four symbols. */
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
<script src="{_SAKANA_JS}"></script>
<script>
(() => {{
    const takina = SakanaWidget.getCharacter('takina');
    takina.image = {json.dumps(image_url)};
    SakanaWidget.registerCharacter('__ecommerce_agent_character__', takina);
    window.__sakana = new SakanaWidget({{
        character: '__ecommerce_agent_character__'
    }}).mount('#sakana-widget');
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

        settings = self.view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        self.view.loadFinished.connect(self._on_load_finished)

        self.toggle = self._install_toggle()
        self.quick.installEventFilter(self)
        self.window.destroyed.connect(self.cleanup)

        base_url = QUrl.fromLocalFile(str(_CHARACTER_IMAGE.parent) + "/")
        self.view.setHtml(_html_source(), base_url)
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

    def _on_load_finished(self, ok: bool) -> None:
        if self._shutting_down:
            return
        self._loaded = bool(ok)
        handle = self.view.windowHandle()
        if handle is not None:
            handle.setTransientParent(self.quick)
        if not self._enabled and self._loaded:
            self.view.page().runJavaScript(
                "if (window.__sakana) window.__sakana.hide();"
            )
        self._sync_overlay()

    def _sync_overlay(self) -> None:
        if self._shutting_down:
            return
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

        if self._loaded:
            script = (
                "if (window.__sakana) window.__sakana.show();"
                if enabled
                else "if (window.__sakana) window.__sakana.hide();"
            )
            self.view.page().runJavaScript(script)
        self._sync_overlay()

    def raise_overlay(self) -> None:
        if self._enabled and self.view.isVisible():
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
