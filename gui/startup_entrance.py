from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtQml import QQmlComponent
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtWidgets import QMainWindow, QWidget

from .native_background import _OVERSCAN


_CAPTURE_DELAY_MS = 48
_UI_FADE_MS = 620
_CURTAIN_DELAY_MS = 0
_CURTAIN_MS = 500
_BACKGROUND_DELAY_MS = 120
_BACKGROUND_MS = 760
_UI_SCALE_DELAY_MS = 0
_UI_SCALE_MS = 0
_TOTAL_MS = 1000

_BG_START_SCALE = 1.16
_UI_START_SCALE = 1.0
_BG_START_DIM = 0.46
_CURTAIN_FRACTION = 0.51
_CURTAIN_COLOR = "#333333"
_LIVE_REVEAL_DELAY_MS = 160
_STARTUP_QML_URL = QUrl("inmemory:/StartupEntrance.qml")


_STARTUP_QML = r'''
import QtQuick

Item {
    id: startupRoot
    objectName: "startupEntranceOverlay"
    anchors.fill: parent
    visible: true
    enabled: true
    z: 40000

    property url sharpUrl
    property url blurUrl
    property bool revealStarted: false
    property real curtainProgress: 0.0
    property real backgroundScale: __BG_START_SCALE__
    property real blurMix: 1.0
    property real dimOpacity: __BG_START_DIM__
    property real coverOpacity: 1.0
    signal finished()

    Item {
        id: backgroundCover
        anchors.fill: parent
        opacity: startupRoot.coverOpacity

        Item {
            id: backgroundScene
            anchors.centerIn: parent
            width: startupRoot.width * __OVERSCAN__
            height: startupRoot.height * __OVERSCAN__
            scale: startupRoot.backgroundScale
            transformOrigin: Item.Center

            Image {
                anchors.fill: parent
                source: startupRoot.sharpUrl
                fillMode: Image.PreserveAspectCrop
                smooth: true
                cache: true
                asynchronous: false
            }

            Image {
                anchors.fill: parent
                source: startupRoot.blurUrl
                fillMode: Image.PreserveAspectCrop
                smooth: true
                cache: true
                asynchronous: false
                opacity: startupRoot.blurMix
            }
        }

        Rectangle {
            anchors.fill: parent
            color: "black"
            opacity: startupRoot.dimOpacity
        }
    }

    Rectangle {
        width: Math.ceil(startupRoot.width * __CURTAIN_FRACTION__) + 1
        height: startupRoot.height
        x: -width * startupRoot.curtainProgress
        color: "__CURTAIN_COLOR__"
    }

    Rectangle {
        width: Math.ceil(startupRoot.width * __CURTAIN_FRACTION__) + 1
        height: startupRoot.height
        x: startupRoot.width - width + width * startupRoot.curtainProgress
        color: "__CURTAIN_COLOR__"
    }

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.AllButtons
        hoverEnabled: true
        z: 10
    }

    ParallelAnimation {
        id: revealTimeline

        SequentialAnimation {
            PauseAnimation { duration: __CURTAIN_DELAY_MS__ }
            NumberAnimation {
                target: startupRoot
                property: "curtainProgress"
                from: 0.0
                to: 1.0
                duration: __CURTAIN_MS__
                easing.type: Easing.BezierSpline
                easing.bezierCurve: [0.645, 0.045, 0.355, 1.0, 1.0, 1.0]
            }
        }

        SequentialAnimation {
            PauseAnimation { duration: __BACKGROUND_DELAY_MS__ }
            ParallelAnimation {
                NumberAnimation {
                    target: startupRoot
                    property: "backgroundScale"
                    from: __BG_START_SCALE__
                    to: 1.0
                    duration: __BACKGROUND_MS__
                    easing.type: Easing.BezierSpline
                    easing.bezierCurve: [0.25, 0.46, 0.45, 0.94, 1.0, 1.0]
                }
                NumberAnimation {
                    target: startupRoot
                    property: "blurMix"
                    from: 1.0
                    to: 0.0
                    duration: __BACKGROUND_MS__
                    easing.type: Easing.BezierSpline
                    easing.bezierCurve: [0.25, 0.46, 0.45, 0.94, 1.0, 1.0]
                }
                NumberAnimation {
                    target: startupRoot
                    property: "dimOpacity"
                    from: __BG_START_DIM__
                    to: 0.0
                    duration: __BACKGROUND_MS__
                    easing.type: Easing.BezierSpline
                    easing.bezierCurve: [0.25, 0.46, 0.45, 0.94, 1.0, 1.0]
                }
            }
        }

        SequentialAnimation {
            PauseAnimation { duration: __LIVE_REVEAL_DELAY_MS__ }
            NumberAnimation {
                target: startupRoot
                property: "coverOpacity"
                from: 1.0
                to: 0.0
                duration: __UI_FADE_MS__
                easing.type: Easing.OutCubic
            }
        }

        SequentialAnimation {
            PauseAnimation { duration: __TOTAL_MS__ }
            ScriptAction { script: startupRoot.finished() }
        }
    }

    onRevealStartedChanged: {
        if (revealStarted && !revealTimeline.running)
            revealTimeline.start()
    }
}
'''

