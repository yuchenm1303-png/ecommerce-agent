from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QTimer, Qt, QUrl
from PySide6.QtGui import QColor, QKeyEvent, QPixmap
from PySide6.QtQml import QQmlComponent
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtWidgets import QFrame, QLabel, QMainWindow

from .card_details_fast import FastCardDetailController


_OPEN_MS = 235
_CLOSE_MS = 165
_PANEL_RISE_PX = 18
_PANEL_CLOSE_DROP_PX = 12
_PANEL_OPEN_SCALE = 0.985
_PANEL_CLOSE_SCALE = 0.990

_STATE_CLOSED = "closed"
_STATE_OPENING_PENDING = "opening-pending"
_STATE_OPENING = "opening"
_STATE_OPEN = "open"
_STATE_CLOSING = "closing"


_TRANSITION_QML = rf'''
import QtQuick

Item {{
    id: root
    anchors.fill: parent
    visible: active

    property bool active: false
    property bool closingRequest: false
    property int command: 0
    property url baseUrl
    property url blurUrl
    property url panelUrl
    property real panelX: 0
    property real panelY: 0
    property real panelW: 1
    property real panelH: 1

    signal transitionFinished(bool opened)

    function prepareOpen() {{
        baseImage.opacity = 1.0
        blurImage.opacity = 0.0
        scrim.opacity = 0.0
        panelImage.opacity = 0.0
        panelImage.y = panelY + {_PANEL_RISE_PX}
        panelImage.scale = {_PANEL_OPEN_SCALE}
    }}

    function prepareClose() {{
        baseImage.opacity = 1.0
        blurImage.opacity = 1.0
        scrim.opacity = 1.0
        panelImage.opacity = 1.0
        panelImage.y = panelY
        panelImage.scale = 1.0
    }}

    onActiveChanged: {{
        if (!active)
            return
        if (closingRequest)
            prepareClose()
        else
            prepareOpen()
    }}

    onCommandChanged: {{
        if (command <= 0)
            return
        if (closingRequest)
            startClose()
        else
            startOpen()
    }}

    function startOpen() {{
        prepareOpen()
        openAnimation.restart()
    }}

    function startClose() {{
        prepareClose()
        closeAnimation.restart()
    }}

    Image {{
        id: baseImage
        anchors.fill: parent
        source: root.baseUrl
        cache: false
        asynchronous: false
        smooth: true
        fillMode: Image.Stretch
    }}

    Image {{
        id: blurImage
        anchors.fill: parent
        source: root.blurUrl
        cache: false
        asynchronous: false
        smooth: true
        fillMode: Image.Stretch
        opacity: 0.0
    }}

    Rectangle {{
        id: scrim
        anchors.fill: parent
        color: "#67101822"
        opacity: 0.0
    }}

    Image {{
        id: panelImage
        x: root.panelX
        y: root.panelY
        width: root.panelW
        height: root.panelH
        source: root.panelUrl
        cache: false
        asynchronous: false
        smooth: true
        fillMode: Image.Stretch
        transformOrigin: Item.Center
        opacity: 0.0
    }}

    ParallelAnimation {{
        id: openAnimation
        OpacityAnimator {{
            target: blurImage
            from: 0.0
            to: 1.0
            duration: 190
            easing.type: Easing.OutCubic
        }}
        OpacityAnimator {{
            target: scrim
            from: 0.0
            to: 1.0
            duration: 175
            easing.type: Easing.OutCubic
        }}
        OpacityAnimator {{
            target: panelImage
            from: 0.0
            to: 1.0
            duration: 155
            easing.type: Easing.OutCubic
        }}
        YAnimator {{
            target: panelImage
            from: root.panelY + {_PANEL_RISE_PX}
            to: root.panelY
            duration: {_OPEN_MS}
            easing.type: Easing.OutQuart
        }}
        ScaleAnimator {{
            target: panelImage
            from: {_PANEL_OPEN_SCALE}
            to: 1.0
            duration: {_OPEN_MS}
            easing.type: Easing.OutQuart
        }}
        onFinished: {{
            blurImage.opacity = 1.0
            scrim.opacity = 1.0
            panelImage.opacity = 1.0
            panelImage.y = root.panelY
            panelImage.scale = 1.0
            root.transitionFinished(true)
        }}
    }}

    ParallelAnimation {{
        id: closeAnimation
        OpacityAnimator {{
            target: blurImage
            from: 1.0
            to: 0.0
            duration: 150
            easing.type: Easing.InCubic
        }}
        OpacityAnimator {{
            target: scrim
            from: 1.0
            to: 0.0
            duration: 145
            easing.type: Easing.InCubic
        }}
        OpacityAnimator {{
            target: panelImage
            from: 1.0
            to: 0.0
            duration: 125
            easing.type: Easing.InCubic
        }}
        YAnimator {{
            target: panelImage
            from: root.panelY
            to: root.panelY + {_PANEL_CLOSE_DROP_PX}
            duration: {_CLOSE_MS}
            easing.type: Easing.InCubic
        }}
        ScaleAnimator {{
            target: panelImage
            from: 1.0
            to: {_PANEL_CLOSE_SCALE}
            duration: {_CLOSE_MS}
            easing.type: Easing.InCubic
        }}
        onFinished: root.transitionFinished(false)
    }}
}}
'''


