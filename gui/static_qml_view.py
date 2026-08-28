from __future__ import annotations

from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QEvent,
    QModelIndex,
    QObject,
    QPoint,
    Property,
    QTimer,
    Qt,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QFontInfo
from PySide6.QtQml import QQmlComponent
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtWidgets import (
    QAbstractButton,
    QCheckBox,
    QComboBox,
    QFrame,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTabBar,
    QTabWidget,
    QTableWidget,
    QWidget,
)

from .nekro_effects import _SAKURA_PNG_B64
from .static_qml_scene import STATIC_QML_SOURCE


_GLASS_NAMES = {"glassCard", "heroCard", "statusCard", "microCard"}
_SCENE_EVENTS = {
    QEvent.Type.LayoutRequest,
    QEvent.Type.Resize,
    QEvent.Type.Move,
    QEvent.Type.Show,
    QEvent.Type.Hide,
    QEvent.Type.EnabledChange,
    QEvent.Type.FontChange,
    QEvent.Type.StyleChange,
}
_ATOMIC_TYPES = (
    QLineEdit,
    QSpinBox,
    QComboBox,
    QPlainTextEdit,
    QTableWidget,
    QAbstractButton,
)


class StaticCardModel(QAbstractListModel):
    _BASE = int(Qt.ItemDataRole.UserRole)
    X = _BASE + 1
    Y = _BASE + 2
    W = _BASE + 3
    H = _BASE + 4
    NAME = _BASE + 5
    CONTROLS = _BASE + 6

    _KEYS = {
        X: "cardX",
        Y: "cardY",
        W: "cardW",
        H: "cardH",
        NAME: "cardName",
        CONTROLS: "cardControls",
    }

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self._rows: list[dict[str, Any]] = []

    def roleNames(self) -> dict[int, QByteArray]:  # noqa: N802
        return {role: QByteArray(key.encode("ascii")) for role, key in self._KEYS.items()}

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802, B008
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)):  # noqa: ANN201
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        key = self._KEYS.get(role)
        return self._rows[index.row()].get(key) if key is not None else None

    def replace(self, rows: list[dict[str, Any]]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()


class StaticQmlBridge(QObject):
    activeChanged = Signal()
    sceneChanged = Signal()

    def __init__(self, window: QMainWindow, visual: Any, card_model: StaticCardModel) -> None:
        super().__init__(window)
        self.window = window
        self.visual = visual
        self.background = visual.background
        self.card_model = card_model
        self._active = False
        self._root_controls: list[dict[str, Any]] = []
        self._targets: dict[str, QObject] = {}
        self._refresh_posted = False
        self._watched: list[QWidget] = []

        blur_path = getattr(self.background, "_blur_path", None)
        self._blur_url = QUrl.fromLocalFile(str(blur_path)) if blur_path is not None else QUrl()
        self._sakura_url = "data:image/png;base64," + _SAKURA_PNG_B64

        self._install_watches()
        self._connect_state_sources()
        self.refresh()

    def _get_active(self) -> bool:
        return self._active

    active = Property(bool, _get_active, notify=activeChanged)

    def _get_root_controls(self):  # noqa: ANN201
        return self._root_controls

    rootControls = Property("QVariantList", _get_root_controls, notify=sceneChanged)

    def _get_blur_url(self) -> QUrl:
        return self._blur_url

    blurUrl = Property(QUrl, _get_blur_url, constant=True)

    def _get_sakura_url(self) -> str:
        return self._sakura_url

    sakuraUrl = Property(str, _get_sakura_url, constant=True)

    def set_active(self, active: bool) -> None:
        active = bool(active)
        if active == self._active:
            return
        self._active = active
        self.activeChanged.emit()

    @staticmethod
    def _layout_visible(widget: QWidget, window: QMainWindow) -> bool:
        current: QWidget | None = widget
        while current is not None and current is not window:
            try:
                if current.isHidden() or current.width() <= 0 or current.height() <= 0:
                    return False
            except RuntimeError:
                return False
            current = current.parentWidget()
        return current is window

    @staticmethod
    def _font_data(widget: QWidget) -> dict[str, Any]:
        try:
            info = QFontInfo(widget.font())
            pixel_size = max(9, int(info.pixelSize()))
            family = info.family() or "Segoe UI"
            weight = int(widget.font().weight())
        except RuntimeError:
            return {"fontFamily": "Segoe UI", "fontSize": 13, "fontWeight": 400}
        return {"fontFamily": family, "fontSize": pixel_size, "fontWeight": weight}

    @staticmethod
    def _label_color(label: QLabel) -> str:
        name = label.objectName()
        known = {
            "brandMark": "#bcffffff",
            "appTitle": "#fffdfd",
            "subtle": "#b2ffedf7",
            "cardHint": "#b2ffedf7",
            "cardTitle": "#fffafb",
            "sectionEyebrow": "#a6ffe2f1",
            "phaseBadge": "#fff1f8",
        }
        return known.get(name, "#fff7fb")

    @staticmethod
    def _alignment(label: QLabel) -> str:
        alignment = label.alignment()
        if alignment & Qt.AlignmentFlag.AlignHCenter:
            return "center"
        if alignment & Qt.AlignmentFlag.AlignRight:
            return "right"
        return "left"

    def _glass_frames(self) -> list[QFrame]:
        glass = getattr(self.visual, "_glass", None)
        if not isinstance(glass, dict):
            return []
        return [frame for frame in glass if isinstance(frame, QFrame)]

    def _nearest_glass(self, widget: QWidget, frames: set[QFrame]) -> QFrame | None:
        current: QWidget | None = widget
        while current is not None and current is not self.window:
            if isinstance(current, QFrame) and current in frames:
                return current
            current = current.parentWidget()
        return None

    @staticmethod
    def _has_atomic_ancestor(widget: QWidget, stop: QWidget) -> bool:
        current = widget.parentWidget()
        while current is not None and current is not stop:
            if isinstance(current, _ATOMIC_TYPES):
                return True
            current = current.parentWidget()
        return False

    def _register(self, widget: QObject) -> str:
        key = f"w{id(widget):x}"
        self._targets[key] = widget
        return key

    def _base(self, widget: QWidget, origin: QWidget, *, target: QObject | None = None) -> dict[str, Any] | None:
        if not self._layout_visible(widget, self.window):
            return None
        try:
            point = widget.mapTo(origin, QPoint(0, 0))
            width = int(widget.width())
            height = int(widget.height())
        except RuntimeError:
            return None
        if width <= 0 or height <= 0:
            return None
        data: dict[str, Any] = {
            "key": self._register(target or widget),
            "x": int(point.x()),
            "y": int(point.y()),
            "w": width,
            "h": height,
            "enabled": bool(widget.isEnabled()),
        }
        data.update(self._font_data(widget))
        return data

    def _snapshot_widget(self, widget: QWidget, origin: QWidget) -> dict[str, Any] | None:
        if self._has_atomic_ancestor(widget, origin):
            return None

        if isinstance(widget, QTabBar):
            tabs = widget.parentWidget()
            while tabs is not None and not isinstance(tabs, QTabWidget):
                tabs = tabs.parentWidget()
            if not isinstance(tabs, QTabWidget):
                return None
            data = self._base(widget, origin, target=tabs)
            if data is None:
                return None
            data.update(
                kind="tabs",
                items=[tabs.tabText(index) for index in range(tabs.count())],
                currentIndex=int(tabs.currentIndex()),
            )
            return data

        if isinstance(widget, QTableWidget):
            data = self._base(widget, origin)
            if data is None:
                return None
            headers = [
                widget.horizontalHeaderItem(column).text()
                if widget.horizontalHeaderItem(column) is not None
                else ""
                for column in range(widget.columnCount())
            ]
            row_limit = min(widget.rowCount(), 160)
            rows = [
                [
                    widget.item(row, column).text() if widget.item(row, column) is not None else ""
                    for column in range(widget.columnCount())
                ]
                for row in range(row_limit)
            ]
            data.update(kind="table", headers=headers, rows=rows)
            return data

        if isinstance(widget, QPlainTextEdit):
            data = self._base(widget, origin)
            if data is None:
                return None
            text = widget.toPlainText()
            if len(text) > 80_000:
                text = text[-80_000:]
            data.update(
                kind="textedit",
                text=text,
                readOnly=bool(widget.isReadOnly()),
                wrap=widget.lineWrapMode() != QPlainTextEdit.LineWrapMode.NoWrap,
                mono=True,
            )
            return data

        if isinstance(widget, QComboBox):
            data = self._base(widget, origin)
            if data is None:
                return None
            data.update(
                kind="combo",
                items=[widget.itemText(index) for index in range(widget.count())],
                currentIndex=int(widget.currentIndex()),
            )
            return data

        if isinstance(widget, QSpinBox):
            data = self._base(widget, origin)
            if data is None:
                return None
            data.update(
                kind="spinbox",
                value=int(widget.value()),
                minimum=int(widget.minimum()),
                maximum=int(widget.maximum()),
            )
            return data

        if isinstance(widget, QLineEdit):
            data = self._base(widget, origin)
            if data is None:
                return None
            data.update(
                kind="lineedit",
                text=widget.text(),
                placeholder=widget.placeholderText(),
                readOnly=bool(widget.isReadOnly()),
            )
            return data

        if isinstance(widget, QCheckBox):
            data = self._base(widget, origin)
            if data is None:
                return None
            data.update(kind="checkbox", text=widget.text(), checked=bool(widget.isChecked()))
            return data

        if isinstance(widget, QPushButton):
            data = self._base(widget, origin)
            if data is None:
                return None
            name = widget.objectName()
            style = (
                "primary"
                if name in {"primaryButton", "modalPrimaryButton"}
                else "danger"
                if name in {"dangerButton", "modalDangerButton"}
                else "quiet"
                if name == "quietButton"
                else "default"
            )
            data.update(kind="button", text=widget.text(), style=style)
            return data

        if isinstance(widget, QAbstractButton) and widget.objectName() in {
            "workspaceModeSwitch",
            "backgroundDriftSwitch",
        }:
            data = self._base(widget, origin)
            if data is None:
                return None
            data.update(kind="toggle", checked=bool(widget.isChecked()))
            return data

        if isinstance(widget, QProgressBar):
            data = self._base(widget, origin)
            if data is None:
                return None
            span = max(1, int(widget.maximum()) - int(widget.minimum()))
            ratio = (int(widget.value()) - int(widget.minimum())) / span
            data.update(kind="progress", ratio=max(0.0, min(1.0, float(ratio))))
            return data

        if isinstance(widget, QLabel):
            data = self._base(widget, origin)
            if data is None:
                return None
            text = widget.text()
            data.update(
                kind="label",
                text=text,
                color=self._label_color(widget),
                wordWrap=bool(widget.wordWrap()),
                align=self._alignment(widget),
                rich=bool(widget.textFormat() == Qt.TextFormat.RichText or ("<" in text and ">" in text)),
            )
            return data

        if isinstance(widget, QFrame) and widget.objectName() in {
            "batchJobCard",
            "batchJobDetails",
            "cardDetailSection",
        }:
            data = self._base(widget, origin)
            if data is None:
                return None
            if widget.objectName() == "batchJobCard":
                data.update(kind="panel", fill="#520d1d34", border="#20ffffff", radius=14)
            elif widget.objectName() == "batchJobDetails":
                data.update(kind="panel", fill="#48050f1e", border="#16ffffff", radius=10)
            else:
                data.update(kind="panel", fill="#16000000", border="#14ffffff", radius=10)
            return data

        return None

    def _controls_for(self, origin: QWidget, frames: set[QFrame]) -> list[dict[str, Any]]:
        controls: list[dict[str, Any]] = []
        descendants = origin.findChildren(QWidget)
        for widget in descendants:
            if isinstance(widget, QFrame) and widget in frames:
                continue
            nearest = self._nearest_glass(widget, frames)
            if isinstance(origin, QFrame):
                if nearest is not origin:
                    continue
            elif nearest is not None:
                continue
            data = self._snapshot_widget(widget, origin)
            if data is not None:
                controls.append(data)
        controls.sort(key=lambda item: (int(item["y"]), int(item["x"])))
        return controls

    def refresh(self) -> None:
        self._refresh_posted = False
        self._targets.clear()
        frames_list = self._glass_frames()
        frames = set(frames_list)
        rows: list[dict[str, Any]] = []
        for frame in frames_list:
            if not self._layout_visible(frame, self.window):
                continue
            try:
                point = frame.mapTo(self.window, QPoint(0, 0))
                width = int(frame.width())
                height = int(frame.height())
            except RuntimeError:
                continue
            if width <= 0 or height <= 0:
                continue
            rows.append(
                {
                    "cardX": int(point.x()),
                    "cardY": int(point.y()),
                    "cardW": width,
                    "cardH": height,
                    "cardName": frame.objectName(),
                    "cardControls": self._controls_for(frame, frames),
                }
            )

        self.card_model.replace(rows)
        self._root_controls = self._controls_for(self.window, frames)
        self.sceneChanged.emit()

    def schedule_refresh(self, *_args: object) -> None:
        if self._refresh_posted:
            return
        self._refresh_posted = True
        QTimer.singleShot(0, self.refresh)

    def _watch(self, widget: QWidget) -> None:
        if any(current is widget for current in self._watched):
            return
        widget.installEventFilter(self)
        self._watched.append(widget)

    def _install_watches(self) -> None:
        self._watch(self.window)
        central = self.window.centralWidget()
        if isinstance(central, QWidget):
            self._watch(central)
        for widget in self.window.findChildren(QWidget):
            self._watch(widget)

    @staticmethod
    def _connect(signal: Any, callback: Any) -> None:
        if signal is not None and hasattr(signal, "connect"):
            try:
                signal.connect(callback)
            except (RuntimeError, TypeError):
                pass

    def _connect_state_sources(self) -> None:
        mode_stack = getattr(self.window, "mode_stack", None)
        self._connect(getattr(mode_stack, "currentChanged", None), self.schedule_refresh)

        runner = getattr(self.window, "runner", None)
        for name in ("progress_changed", "result_updated", "running_changed", "completed", "failed"):
            self._connect(getattr(runner, name, None), self.schedule_refresh)

        execution = getattr(self.window, "execution_runner", None)
        for name in ("progress_changed", "running_changed", "completed", "failed"):
            self._connect(getattr(execution, name, None), self.schedule_refresh)

        batch_workspace = getattr(self.window, "batch_workspace", None)
        controller = getattr(batch_workspace, "controller", None)
        for name in ("jobs_changed", "summary_changed", "running_changed", "state_changed"):
            self._connect(getattr(controller, name, None), self.schedule_refresh)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() in _SCENE_EVENTS:
            self.schedule_refresh()
        return False

    def _target(self, key: str) -> QObject | None:
        return self._targets.get(str(key))

    @Slot(str)
    def click(self, key: str) -> None:
        target = self._target(key)
        if isinstance(target, QAbstractButton) and target.isEnabled():
            target.click()
            self.schedule_refresh()

    @Slot(str, str)
    def setText(self, key: str, text: str) -> None:  # noqa: N802
        target = self._target(key)
        if isinstance(target, QLineEdit) and not target.isReadOnly():
            target.setText(str(text))
        elif isinstance(target, QPlainTextEdit) and not target.isReadOnly():
            target.setPlainText(str(text))
        self.schedule_refresh()

    @Slot(str, bool)
    def setChecked(self, key: str, checked: bool) -> None:  # noqa: N802
        target = self._target(key)
        if isinstance(target, QAbstractButton) and target.isCheckable() and target.isEnabled():
            target.setChecked(bool(checked))
        self.schedule_refresh()

    @Slot(str, float)
    def setValue(self, key: str, value: float) -> None:  # noqa: N802
        target = self._target(key)
        if isinstance(target, QSpinBox) and target.isEnabled():
            target.setValue(int(value))
        self.schedule_refresh()

    @Slot(str, int)
    def setComboIndex(self, key: str, index: int) -> None:  # noqa: N802
        target = self._target(key)
        if isinstance(target, QComboBox) and target.isEnabled() and 0 <= int(index) < target.count():
            target.setCurrentIndex(int(index))
        self.schedule_refresh()

    @Slot(str, int)
    def setTabIndex(self, key: str, index: int) -> None:  # noqa: N802
        target = self._target(key)
        if isinstance(target, QTabWidget) and 0 <= int(index) < target.count():
            target.setCurrentIndex(int(index))
        self.schedule_refresh()

    def cleanup(self) -> None:
        for widget in self._watched:
            try:
                widget.removeEventFilter(self)
            except RuntimeError:
                pass
        self._watched.clear()


class StaticQmlViewController(QObject):
    """Whole-view static renderer hosted by the already-existing Quick window.

    Drift-off owns one native QML scene. Drift-on simply hides that scene and
    restores the untouched embedded QWidget child and legacy presentation clock.
    No QWidget is sampled, textured, or handed to Quick during card interaction.
    """

    def __init__(self, window: QMainWindow, visual: Any, entrance: Any) -> None:
        super().__init__(window)
        self.window = window
        self.visual = visual
        self.background = visual.background
        self.entrance = entrance
        self.quick = self.background.quick_window
        self.engine = self.background.engine
        self.clock = getattr(window, "_presentation_clock", None)
        self.card_fx = getattr(window, "_nekro_card_fx", None)
        self.toggle = getattr(window, "_background_drift_switch", None)
        self._static_active = False
        self._cleaned = False

        if not isinstance(self.quick, QQuickWindow):
            raise RuntimeError("static QML view requires the existing native QQuickWindow")

        self.card_model = StaticCardModel(self)
        self.bridge = StaticQmlBridge(window, visual, self.card_model)
        context = self.engine.rootContext()
        context.setContextProperty("staticBridge", self.bridge)
        context.setContextProperty("staticCardModel", self.card_model)

        self.component = QQmlComponent(self.engine, self)
        self.component.setData(STATIC_QML_SOURCE.encode("utf-8"), QUrl("inmemory:/StaticRoot.qml"))
        if self.component.isError():
            message = "\n".join(error.toString() for error in self.component.errors())
            raise RuntimeError(f"static QML scene failed to compile:\n{message}")
        created = self.component.create(context)
        if not isinstance(created, QQuickItem):
            if created is not None:
                created.deleteLater()
            raise RuntimeError("static QML scene did not create a QQuickItem")

        self.item = created
        self.item.setParent(self)
        self.item.setParentItem(self.quick.contentItem())
        self.item.setVisible(False)
        self._fit()
        self.quick.widthChanged.connect(self._fit)
        self.quick.heightChanged.connect(self._fit)

        if self.toggle is not None:
            self.toggle.toggled.connect(self._on_drift_changed)

        overlay = getattr(entrance, "overlay", None)
        if isinstance(overlay, QObject):
            overlay.destroyed.connect(self._activate_after_startup)
        else:
            QTimer.singleShot(0, self._activate_after_startup)

        window.destroyed.connect(self.cleanup)

    def _fit(self, *_args: object) -> None:
        if self._cleaned:
            return
        try:
            self.item.setWidth(float(max(1, self.quick.width())))
            self.item.setHeight(float(max(1, self.quick.height())))
        except RuntimeError:
            pass

    def _suspend_legacy(self) -> None:
        suspend_cards = getattr(self.card_fx, "suspend_for_modal", None)
        if callable(suspend_cards):
            suspend_cards()
        suspend_clock = getattr(self.clock, "suspend", None)
        if callable(suspend_clock):
            suspend_clock("static_qml")

    def _resume_legacy(self) -> None:
        resume_cards = getattr(self.card_fx, "resume_from_modal", None)
        if callable(resume_cards):
            resume_cards()
        resume_clock = getattr(self.clock, "resume", None)
        if callable(resume_clock):
            resume_clock("static_qml")

    def _set_legacy_glass_alpha(self, alpha: float) -> None:
        for frame in self.bridge._glass_frames():
            try:
                self.background.set_card_presentation(frame, scale=1.0, alpha=float(alpha))
            except RuntimeError:
                continue

    def _hide_legacy(self) -> None:
        if not self._static_active or self._cleaned:
            return
        try:
            self.window.hide()
        except RuntimeError:
            return

    def _enter_static(self) -> None:
        if self._cleaned or self._static_active:
            return
        self.bridge.refresh()
        self._suspend_legacy()
        self._set_legacy_glass_alpha(0.0)
        self._fit()
        self.bridge.set_active(True)
        self.item.setVisible(True)
        self._static_active = True
        try:
            self.quick.requestUpdate()
        except RuntimeError:
            pass
        QTimer.singleShot(0, self._hide_legacy)

    def _leave_static(self) -> None:
        if self._cleaned or not self._static_active:
            return
        self._static_active = False
        self.bridge.set_active(False)
        self.item.setVisible(False)
        self._set_legacy_glass_alpha(64.0)
        try:
            self.window.show()
        except RuntimeError:
            return
        shell = getattr(self.window, "_native_window_shell", None)
        fit = getattr(shell, "_fit_native_child", None)
        if callable(fit):
            QTimer.singleShot(0, fit)
        schedule = getattr(self.background, "schedule_mask_update", None)
        if callable(schedule):
            schedule()
        self._resume_legacy()

    def _activate_after_startup(self, *_args: object) -> None:
        if self._cleaned:
            return
        enabled = bool(getattr(self.clock, "background_drift_enabled", False))
        if not enabled:
            QTimer.singleShot(0, self._enter_static)

    def _on_drift_changed(self, enabled: bool) -> None:
        if bool(enabled):
            self._leave_static()
        else:
            self._enter_static()

    def cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        self.bridge.cleanup()
        try:
            self.item.setParentItem(None)
            self.item.deleteLater()
        except RuntimeError:
            pass


def install_static_qml_view(window: QMainWindow, visual: Any, entrance: Any) -> StaticQmlViewController:
    existing = getattr(window, "_static_qml_view_controller", None)
    if isinstance(existing, StaticQmlViewController):
        return existing
    controller = StaticQmlViewController(window, visual, entrance)
    window._static_qml_view_controller = controller  # type: ignore[attr-defined]
    return controller


__all__ = ["StaticCardModel", "StaticQmlBridge", "StaticQmlViewController", "install_static_qml_view"]
