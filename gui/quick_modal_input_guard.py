from __future__ import annotations

from PySide6.QtCore import QObject, Property, QUrl, Signal
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
    z: 20000

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
    """Give an active Quick modal exclusive pointer ownership over the main scene.

    The main scene and detail modal are siblings in one QQuickWindow. Pointer
    handlers may both observe the same physical sequence unless an intermediate
    owner consumes any event that the modal does not keep. This transparent guard
    lives between the main scene (z=10000) and modal (z=25000), so modal controls
    remain fully interactive while no click/release can fall through to the main
    workspace during open, visible, or closing states.
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

    def cleanup(self) -> None:
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