_STARTUP_QML = (
    _STARTUP_QML
    .replace("__OVERSCAN__", f"{float(_OVERSCAN):.9f}")
    .replace("__BG_START_SCALE__", f"{_BG_START_SCALE:.9f}")
    .replace("__BG_START_DIM__", f"{_BG_START_DIM:.9f}")
    .replace("__CURTAIN_FRACTION__", f"{_CURTAIN_FRACTION:.9f}")
    .replace("__CURTAIN_COLOR__", _CURTAIN_COLOR)
    .replace("__CURTAIN_DELAY_MS__", str(_CURTAIN_DELAY_MS))
    .replace("__CURTAIN_MS__", str(_CURTAIN_MS))
    .replace("__BACKGROUND_DELAY_MS__", str(_BACKGROUND_DELAY_MS))
    .replace("__BACKGROUND_MS__", str(_BACKGROUND_MS))
    .replace("__LIVE_REVEAL_DELAY_MS__", str(_LIVE_REVEAL_DELAY_MS))
    .replace("__UI_FADE_MS__", str(_UI_FADE_MS))
    .replace("__TOTAL_MS__", str(_TOTAL_MS))
)


class _StartupEntranceOverlay(QObject):
    """GPU-owned startup cover attached to the application's existing QQuickWindow."""

    finished = Signal()

    def __init__(self, window: QMainWindow, visual: Any, parent: QObject) -> None:
        super().__init__(parent)
        self.window = window
        self.background = getattr(visual, "background", None)
        self.quick = getattr(self.background, "quick_window", None)
        self.engine = getattr(self.background, "engine", None)
        self._component: QQmlComponent | None = None
        self._item: QQuickItem | None = None
        self._reveal_started = False

        if not isinstance(self.quick, QQuickWindow) or self.engine is None:
            raise RuntimeError("startup entrance requires the shared QQuickWindow")
        self._create_item()

    def _create_item(self) -> None:
        component = QQmlComponent(self.engine, self)
        self._component = component
        component.setData(_STARTUP_QML.encode("utf-8"), _STARTUP_QML_URL)
        if component.status() == QQmlComponent.Status.Error:
            errors = "\n".join(error.toString() for error in component.errors())
            raise RuntimeError("Startup entrance QML failed: " + errors)
        if component.status() != QQmlComponent.Status.Ready:
            raise RuntimeError(f"Startup entrance QML is not ready: {component.status()}")

        created = component.create(self.engine.rootContext())
        if not isinstance(created, QQuickItem):
            if created is not None:
                created.deleteLater()
            raise RuntimeError("Startup entrance QML did not create a QQuickItem")

        created.setParent(self)
        created.setParentItem(self.quick.contentItem())
        created.setProperty(
            "sharpUrl",
            QUrl.fromLocalFile(str(getattr(self.background, "_sharp_path", ""))),
        )
        created.setProperty(
            "blurUrl",
            QUrl.fromLocalFile(str(getattr(self.background, "_blur_path", ""))),
        )
        created.setZ(40000.0)
        try:
            created.finished.connect(self._on_qml_finished)
        except (AttributeError, RuntimeError, TypeError) as exc:
            created.setParentItem(None)
            created.deleteLater()
            raise RuntimeError("Startup entrance QML has no finish signal") from exc
        self._item = created

    @Slot()
    def _on_qml_finished(self) -> None:
        self.finished.emit()

    def begin(self) -> None:
        item = self._item
        if item is None:
            return
        try:
            item.setVisible(True)
            item.setZ(40000.0)
            self.quick.requestUpdate()
        except RuntimeError:
            pass

    def begin_reveal(self) -> None:
        if self._reveal_started:
            return
        self._reveal_started = True
        item = self._item
        if item is None:
            self.finished.emit()
            return
        try:
            item.setProperty("revealStarted", True)
            self.quick.requestUpdate()
        except RuntimeError:
            self.finished.emit()

    def raise_overlay(self) -> None:
        item = self._item
        if item is None:
            return
        try:
            item.setVisible(True)
            item.setZ(40000.0)
            self.quick.requestUpdate()
        except RuntimeError:
            pass

    def resize_to_window(self) -> None:
        # anchors.fill keeps the Quick surface fitted by the Scene Graph itself.
        try:
            self.quick.requestUpdate()
        except RuntimeError:
            pass

    def release(self) -> None:
        item = self._item
        self._item = None
        if item is not None:
            try:
                item.finished.disconnect(self._on_qml_finished)
            except (AttributeError, RuntimeError, TypeError):
                pass
            try:
                item.setVisible(False)
                item.setParentItem(None)
                item.deleteLater()
            except RuntimeError:
                pass
        component = self._component
        self._component = None
        if component is not None:
            try:
                component.deleteLater()
            except RuntimeError:
                pass


