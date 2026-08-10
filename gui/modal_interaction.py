from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QTimer, Qt, QUrl
from PySide6.QtGui import QKeyEvent, QPixmap
from PySide6.QtQml import QQmlComponent
from PySide6.QtQuick import QQuickItem
from PySide6.QtWidgets import QFrame, QLabel, QMainWindow

from .card_details_fast import FastCardDetailController


_OPEN_MS = 235
_CLOSE_MS = 165
_PANEL_RISE_PX = 18
_PANEL_CLOSE_DROP_PX = 12
_PANEL_OPEN_SCALE = 0.985
_PANEL_CLOSE_SCALE = 0.990


_TRANSITION_QML = rf'''
import QtQuick
import QtQuick.Effects

Item {{
    id: root
    anchors.fill: parent
    z: 100000
    visible: active

    property bool active: false
    property bool closingRequest: false
    property int command: 0
    property url baseUrl
    property url panelUrl
    property real panelX: 0
    property real panelY: 0
    property real panelW: 1
    property real panelH: 1

    signal transitionFinished(bool opened)

    onCommandChanged: {{
        if (command <= 0)
            return
        if (closingRequest)
            startClose()
        else
            startOpen()
    }}

    function startOpen() {{
        active = true
        baseImage.opacity = 1.0
        blurLayer.opacity = 0.0
        scrim.opacity = 0.0
        panelImage.opacity = 0.0
        panelImage.y = panelY + {_PANEL_RISE_PX}
        panelImage.scale = {_PANEL_OPEN_SCALE}
        openAnimation.restart()
    }}

    function startClose() {{
        active = true
        baseImage.opacity = 1.0
        blurLayer.opacity = 1.0
        scrim.opacity = 1.0
        panelImage.opacity = 1.0
        panelImage.y = panelY
        panelImage.scale = 1.0
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

    MultiEffect {{
        id: blurLayer
        anchors.fill: parent
        source: baseImage
        opacity: 0.0
        blurEnabled: true
        blur: 0.72
        blurMax: 28
        autoPaddingEnabled: false
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
            target: blurLayer
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
            blurLayer.opacity = 1.0
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
            target: blurLayer
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
    """GPU-composited modal transitions over the existing QWidget application.

    The real QWidget tree only exists in two stable states: normal UI and the
    fully prepared modal. During the 150-235 ms transition the QWidget child HWND
    is hidden and the already-existing threaded QQuickWindow animates two static
    snapshots with scene-graph Animator types. No real table, text widget,
    splitter or card is repainted frame-by-frame.
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
        if self.quick_window is None or self.engine is None:
            raise RuntimeError("glass modal interaction requires the native Quick renderer")

        self._transitioning = False
        self._target_open = False
        self._command = 0
        self._snapshot_revision = 0
        self._base_snapshot = QPixmap()
        self._base_url = QUrl()
        self._static_blur_ready = False
        self._passive_labels: dict[QLabel, bool] = {}

        # FastCardDetailController still needs one blurred screenshot for its
        # final resting state. Capture only the raw frame in the click path; the
        # CPU blur is deferred until the QWidget layer is hidden and the Quick
        # render-thread animation has already started.
        self._original_capture_backdrop = self.details._capture_backdrop  # noqa: SLF001
        self.details._capture_backdrop = self._capture_raw_backdrop  # type: ignore[method-assign]  # noqa: SLF001

        self.transition_item = self._create_quick_transition_item()
        self.transition_item.transitionFinished.connect(self._on_transition_finished)  # type: ignore[attr-defined]

        self.details.drawer.installEventFilter(self)
        self.root.installEventFilter(self)
        self._install_passive_card_surfaces()
        self._rewire_close_inputs()
        window.destroyed.connect(self.cleanup)

    def _create_quick_transition_item(self) -> QQuickItem:
        component = QQmlComponent(self.engine, self)
        component.setData(_TRANSITION_QML.encode("utf-8"), QUrl("inline:glass-modal-transition.qml"))
        if component.isError():
            errors = "\n".join(error.toString() for error in component.errors())
            raise RuntimeError(f"Glass modal transition QML failed to compile:\n{errors}")
        obj = component.create()
        if not isinstance(obj, QQuickItem):
            raise RuntimeError("Glass modal transition did not create a QQuickItem")
        obj.setParent(self)
        obj.setParentItem(self.quick_window.contentItem())
        obj.setProperty("active", False)
        return obj

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
        """Let ordinary card text behave as part of the card hit target itself."""

        for card in self.details._expandable_cards:  # noqa: SLF001 - presentation adapter
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

    def _snapshot_path(self, stem: str) -> Path:
        self._snapshot_revision += 1
        temp_dir = getattr(self.background, "_temp_dir", None)
        if not isinstance(temp_dir, Path):
            raise RuntimeError("native Quick temporary directory is unavailable")
        return temp_dir / f"{stem}_{self._snapshot_revision}.bmp"

    def _publish_pixmap(self, stem: str, pixmap: QPixmap) -> QUrl:
        path = self._snapshot_path(stem)
        if pixmap.isNull() or not pixmap.save(str(path), "BMP"):
            raise RuntimeError(f"Failed to publish {stem} snapshot for modal transition")
        return QUrl.fromLocalFile(str(path))

    def _configure_quick_item(self, base: QPixmap, panel: QPixmap) -> None:
        target = self.details._drawer_rect()  # noqa: SLF001 - final modal geometry
        self._base_snapshot = base
        self._base_url = self._publish_pixmap("modal_base", base)
        panel_url = self._publish_pixmap("modal_panel", panel)
        self.transition_item.setProperty("baseUrl", self._base_url)
        self.transition_item.setProperty("panelUrl", panel_url)
        self.transition_item.setProperty("panelX", float(target.x()))
        self.transition_item.setProperty("panelY", float(target.y()))
        self.transition_item.setProperty("panelW", float(target.width()))
        self.transition_item.setProperty("panelH", float(target.height()))

    def _issue_transition(self, *, closing: bool) -> None:
        self._command += 1
        self.transition_item.setProperty("active", True)
        self.transition_item.setProperty("closingRequest", closing)
        self.transition_item.setProperty("command", self._command)

    def _start_open_transition(self) -> None:
        if self._transitioning:
            return
        base = self.details.backdrop.pixmap()
        if base.isNull():
            base = self._capture_raw_backdrop()
        panel = self.details.drawer.grab()
        self._configure_quick_item(base, panel)

        self._transitioning = True
        self._target_open = True
        self._static_blur_ready = False
        self._issue_transition(closing=False)

        # Hide the complete QWidget child HWND. The Quick owner now displays the
        # exact pre-modal snapshot, so there is no layout or paint work left in
        # the animation hot path.
        self.window.hide()
        QTimer.singleShot(24, self._prepare_static_blur)

    def _prepare_static_blur(self) -> None:
        if self._base_snapshot.isNull() or not self._target_open:
            return
        blurred = self.details._blur_pixmap(self._base_snapshot)  # noqa: SLF001
        if not blurred.isNull():
            self.details.backdrop.setPixmap(blurred)
            self._static_blur_ready = True

    def request_close(self, *_args: object) -> None:
        if self._transitioning:
            return
        if not self.details.drawer.isVisible() and not self.details.scrim.isVisible():
            return

        panel = self.details.drawer.grab()
        if not self._base_url.isValid():
            base = self._capture_raw_backdrop()
            self._configure_quick_item(base, panel)
        else:
            target = self.details._drawer_rect()  # noqa: SLF001
            panel_url = self._publish_pixmap("modal_panel_close", panel)
            self.transition_item.setProperty("baseUrl", self._base_url)
            self.transition_item.setProperty("panelUrl", panel_url)
            self.transition_item.setProperty("panelX", float(target.x()))
            self.transition_item.setProperty("panelY", float(target.y()))
            self.transition_item.setProperty("panelW", float(target.width()))
            self.transition_item.setProperty("panelH", float(target.height()))

        self._transitioning = True
        self._target_open = False
        self._issue_transition(closing=True)
        self.window.hide()

    def _restore_widget_layer(self) -> None:
        self.window.show()
        shell = getattr(self.window, "_native_window_shell", None)
        fit = getattr(shell, "_fit_native_child", None)
        if callable(fit):
            QTimer.singleShot(0, fit)
        self.transition_item.setProperty("active", False)

    def _on_transition_finished(self, opened: bool) -> None:
        if opened:
            if not self._static_blur_ready:
                self._prepare_static_blur()
            self._restore_widget_layer()
            self.details.close_button.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            # Hide all final-state modal widgets while the QWidget child is still
            # invisible, then reveal the normal application atomically.
            self.details.close()
            self._restore_widget_layer()
            self._base_snapshot = QPixmap()
            self._base_url = QUrl()
            self._static_blur_ready = False

        self._transitioning = False
        self._target_open = bool(opened)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()

        if watched is self.details.drawer and event_type == QEvent.Type.Show:
            # FastCardDetailController has already populated the final layout and
            # assigned a raw backdrop, but updates are still suppressed here.
            self._start_open_transition()
            return False

        if watched is self.root and event_type == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            if event.key() == Qt.Key.Key_Escape and self.details.drawer.isVisible():
                self.request_close()
                return True

        return False

    def cleanup(self) -> None:
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
            self.transition_item.setProperty("active", False)
            self.transition_item.setParentItem(None)
            self.transition_item.deleteLater()


def install_modal_interaction(
    window: QMainWindow,
    details: FastCardDetailController,
) -> GlassModalInteractionController:
    controller = GlassModalInteractionController(window, details)
    window._glass_modal_interaction = controller  # type: ignore[attr-defined]
    return controller
