from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, QObject, QPoint, Property, QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtQml import QQmlComponent
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtWidgets import (
    QAbstractButton,
    QCheckBox,
    QComboBox,
    QFrame,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QWidget,
)

from .card_details_fast import FastCardDetailController
from .static_qml_view import StaticQmlViewController


_OPEN_MS = 260
_CLOSE_MS = 210
_DRAWER_TRAVEL_PX = 14
_MODAL_QML_URL = QUrl("inmemory:/QuickDetailModal.qml")
_INTERACTIVE_WIDGETS = (
    QAbstractButton,
    QLineEdit,
    QSpinBox,
    QComboBox,
    QCheckBox,
    QPlainTextEdit,
    QTableWidget,
    QTabWidget,
)


_QUICK_MODAL_QML = r'''
import QtQuick
import QtQuick.Controls
import QtQuick.Effects

Item {
    id: modalRoot
    objectName: "quickDetailModalRoot"
    anchors.fill: parent
    visible: quickModal.presented
    enabled: visible
    z: 25000
    opacity: quickModal.visible ? 1.0 : 0.0

    Behavior on opacity {
        NumberAnimation {
            duration: quickModal.visible ? 260 : 210
            easing.type: quickModal.visible ? Easing.OutCubic : Easing.InOutCubic
        }
    }

    function componentFor(kind) {
        if (kind === "label") return labelComponent
        if (kind === "badge") return badgeComponent
        if (kind === "button") return buttonComponent
        if (kind === "lineedit") return lineEditComponent
        if (kind === "spinbox") return spinBoxComponent
        if (kind === "checkbox") return checkboxComponent
        if (kind === "combo") return comboComponent
        if (kind === "textedit") return textEditComponent
        if (kind === "table") return tableComponent
        if (kind === "tabs") return tabsComponent
        if (kind === "panel") return panelComponent
        return labelComponent
    }

    function buttonFill(style, hovered, pressed, enabled) {
        if (!enabled) return Qt.rgba(0, 0, 0, 34/255)
        if (style === "primary")
            return Qt.rgba(1, 1, 1, (hovered && !pressed ? 70 : 48)/255)
        if (style === "danger")
            return Qt.rgba((hovered && !pressed ? 92 : 70)/255, 0, (hovered && !pressed ? 24 : 18)/255, (hovered && !pressed ? 118 : 86)/255)
        if (pressed) return Qt.rgba(0, 0, 0, 78/255)
        if (hovered) return Qt.rgba(0, 0, 0, 98/255)
        return Qt.rgba(0, 0, 0, 68/255)
    }

    function fieldFill(hovered, focused) {
        if (focused) return Qt.rgba(0, 0, 0, 96/255)
        if (hovered) return Qt.rgba(0, 0, 0, 86/255)
        return Qt.rgba(0, 0, 0, 72/255)
    }

    function fieldBorder(hovered, focused) {
        if (focused) return Qt.rgba(1, 1, 1, 90/255)
        if (hovered) return Qt.rgba(1, 1, 1, 44/255)
        return Qt.rgba(1, 1, 1, 26/255)
    }

    ShaderEffectSource {
        id: workspaceSnapshot
        anchors.fill: parent
        sourceItem: quickMainItem
        hideSource: false
        live: false
        smooth: true
        visible: false
        Component.onCompleted: scheduleUpdate()
    }

    MultiEffect {
        anchors.fill: parent
        source: workspaceSnapshot
        blurEnabled: true
        blur: 0.52
        blurMax: 32
        autoPaddingEnabled: false
    }

    Rectangle {
        anchors.fill: parent
        color: Qt.rgba(12/255, 17/255, 26/255, 122/255)
        MouseArea {
            anchors.fill: parent
            onClicked: quickModal.closeModal()
        }
    }

    Item {
        id: drawerLayer
        x: quickModal.modalX
        y: quickModal.modalY
        width: quickModal.modalW
        height: quickModal.modalH
        clip: true
        transform: Translate {
            y: quickModal.visible ? 0 : 14
            Behavior on y {
                NumberAnimation {
                    duration: quickModal.visible ? 260 : 210
                    easing.type: quickModal.visible ? Easing.OutCubic : Easing.InOutCubic
                }
            }
        }

        Rectangle {
            anchors.fill: parent
            radius: 14
            color: Qt.rgba(220/255, 228/255, 238/255, 188/255)
            border.width: 1
            border.color: Qt.rgba(1, 1, 1, 72/255)
        }

        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.AllButtons
            onClicked: mouse.accepted = false
        }

        Repeater {
            model: quickModal.controls
            delegate: Loader {
                required property var modelData
                property var d: modelData
                x: d.x; y: d.y; width: d.w; height: d.h
                sourceComponent: modalRoot.componentFor(d.kind)
                onLoaded: if (item) item.d = d
            }
        }
    }

    Connections {
        target: quickModal
        function onChanged() {
            if (quickModal.presented)
                workspaceSnapshot.scheduleUpdate()
        }
    }

    Component {
        id: labelComponent
        Item {
            property var d
            Text {
                anchors.fill: parent
                text: d ? d.text : ""
                color: d && d.color ? d.color : "white"
                font.family: d && d.fontFamily ? d.fontFamily : "Microsoft YaHei UI"
                font.pixelSize: d && d.fontSize ? d.fontSize : 13
                font.weight: d && d.fontWeight ? d.fontWeight : Font.Normal
                wrapMode: d && d.wordWrap ? Text.WordWrap : Text.NoWrap
                elide: d && d.wordWrap ? Text.ElideNone : Text.ElideRight
                verticalAlignment: Text.AlignVCenter
                horizontalAlignment: d && d.align === "center" ? Text.AlignHCenter : d && d.align === "right" ? Text.AlignRight : Text.AlignLeft
                textFormat: d && d.rich ? Text.RichText : Text.PlainText
            }
        }
    }

    Component {
        id: badgeComponent
        Rectangle {
            property var d
            radius: 7
            color: Qt.rgba(0, 0, 0, 72/255)
            border.width: 1
            border.color: Qt.rgba(1, 1, 1, 20/255)
            Text {
                anchors.fill: parent
                anchors.leftMargin: 12
                anchors.rightMargin: 12
                text: d ? d.text : ""
                color: d && d.color ? d.color : Qt.rgba(1, 1, 1, 225/255)
                font.pixelSize: d && d.fontSize ? d.fontSize : 11
                font.weight: Font.DemiBold
                verticalAlignment: Text.AlignVCenter
                horizontalAlignment: Text.AlignHCenter
                elide: Text.ElideRight
            }
        }
    }

    Component {
        id: panelComponent
        Rectangle {
            property var d
            radius: d && d.name === "cardDetailSection" ? 10 : 8
            color: d && d.name === "cardDetailSection" ? Qt.rgba(1,1,1,22/255) : (d && d.fill ? d.fill : "transparent")
            border.width: d && d.name === "cardDetailSection" ? 1 : 0
            border.color: Qt.rgba(1,1,1,24/255)
        }
    }

    Component {
        id: buttonComponent
        Item {
            property var d
            HoverHandler { id: hover }
            TapHandler {
                id: tap
                enabled: d ? d.enabled : false
                acceptedButtons: Qt.LeftButton
                onTapped: if (d) quickModal.click(d.key)
            }
            Rectangle {
                anchors.fill: parent
                radius: d && d.name === "cardDetailClose" ? 9 : 8
                color: modalRoot.buttonFill(d ? d.style : "default", hover.hovered, tap.pressed, d ? d.enabled : false)
                border.width: 1
                border.color: Qt.rgba(1,1,1, d && d.name === "cardDetailClose" ? 26/255 : 28/255)
            }
            Text {
                anchors.fill: parent
                anchors.leftMargin: 10; anchors.rightMargin: 10
                text: d ? d.text : ""
                color: d && d.enabled ? "white" : Qt.rgba(1,1,1,76/255)
                font.pixelSize: d && d.name === "cardDetailClose" ? 18 : (d && d.fontSize ? d.fontSize : 13)
                font.weight: Font.DemiBold
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
            }
        }
    }

    Component {
        id: lineEditComponent
        Item {
            property var d
            HoverHandler { id: hover }
            Rectangle {
                anchors.fill: parent; radius: 8
                color: modalRoot.fieldFill(hover.hovered, editor.activeFocus)
                border.width: 1; border.color: modalRoot.fieldBorder(hover.hovered, editor.activeFocus)
            }
            TextInput {
                id: editor
                anchors.fill: parent; leftPadding: 11; rightPadding: 11
                text: d ? d.text : ""; readOnly: d ? d.readOnly : true
                color: "white"; verticalAlignment: TextInput.AlignVCenter; clip: true
                font.pixelSize: d && d.fontSize ? d.fontSize : 13
                onEditingFinished: if (d && !d.readOnly) quickModal.setText(d.key, text)
            }
        }
    }

    Component {
        id: spinBoxComponent
        Item {
            property var d
            Rectangle {
                anchors.fill: parent; radius: 8
                color: Qt.rgba(13/255,22/255,34/255,66/255)
                border.width: 1; border.color: Qt.rgba(1,1,1,20/255)
            }
            TextInput {
                id: editor
                anchors.fill: parent; leftPadding: 11; rightPadding: 11
                text: d ? String(d.value) : "0"; color: "white"
                verticalAlignment: TextInput.AlignVCenter
                validator: IntValidator { bottom: d ? d.minimum : -2147483647; top: d ? d.maximum : 2147483647 }
                onEditingFinished: if (d) quickModal.setValue(d.key, Number(text))
            }
        }
    }

    Component {
        id: checkboxComponent
        Item {
            property var d
            TapHandler {
                acceptedButtons: Qt.LeftButton
                enabled: d ? d.enabled : false
                onTapped: if (d) quickModal.setChecked(d.key, !d.checked)
            }
            Rectangle {
                width: 15; height: 15; x: 0; y: (parent.height - height) / 2; radius: 4
                color: d && d.checked ? Qt.rgba(1,1,1,118/255) : Qt.rgba(0,0,0,72/255)
                border.width: 1
                border.color: d && d.checked ? Qt.rgba(1,1,1,188/255) : Qt.rgba(1,1,1,62/255)
            }
            Text {
                x: 22; width: Math.max(0, parent.width - 22); height: parent.height
                text: d ? d.text : ""; color: Qt.rgba(1,1,1,220/255)
                font.pixelSize: d && d.fontSize ? d.fontSize : 11
                verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
            }
        }
    }

    Component {
        id: comboComponent
        ComboBox {
            id: combo
            property var d
            hoverEnabled: true
            enabled: d ? d.enabled : false
            model: d ? d.items : []
            currentIndex: d ? d.currentIndex : -1
            onActivated: if (d) quickModal.setComboIndex(d.key, currentIndex)
            background: Rectangle {
                radius: 8
                color: Qt.rgba(13/255,22/255,34/255,66/255)
                border.width: 1; border.color: Qt.rgba(1,1,1,20/255)
            }
            contentItem: Text {
                leftPadding: 11; rightPadding: 30
                text: combo.displayText; color: "white"
                font.pixelSize: combo.d && combo.d.fontSize ? combo.d.fontSize : 13
                verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
            }
            indicator: Text {
                x: combo.width - width - 10; y: (combo.height - height) / 2
                text: "▾"; color: Qt.rgba(1,1,1,180/255); font.pixelSize: 11
            }
        }
    }

    Component {
        id: textEditComponent
        Item {
            property var d
            Rectangle {
                anchors.fill: parent; radius: 8
                color: Qt.rgba(15/255,23/255,34/255,66/255)
                border.width: 1; border.color: Qt.rgba(1,1,1,14/255)
            }
            TextArea {
                anchors.fill: parent; leftPadding: 9; rightPadding: 9; topPadding: 9; bottomPadding: 9
                text: d ? d.text : ""; readOnly: d ? d.readOnly : true
                wrapMode: d && d.wrap ? TextEdit.Wrap : TextEdit.NoWrap
                color: Qt.rgba(1,1,1,224/255); background: null
                font.family: "Cascadia Mono"; font.pixelSize: d && d.fontSize ? d.fontSize : 10
                onActiveFocusChanged: if (!activeFocus && d && !d.readOnly) quickModal.setText(d.key, text)
            }
        }
    }

    Component {
        id: tabsComponent
        Item {
            property var d
            Row {
                id: tabRow
                anchors.left: parent.left; anchors.top: parent.top
                height: d ? d.tabHeight : 30; spacing: 4
                Repeater {
                    model: d ? d.items : []
                    delegate: Rectangle {
                        required property string modelData
                        required property int index
                        width: d && d.tabWidths && d.tabWidths.length > index ? d.tabWidths[index] : 80
                        height: tabRow.height; radius: 7
                        color: index === d.currentIndex ? Qt.rgba(1,1,1,30/255) : Qt.rgba(1,1,1,12/255)
                        border.width: 1; border.color: index === d.currentIndex ? Qt.rgba(1,1,1,24/255) : Qt.rgba(1,1,1,10/255)
                        TapHandler { onTapped: if (d) quickModal.setTabIndex(d.key, index) }
                        Text {
                            anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12
                            text: modelData; color: index === d.currentIndex ? "white" : Qt.rgba(1,1,1,170/255)
                            font.pixelSize: 10; font.weight: Font.DemiBold
                            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
                        }
                    }
                }
            }
        }
    }

    Component {
        id: tableComponent
        Item {
            property var d
            Rectangle {
                anchors.fill: parent; radius: 8
                color: Qt.rgba(15/255,23/255,34/255,66/255)
                border.width: 1; border.color: Qt.rgba(1,1,1,14/255)
            }
            Flickable {
                anchors.fill: parent; clip: true
                contentWidth: headerRow.width
                contentHeight: (d ? d.headerHeight : 35) + rowsColumn.height
                Row {
                    id: headerRow
                    height: d ? d.headerHeight : 35
                    Repeater {
                        model: d ? d.headers : []
                        delegate: Rectangle {
                            required property string modelData
                            required property int index
                            width: d && d.columnWidths && d.columnWidths.length > index ? d.columnWidths[index] : 100
                            height: headerRow.height
                            color: Qt.rgba(1,1,1,22/255)
                            Text {
                                anchors.fill: parent; leftPadding: 9; rightPadding: 9
                                text: modelData; color: Qt.rgba(1,1,1,220/255)
                                font.pixelSize: 10; font.weight: Font.Bold
                                verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
                            }
                        }
                    }
                }
                Column {
                    id: rowsColumn
                    y: headerRow.height; width: headerRow.width
                    Repeater {
                        model: d ? d.rows : []
                        delegate: Item {
                            required property var modelData
                            required property int index
                            property var rowData: modelData
                            width: rowsColumn.width; height: d ? d.rowHeight : 35
                            Rectangle { anchors.fill: parent; color: index % 2 ? Qt.rgba(1,1,1,9/255) : "transparent" }
                            Row {
                                anchors.fill: parent
                                Repeater {
                                    model: d ? d.headers.length : 0
                                    delegate: Text {
                                        required property int index
                                        width: d && d.columnWidths && d.columnWidths.length > index ? d.columnWidths[index] : 100
                                        height: parent.height; leftPadding: 9; rightPadding: 9
                                        text: rowData && rowData.length > index ? String(rowData[index]) : ""
                                        color: Qt.rgba(1,1,1,224/255); font.pixelSize: 10
                                        verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
'''