class StartupEntranceController(QObject):
    """Reveal the settled Quick workspace with one Scene Graph animation owner."""

    def __init__(self, window: QMainWindow, visual: Any) -> None:
        super().__init__(window)
        self.window = window
        self.visual = visual
        self.background = getattr(visual, "background", None)
        self.quick = getattr(self.background, "quick_window", None)
        self.overlay = _StartupEntranceOverlay(window, visual, self)
        self._started = False
        self._finished = False
        self._card_fx_was_suspended = False
        self._hidden_effects: QWidget | None = None

        self.overlay.finished.connect(self._finish)
        window.destroyed.connect(self.cleanup)
        self._freeze_runtime_presentation()

    def _freeze_runtime_presentation(self) -> None:
        clock = getattr(self.window, "_presentation_clock", None)
        suspend_clock = getattr(clock, "suspend", None)
        if callable(suspend_clock):
            suspend_clock("startup")

        if self.quick is not None:
            try:
                self.quick.setProperty("animationRunning", False)
                self.quick.setProperty("pointerX", 0.0)
                self.quick.setProperty("pointerY", 0.0)
                self.quick.setProperty("offsetX", 0.0)
                self.quick.setProperty("offsetY", 0.0)
            except RuntimeError:
                pass

        card_fx = getattr(self.window, "_nekro_card_fx", None)
        suspend_cards = getattr(card_fx, "suspend_for_modal", None)
        if callable(suspend_cards):
            try:
                self._card_fx_was_suspended = bool(getattr(card_fx, "_suspended", False))
                if not self._card_fx_was_suspended:
                    suspend_cards()
            except RuntimeError:
                pass

        effects = getattr(self.window, "_nekro_effects", None)
        if isinstance(effects, QWidget) and effects.isVisible():
            self._hidden_effects = effects
            effects.hide()

    def raise_overlay(self) -> None:
        if not self._finished:
            self.overlay.raise_overlay()

    def start(self) -> None:
        if self._started or self._finished:
            return
        self._started = True
        self.overlay.begin()
        self.raise_overlay()
        QTimer.singleShot(_CAPTURE_DELAY_MS, self._capture_and_reveal)

    def _capture_and_reveal(self) -> None:
        """Compatibility boundary: the reveal is now entirely Scene Graph owned."""

        if self._finished:
            return
        self.overlay.begin_reveal()

    def _quick_presentation_active(self) -> bool:
        view = getattr(self.window, "_static_qml_view_controller", None)
        return bool(getattr(view, "static_active", False))

    def _restore_runtime_presentation(self) -> None:
        quick_active = self._quick_presentation_active()
        if self._hidden_effects is not None:
            if not quick_active:
                try:
                    self._hidden_effects.show()
                    self._hidden_effects.raise_()
                except RuntimeError:
                    pass
            self._hidden_effects = None

        if not quick_active:
            card_fx = getattr(self.window, "_nekro_card_fx", None)
            resume_cards = getattr(card_fx, "resume_from_modal", None)
            if callable(resume_cards) and not self._card_fx_was_suspended:
                try:
                    resume_cards()
                except RuntimeError:
                    pass

        clock = getattr(self.window, "_presentation_clock", None)
        resume_clock = getattr(clock, "resume", None)
        if callable(resume_clock):
            resume_clock("startup")

    def release_overlay(self) -> None:
        self.overlay.release()

    def _finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        self.release_overlay()
        self._restore_runtime_presentation()
        assistant = getattr(self.window, "_runtime_assistant", None)
        if isinstance(assistant, QWidget):
            try:
                assistant.raise_()
            except RuntimeError:
                pass

    def cleanup(self) -> None:
        self.overlay.release()


def install_startup_entrance(
    window: QMainWindow,
    visual: Any,
) -> StartupEntranceController:
    existing = getattr(window, "_startup_entrance", None)
    if isinstance(existing, StartupEntranceController):
        return existing
    controller = StartupEntranceController(window, visual)
    window._startup_entrance = controller  # type: ignore[attr-defined]
    return controller


__all__ = ["StartupEntranceController", "install_startup_entrance"]