class GlassModalInteractionController(QObject):
    """GPU modal transition hosted by a dedicated native Quick child window.

    The production window keeps the baseline QWidget tree continuously mapped.
    A short-lived child QQuickWindow is raised above that QWidget child only
    while an open/close animation is running. It renders static snapshots, so
    there is no QWidget geometry animation, layout reflow, or parent hide/show
    churn in the transition hot path.

    QML and the transition surface are created lazily after app.exec(). If any
    preparation step fails, the already-prepared QWidget modal remains usable.
    """

    def __init__(self, window: QMainWindow, details: FastCardDetailController) -> None:
        super().__init__(window)
        self.window = window
        self.details = details
        self.root = window.centralWidget()
        if self.root is None:
            raise RuntimeError("glass modal interaction requires a central widget")

        visual = getattr(window, "_visual_style", None)
        self.background = getattr(visual, "background", None)
        self.quick_window = getattr(self.background, "quick_window", None)
        self.engine = getattr(self.background, "engine", None)

        self.transition_window: QQuickWindow | None = None
        self.transition_item: QQuickItem | None = None
        self._transition_error = ""
        self._state = _STATE_CLOSED
        self._transitioning = False
        self._target_open = False
        self._command = 0
        self._snapshot_revision = 0
        self._base_snapshot = QPixmap()
        self._blur_snapshot = QPixmap()
        self._base_url = QUrl()
        self._blur_url = QUrl()
        self._passive_labels: dict[QLabel, bool] = {}

        self._original_capture_backdrop = self.details._capture_backdrop  # noqa: SLF001
        self.details._capture_backdrop = self._capture_raw_backdrop  # type: ignore[method-assign]  # noqa: SLF001

        self.details.drawer.installEventFilter(self)
        self.root.installEventFilter(self)
        self._install_passive_card_surfaces()
        self._rewire_close_inputs()
        window.destroyed.connect(self.cleanup)

    @staticmethod
    def _error_text(exc: Exception) -> str:
        text = str(exc).strip()
        return f"{type(exc).__name__}: {text}" if text else type(exc).__name__

    def _sync_transition_window_geometry(self, *_args: object) -> None:
        surface = self.transition_window
        owner = self.quick_window
        if surface is None or owner is None:
            return
        surface.setGeometry(0, 0, max(1, owner.width()), max(1, owner.height()))

    def _hide_transition_surface(self) -> None:
        if self.transition_item is not None:
            try:
                self.transition_item.setProperty("active", False)
            except RuntimeError:
                pass
        surface = self.transition_window
        if surface is not None:
            try:
                surface.hide()
            except RuntimeError:
                pass

    def _clear_snapshots(self) -> None:
        self._base_snapshot = QPixmap()
        self._blur_snapshot = QPixmap()
        self._base_url = QUrl()
        self._blur_url = QUrl()

    def _fallback_open(self, exc: Exception | None = None) -> None:
        if exc is not None:
            self._transition_error = self._error_text(exc)
        self._state = _STATE_OPEN
        self._transitioning = False
        self._target_open = True
        self._hide_transition_surface()

    def _fallback_closed(self, exc: Exception | None = None) -> None:
        if exc is not None:
            self._transition_error = self._error_text(exc)
        self._state = _STATE_CLOSED
        self._transitioning = False
        self._target_open = False
        try:
            self.details.close()
        except RuntimeError:
            pass
        self._hide_transition_surface()
        self._clear_snapshots()

    def _ensure_transition_surface(self) -> bool:
        if self.transition_window is not None and self.transition_item is not None:
            return True
        if self.quick_window is None or self.engine is None:
            self._transition_error = "native Quick renderer unavailable"
            return False

        surface: QQuickWindow | None = None
        component: QQmlComponent | None = None
        obj: QObject | None = None
        try:
            surface = QQuickWindow(self.quick_window)
            surface.setObjectName("glassModalTransitionWindow")
            surface.setFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowDoesNotAcceptFocus
            )
            surface.setColor(QColor(0, 0, 0, 0))
            surface.setGeometry(
                0,
                0,
                max(1, self.quick_window.width()),
                max(1, self.quick_window.height()),
            )
            surface.hide()

            component = QQmlComponent(self.engine, self)
            component.setData(
                _TRANSITION_QML.encode("utf-8"),
                QUrl("inline:glass-modal-transition.qml"),
            )
            status = component.status()
            if status != QQmlComponent.Status.Ready:
                errors = "\n".join(error.toString() for error in component.errors())
                self._transition_error = errors or f"QML component status={status}"
                return False

            obj = component.create()
            if not isinstance(obj, QQuickItem):
                self._transition_error = "QML transition root was not a QQuickItem"
                return False

            obj.setParent(self)
            obj.setParentItem(surface.contentItem())
            obj.setProperty("active", False)
            obj.transitionFinished.connect(self._on_transition_finished)  # type: ignore[attr-defined]

            self.transition_window = surface
            self.transition_item = obj
            surface = None
            obj = None
            self.quick_window.widthChanged.connect(self._sync_transition_window_geometry)
            self.quick_window.heightChanged.connect(self._sync_transition_window_geometry)
            self._transition_error = ""
            return True
        except Exception as exc:
            self._transition_error = self._error_text(exc)
            return False
        finally:
            if obj is not None:
                obj.deleteLater()
            if component is not None:
                component.deleteLater()
            if surface is not None:
                surface.hide()
                surface.close()
                surface.deleteLater()

    def _rewire_close_inputs(self) -> None:
        try:
            self.details.close_button.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.details.close_button.clicked.connect(self.request_close)

        try:
            self.details.scrim.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.details.scrim.clicked.connect(self.request_close)

    @staticmethod
    def _label_is_passive(label: QLabel) -> bool:
        flags = label.textInteractionFlags()
        interactive = (
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        return not bool(flags & interactive)

    def _install_passive_card_surfaces(self) -> None:
        for card in self.details._expandable_cards:  # noqa: SLF001
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            for label in card.findChildren(QLabel):
                if not self._label_is_passive(label):
                    continue
                previous = label.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                self._passive_labels[label] = previous
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def _capture_raw_backdrop(self) -> QPixmap:
        screen = self.window.screen()
        pixmap = QPixmap()
        if screen is not None:
            global_pos = self.root.mapToGlobal(QPoint(0, 0))
            screen_origin = screen.geometry().topLeft()
            local_pos = global_pos - screen_origin
            pixmap = screen.grabWindow(
                0,
                local_pos.x(),
                local_pos.y(),
                self.root.width(),
                self.root.height(),
            )
        if pixmap.isNull():
            pixmap = self.root.grab()
        return pixmap

    def _snapshot_path(self, stem: str, *, alpha: bool = False) -> Path:
        self._snapshot_revision += 1
        temp_dir = getattr(self.background, "_temp_dir", None)
        if not isinstance(temp_dir, Path):
            raise RuntimeError("native Quick temporary directory is unavailable")
        suffix = ".png" if alpha else ".bmp"
        return temp_dir / f"{stem}_{self._snapshot_revision}{suffix}"

    def _publish_pixmap(self, stem: str, pixmap: QPixmap, *, alpha: bool = False) -> QUrl:
        path = self._snapshot_path(stem, alpha=alpha)
        fmt = "PNG" if alpha else "BMP"
        if pixmap.isNull() or not pixmap.save(str(path), fmt):
            raise RuntimeError(f"Failed to publish {stem} snapshot for modal transition")
        return QUrl.fromLocalFile(str(path))

    def _configure_quick_item(self, base: QPixmap, blurred: QPixmap, panel: QPixmap) -> None:
        item = self.transition_item
        if item is None:
            return
        target = self.details._drawer_rect()  # noqa: SLF001
        self._base_snapshot = base
        self._blur_snapshot = blurred
        self._base_url = self._publish_pixmap("modal_base", base)
        self._blur_url = self._publish_pixmap("modal_blur", blurred)
        panel_url = self._publish_pixmap("modal_panel", panel, alpha=True)
        item.setProperty("baseUrl", self._base_url)
        item.setProperty("blurUrl", self._blur_url)
        item.setProperty("panelUrl", panel_url)
        item.setProperty("panelX", float(target.x()))
        item.setProperty("panelY", float(target.y()))
        item.setProperty("panelW", float(target.width()))
        item.setProperty("panelH", float(target.height()))

    def _issue_transition(self, *, closing: bool) -> None:
        item = self.transition_item
        surface = self.transition_window
        if item is None or surface is None:
            return

        item.setProperty("closingRequest", closing)
        item.setProperty("active", True)
        self._sync_transition_window_geometry()
        surface.show()
        surface.raise_()

        self._command += 1
        item.setProperty("command", self._command)
        surface.requestUpdate()

    def _start_open_transition(self) -> None:
        if self._state != _STATE_OPENING_PENDING:
            return

        try:
            base = self.details.backdrop.pixmap()
            if base.isNull():
                base = self._capture_raw_backdrop()
            blurred = self.details._blur_pixmap(base)  # noqa: SLF001
            if not blurred.isNull():
                self.details.backdrop.setPixmap(blurred)

            if not self._ensure_transition_surface():
                self._fallback_open()
                return

            panel = self.details.drawer.grab()
            self._configure_quick_item(base, blurred if not blurred.isNull() else base, panel)
            self._state = _STATE_OPENING
            self._transitioning = True
            self._target_open = True
            self._issue_transition(closing=False)
        except Exception as exc:
            self._fallback_open(exc)

    def request_close(self, *_args: object) -> None:
        if self._state != _STATE_OPEN:
            return
        if self.details.drawer.isHidden() and self.details.scrim.isHidden():
            self._state = _STATE_CLOSED
            self._target_open = False
            return

        try:
            if not self._ensure_transition_surface():
                self._fallback_closed()
                return

            base = self._base_snapshot
            if base.isNull():
                base = self._capture_raw_backdrop()
            blurred = self._blur_snapshot
            if blurred.isNull():
                blurred = self.details._blur_pixmap(base)  # noqa: SLF001
            panel = self.details.drawer.grab()
            self._configure_quick_item(base, blurred if not blurred.isNull() else base, panel)

            self._state = _STATE_CLOSING
            self._transitioning = True
            self._target_open = False
            self._issue_transition(closing=True)
        except Exception as exc:
            self._fallback_closed(exc)

    def _on_transition_finished(self, opened: bool) -> None:
        if opened:
            if self._state != _STATE_OPENING:
                return
            self._state = _STATE_OPEN
            self._transitioning = False
            self._target_open = True
            self._hide_transition_surface()
            self.details.close_button.setFocus(Qt.FocusReason.OtherFocusReason)
            return

        if self._state != _STATE_CLOSING:
            return
        self._state = _STATE_CLOSED
        self._transitioning = False
        self._target_open = False

        # Keep the Quick child covering the real widget modal until the modal
        # widgets are explicitly hidden. Then remove the transition surface in
        # the same GUI-thread turn, so no intermediate modal frame can resurface.
        self.details.close()
        self._hide_transition_surface()
        self._clear_snapshots()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()

        if watched is self.details.drawer and event_type == QEvent.Type.Show:
            if self._state == _STATE_CLOSED:
                self._state = _STATE_OPENING_PENDING
                QTimer.singleShot(0, self._start_open_transition)
            return False

        if watched is self.root and event_type == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            if event.key() == Qt.Key.Key_Escape and self._state == _STATE_OPEN:
                self.request_close()
                return True

        return False

    def cleanup(self) -> None:
        self._state = _STATE_CLOSED
        self._transitioning = False
        self._target_open = False
        try:
            self.details.drawer.removeEventFilter(self)
        except RuntimeError:
            pass
        try:
            self.root.removeEventFilter(self)
        except RuntimeError:
            pass

        for label, previous in tuple(self._passive_labels.items()):
            try:
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, previous)
            except RuntimeError:
                pass
        self._passive_labels.clear()

        try:
            self.details._capture_backdrop = self._original_capture_backdrop  # type: ignore[method-assign]  # noqa: SLF001
        except RuntimeError:
            pass

        if self.transition_item is not None:
            try:
                self.transition_item.setProperty("active", False)
                self.transition_item.setParentItem(None)
            except RuntimeError:
                pass
            self.transition_item.deleteLater()
            self.transition_item = None

        if self.transition_window is not None:
            try:
                self.transition_window.hide()
                self.transition_window.releaseResources()
                self.transition_window.close()
            except RuntimeError:
                pass
            self.transition_window.deleteLater()
            self.transition_window = None


def install_modal_interaction(
    window: QMainWindow,
    details: FastCardDetailController,
) -> GlassModalInteractionController:
    controller = GlassModalInteractionController(window, details)
    window._glass_modal_interaction = controller  # type: ignore[attr-defined]
    return controller
