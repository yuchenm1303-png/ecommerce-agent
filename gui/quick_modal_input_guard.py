from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Property, QUrl, Signal
from PySide6.QtGui import QWheelEvent
from PySide6.QtQml import QQmlComponent
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtWidgets import QWidget

from .quick_modal_layer import QuickModalLayerController
from .static_qml_view import StaticQmlViewController


_GUARD_QML_URL = QUrl("inmemory:/QuickModalInputGuard.qml")
_GUARD_QML = r'''
import QtQuick

Item {
    id: guardRoot
    objectName: "quickModalInputGuard"
    anchors.fill: parent
    visible: modalInputGuard.active
    enabled: visible
    // Keep this immediately below the detail modal (z=25000).  The main Quick
    // scene contains nested overlays with their own z values, so a low guard z
    // can still leave an interactive main-scene item above the blocker.
    z: 24990

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.AllButtons
        hoverEnabled: true
        preventStealing: true
        propagateComposedEvents: false
        onPressed: mouse.accepted = true
        onReleased: mouse.accepted = true
        onClicked: mouse.accepted = true
        onDoubleClicked: mouse.accepted = true
    }
}
'''


class QuickModalInputGuard(QObject):
    """Give an active Quick modal exclusive input ownership over the main scene.

    The main scene and detail modal are siblings in one QQuickWindow. Pointer
    handlers may both observe the same physical sequence unless an intermediate
    owner consumes any event that the modal does not keep. This transparent guard
    lives immediately below the modal (z=25000), so modal controls remain fully
    interactive while no click/release can fall through to the main workspace.

    The visible modal is a QML mirror of an off-screen QWidget detail drawer. The
    QWidget side already owns the canonical QScrollArea, therefore wheel/touchpad
    input is intercepted at the QQuickWindow and forwarded to that scroll bar.
    Re-snapshotting the controls after the scroll keeps the QML mirror in sync and
    lets long settings panels expose their complete lower content.
    """

    activeChanged = Signal()

    def __init__(
        self,
        window: QWidget,
        static_view: StaticQmlViewController,
        modal: QuickModalLayerController,
    ) -> None:
        super().__init__(window)
        self.window = window
        self.static_view = static_view
        self.modal = modal
        self.quick = static_view.quick
        self.engine = static_view.engine
        self._active = bool(getattr(modal, "_presented", False))
        self._component: QQmlComponent | None = None
        self._item: QQuickItem | None = None

        if not isinstance(self.quick, QQuickWindow):
            raise RuntimeError("Quick modal input guard requires the unified QQuickWindow")

        self._create_guard()
        # Catch wheel input before QML dispatch.  The QML detail surface is only a
        # mirror, while the real scroll state lives in FastCardDetailController.
        self.quick.installEventFilter(self)
        self.modal.changed.connect(self._sync_active)
        window.destroyed.connect(self.cleanup)

    def _get_active(self) -> bool:
        return self._active

    active = Property(bool, _get_active, notify=activeChanged)

    def _create_guard(self) -> None:
        context = self.engine.rootContext()
        context.setContextProperty("modalInputGuard", self)
        component = QQmlComponent(self.engine, self)
        self._component = component
        component.setData(_GUARD_QML.encode("utf-8"), _GUARD_QML_URL)
        if component.status() == QQmlComponent.Status.Error:
            raise RuntimeError(
                "Quick modal input guard QML failed: "
                + "\n".join(error.toString() for error in component.errors())
            )
        if component.status() != QQmlComponent.Status.Ready:
            raise RuntimeError("Quick modal input guard QML did not become ready")
        created = component.create(context)
        if not isinstance(created, QQuickItem):
            if created is not None:
                created.deleteLater()
            raise RuntimeError("Quick modal input guard did not create a QQuickItem")
        created.setParent(self)
        created.setParentItem(self.quick.contentItem())
        self._item = created

    def _sync_active(self) -> None:
        active = bool(getattr(self.modal, "_presented", False))
        if active == self._active:
            return
        self._active = active
        self.activeChanged.emit()

    def _point_inside_modal(self, x: float, y: float) -> bool:
        modal_x = int(getattr(self.modal, "_modal_x", 0))
        modal_y = int(getattr(self.modal, "_modal_y", 0))
        modal_w = int(getattr(self.modal, "_modal_w", 0))
        modal_h = int(getattr(self.modal, "_modal_h", 0))
        return (
            modal_w > 0
            and modal_h > 0
            and modal_x <= x < modal_x + modal_w
            and modal_y <= y < modal_y + modal_h
        )

    def _scroll_modal(self, event: QWheelEvent) -> None:
        position = event.position()
        if not self._point_inside_modal(position.x(), position.y()):
            return

        details = getattr(self.modal, "details", None)
        scroll = getattr(details, "scroll", None)
        if scroll is None:
            return
        try:
            bar = scroll.verticalScrollBar()
        except RuntimeError:
            return

        pixel_y = int(event.pixelDelta().y())
        if pixel_y:
            delta = -pixel_y
        else:
            angle_y = int(event.angleDelta().y())
            if not angle_y:
                return
            # One traditional wheel notch is 120 units. Keep a useful minimum
            # distance while respecting a larger widget-configured single step.
            per_notch = max(48, int(bar.singleStep()) * 3)
            delta = -int(round((angle_y / 120.0) * per_notch))

        if not delta:
            return
        current = int(bar.value())
        target = max(int(bar.minimum()), min(int(bar.maximum()), current + delta))
        if target == current:
            return
        bar.setValue(target)
        try:
            self.modal._refresh_controls(force=True)  # noqa: SLF001
        except RuntimeError:
            return

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is not self.quick or not self._active:
            return False
        if event.type() == QEvent.Type.Wheel and isinstance(event, QWheelEvent):
            # While a modal is presented the wheel belongs to that modal, even
            # over its backdrop. Never let the main workspace scroll underneath.
            self._scroll_modal(event)
            return True
        return False

    def cleanup(self) -> None:
        try:
            self.quick.removeEventFilter(self)
        except RuntimeError:
            pass
        try:
            self.modal.changed.disconnect(self._sync_active)
        except (RuntimeError, TypeError):
            pass
        item = self._item
        self._item = None
        if item is not None:
            try:
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


def install_quick_modal_input_guard(
    window: QWidget,
    static_view: StaticQmlViewController,
    modal: QuickModalLayerController,
) -> QuickModalInputGuard:
    existing = getattr(window, "_quick_modal_input_guard", None)
    if isinstance(existing, QuickModalInputGuard):
        return existing
    controller = QuickModalInputGuard(window, static_view, modal)
    window._quick_modal_input_guard = controller  # type: ignore[attr-defined]
    return controller


__all__ = ["QuickModalInputGuard", "install_quick_modal_input_guard"]