class QuickModalLayerController(QObject):
    """Keep detail-card presentation inside the same QQuickWindow as the main UI."""

    changed = Signal()

    def __init__(
        self,
        window: QWidget,
        static_view: StaticQmlViewController,
        details: FastCardDetailController,
    ) -> None:
        super().__init__(window)
        self.window = window
        self.static_view = static_view
        self.details = details
        self.quick = static_view.quick
        self.engine = static_view.engine
        self.static_bridge = static_view.bridge

        if not isinstance(self.quick, QQuickWindow):
            raise RuntimeError("Quick modal layer requires the unified QQuickWindow")

        self._presented = False
        self._visible = False
        self._controls: list[dict[str, Any]] = []
        self._modal_x = 0
        self._modal_y = 0
        self._modal_w = 0
        self._modal_h = 0
        self._patched = False
        self._original_show_prepared_modal = self.details._show_prepared_modal  # noqa: SLF001
        self._component: QQmlComponent | None = None
        self._item: QQuickItem | None = None
        self._excluded_detail_cards = self._resolve_excluded_detail_cards()
        if self._excluded_detail_cards:
            self.details._expandable_cards = tuple(  # noqa: SLF001
                frame
                for frame in self.details._expandable_cards  # noqa: SLF001
                if frame not in self._excluded_detail_cards
            )

        self._close_timer = QTimer(self)
        self._close_timer.setSingleShot(True)
        self._close_timer.setInterval(_CLOSE_MS)
        self._close_timer.timeout.connect(self._finish_close)

        self._owner_timer = QTimer(self)
        self._owner_timer.setInterval(25)
        self._owner_timer.timeout.connect(self._sync_quick_owner)
        self._owner_timer.start()

        self.quick.installEventFilter(self)
        self.static_bridge.sceneChanged.connect(self._on_main_scene_changed)
        window.destroyed.connect(self.cleanup)

    def _get_presented(self) -> bool:
        return self._presented

    presented = Property(bool, _get_presented, notify=changed)

    def _get_visible(self) -> bool:
        return self._visible

    visible = Property(bool, _get_visible, notify=changed)

    def _get_controls(self):  # noqa: ANN201
        return self._controls

    controls = Property("QVariantList", _get_controls, notify=changed)

    modalX = Property(int, lambda self: self._modal_x, notify=changed)
    modalY = Property(int, lambda self: self._modal_y, notify=changed)
    modalW = Property(int, lambda self: self._modal_w, notify=changed)
    modalH = Property(int, lambda self: self._modal_h, notify=changed)

    def _resolve_excluded_detail_cards(self) -> set[QFrame]:
        """Cards that are control surfaces, not drill-down presentation cards."""

        workspace = getattr(self.window, "batch_workspace", None)
        editor = getattr(workspace, "_batch_url_editor", None)
        if not isinstance(editor, QWidget):
            return set()

        expandable = set(getattr(self.details, "_expandable_cards", ()))  # noqa: SLF001
        current: QWidget | None = editor
        while current is not None:
            if isinstance(current, QFrame) and current in expandable:
                return {current}
            current = current.parentWidget()
        return set()

    def _sync_quick_owner(self) -> None:
        if bool(getattr(self.static_view, "_load_failed", False)):
            self._owner_timer.stop()
            self._restore_detail_owner()
            return
        if self.static_view.item is None:
            return
        if self._item is None:
            self._create_overlay()
        if not self.static_view.static_active:
            return
        self._owner_timer.stop()
        self._install_detail_owner()

    def _create_overlay(self) -> None:
        main_item = self.static_view.item
        if main_item is None:
            return
        context = self.engine.rootContext()
        context.setContextProperty("quickModal", self)
        context.setContextProperty("quickMainItem", main_item)
        component = QQmlComponent(self.engine, self)
        self._component = component
        component.setData(_QUICK_MODAL_QML.encode("utf-8"), _MODAL_QML_URL)
        if component.status() == QQmlComponent.Status.Error:
            raise RuntimeError("Quick detail modal QML failed: " + "\n".join(e.toString() for e in component.errors()))
        if component.status() != QQmlComponent.Status.Ready:
            return
        created = component.create(context)
        if not isinstance(created, QQuickItem):
            if created is not None:
                created.deleteLater()
            raise RuntimeError("Quick detail modal did not create a QQuickItem")
        created.setParent(self)
        created.setParentItem(self.quick.contentItem())
        created.setWidth(float(max(1, self.quick.width())))
        created.setHeight(float(max(1, self.quick.height())))
        self._item = created

    def _install_detail_owner(self) -> None:
        if self._patched:
            return
        self._patched = True
        self.details._show_prepared_modal = self._show_quick_modal  # type: ignore[method-assign]  # noqa: SLF001

    def _restore_detail_owner(self) -> None:
        if not self._patched:
            return
        self._patched = False
        self.details._show_prepared_modal = self._original_show_prepared_modal  # type: ignore[method-assign]  # noqa: SLF001

    def _show_quick_modal(self, *, ratio: tuple[float, float]) -> None:
        if not self.static_view.static_active:
            self._original_show_prepared_modal(ratio=ratio)
            return
        self._close_timer.stop()
        self.details._modal_ratio = ratio  # noqa: SLF001
        self.details.scroll.verticalScrollBar().setValue(0)
        self.details.ghost.hide()
        self.details.backdrop.hide()
        self.details.scrim.hide()
        self.details.body_layout.activate()
        drawer_layout = self.details.drawer.layout()
        if drawer_layout is not None:
            drawer_layout.activate()
        rect = self.details._drawer_rect()  # noqa: SLF001
        self.details.drawer.setGeometry(rect)
        self.details.drawer.show()
        self.details.close_button.setEnabled(True)

        self._modal_x = int(rect.x())
        self._modal_y = int(rect.y())
        self._modal_w = int(rect.width())
        self._modal_h = int(rect.height())
        self._presented = True
        self._visible = False
        self._refresh_controls()
        self.changed.emit()
        QTimer.singleShot(0, self._finish_open)

    def _finish_open(self) -> None:
        if not self._presented:
            return
        self._visible = True
        self.changed.emit()

    def _refresh_controls(self) -> None:
        if not self._presented or self.details.drawer.isHidden():
            return
        controls: list[dict[str, Any]] = []
        try:
            descendants = self.details.drawer.findChildren(QWidget)
        except RuntimeError:
            descendants = []
        for widget in descendants:
            data = self.static_bridge._snapshot_widget(widget, self.details.drawer)  # noqa: SLF001
            if data is not None:
                controls.append(data)
        controls.sort(key=lambda item: (int(item["y"]), int(item["x"])))
        self._controls = controls
        self.changed.emit()

    def _on_main_scene_changed(self) -> None:
        if self._presented:
            self._refresh_controls()

    @staticmethod
    def _widget_contains_window_point(widget: QWidget, window: QWidget, point: QPoint) -> bool:
        try:
            top_left = widget.mapTo(window, QPoint(0, 0))
            return (
                widget.isEnabled()
                and widget.isVisibleTo(window)
                and top_left.x() <= point.x() < top_left.x() + widget.width()
                and top_left.y() <= point.y() < top_left.y() + widget.height()
            )
        except RuntimeError:
            return False

    def _point_hits_interactive_control(self, point: QPoint) -> bool:
        """Interactive controls own pointer input globally before any card does."""

        try:
            widgets = self.window.findChildren(QWidget)
        except RuntimeError:
            return False
        return any(
            isinstance(widget, _INTERACTIVE_WIDGETS)
            and self._widget_contains_window_point(widget, self.window, point)
            for widget in widgets
        )

    def _open_card_at(self, point: QPoint) -> bool:
        if self._presented or not self.static_view.static_active:
            return False
        if self._point_hits_interactive_control(point):
            return False
        cards = tuple(getattr(self.details, "_expandable_cards", ()))  # noqa: SLF001
        for frame in reversed(cards):
            if not isinstance(frame, QFrame):
                continue
            if not self._widget_contains_window_point(frame, self.window, point):
                continue
            try:
                if frame is getattr(self.window, "console", None):
                    self.details.open_console_details()
                else:
                    self.details.open(frame)
            except RuntimeError:
                return False
            return not self.details.drawer.isHidden()
        return False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is not self.quick or not self.static_view.static_active:
            return False
        if event.type() == QEvent.Type.Resize and self._item is not None:
            self._item.setWidth(float(max(1, self.quick.width())))
            self._item.setHeight(float(max(1, self.quick.height())))
            return False
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            if event.key() == Qt.Key.Key_Escape and self._presented:
                self.closeModal()
                return True
            return False
        if event.type() != QEvent.Type.MouseButtonRelease:
            return False
        if not isinstance(event, QMouseEvent) or event.button() != Qt.MouseButton.LeftButton:
            return False
        point = event.position().toPoint()
        self._open_card_at(point)
        return False

    @Slot()
    def closeModal(self) -> None:  # noqa: N802
        if not self._presented or not self._visible:
            return
        self._visible = False
        self.changed.emit()
        self._close_timer.start()

    def _finish_close(self) -> None:
        if not self._presented:
            return
        try:
            self.details.close_button.setEnabled(True)
            self.details.drawer.hide()
            self.details.scrim.hide()
            self.details.backdrop.hide()
            self.details.backdrop.clear()
            self.details.ghost.hide()
            self.details._selected = None  # noqa: SLF001
            self.details._modal_ratio = (0.80, 0.80)  # noqa: SLF001
        except RuntimeError:
            pass
        self._controls = []
        self._presented = False
        self._visible = False
        self.changed.emit()
        self.static_bridge.schedule_refresh()

    @Slot(str)
    def click(self, key: str) -> None:
        target = self.static_bridge._target(str(key))  # noqa: SLF001
        if target is self.details.close_button:
            self.closeModal()
            return
        self.static_bridge.click(str(key))
        QTimer.singleShot(0, self._refresh_controls)

    @Slot(str, str)
    def setText(self, key: str, text: str) -> None:  # noqa: N802
        self.static_bridge.setText(str(key), str(text))
        QTimer.singleShot(0, self._refresh_controls)

    @Slot(str, bool)
    def setChecked(self, key: str, checked: bool) -> None:  # noqa: N802
        self.static_bridge.setChecked(str(key), bool(checked))
        QTimer.singleShot(0, self._refresh_controls)

    @Slot(str, float)
    def setValue(self, key: str, value: float) -> None:  # noqa: N802
        self.static_bridge.setValue(str(key), float(value))
        QTimer.singleShot(0, self._refresh_controls)

    @Slot(str, int)
    def setComboIndex(self, key: str, index: int) -> None:  # noqa: N802
        self.static_bridge.setComboIndex(str(key), int(index))
        QTimer.singleShot(0, self._refresh_controls)

    @Slot(str, int)
    def setTabIndex(self, key: str, index: int) -> None:  # noqa: N802
        self.static_bridge.setTabIndex(str(key), int(index))
        QTimer.singleShot(0, self._refresh_controls)

    def cleanup(self) -> None:
        self._close_timer.stop()
        self._owner_timer.stop()
        self._restore_detail_owner()
        try:
            self.quick.removeEventFilter(self)
        except RuntimeError:
            pass
        try:
            self.static_bridge.sceneChanged.disconnect(self._on_main_scene_changed)
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


def install_quick_modal_layer(
    window: QWidget,
    static_view: StaticQmlViewController,
    details: FastCardDetailController,
) -> QuickModalLayerController:
    existing = getattr(window, "_quick_modal_layer", None)
    if isinstance(existing, QuickModalLayerController):
        return existing
    controller = QuickModalLayerController(window, static_view, details)
    window._quick_modal_layer = controller  # type: ignore[attr-defined]
    return controller


__all__ = ["QuickModalLayerController", "install_quick_modal_layer"]
