from __future__ import annotations

from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
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
        return {
            role: QByteArray(key.encode("ascii"))
            for role, key in self._KEYS.items()
        }

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802, B008
        return 0 if parent.isValid() else len(self._rows)

    def data(
        self,
        index: QModelIndex,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ):  # noqa: ANN201
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        key = self._KEYS.get(role)
        return self._rows[index.row()].get(key) if key is not None else None

    def replace(self, rows: list[dict[str, Any]]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()


class StaticQmlBridge(QObject):
    """State bridge only; it never participates in pointer/render frame traffic."""

    activeChanged = Signal()
    sceneChanged = Signal()

    def __init__(
        self,
        window: QMainWindow,
        visual: Any,
        card_model: StaticCardModel,
        parent: QObject,
    ) -> None:
        super().__init__(parent)
        self.window = window
        self.visual = visual
        self.background = visual.background
        self.card_model = card_model
        self._active = False
        self._root_controls: list[dict[str, Any]] = []
        self._targets: dict[str, QObject] = {}
        self._connected_widget_ids: set[int] = set()

        blur_path = getattr(self.background, "_blur_path", None)
        self._blur_url = (
            QUrl.fromLocalFile(str(blur_path))
            if blur_path is not None
            else QUrl()
        )
        self._sakura_url = "data:image/png;base64," + _SAKURA_PNG_B64

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(0)
        self._refresh_timer.timeout.connect(self.refresh)

        self._connect_state_sources()

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
        if not active:
            self._refresh_timer.stop()
        self.activeChanged.emit()

    @staticmethod
    def _layout_visible(widget: QWidget, window: QMainWindow) -> bool:
        current: QWidget | None = widget
        try:
            while current is not None and current is not window:
                if current.isHidden() or current.width() <= 0 or current.height() <= 0:
                    return False
                current = current.parentWidget()
        except RuntimeError:
            return False
        return current is window

    @staticmethod
    def _font_data(widget: QWidget) -> dict[str, Any]:
        try:
            info = QFontInfo(widget.font())
            pixel_size = max(9, int(info.pixelSize()))
            family = info.family() or "Segoe UI"
            weight = int(widget.font().weight())
        except RuntimeError:
            return {
                "fontFamily": "Segoe UI",
                "fontSize": 13,
                "fontWeight": 400,
            }
        return {
            "fontFamily": family,
            "fontSize": pixel_size,
            "fontWeight": weight,
        }

    @staticmethod
    def _label_color(label: QLabel) -> str:
        known = {
            "brandMark": "#bcffffff",
            "appTitle": "#fffdfd",
            "subtle": "#b2ffedf7",
            "cardHint": "#b2ffedf7",
            "cardTitle": "#fffafb",
            "sectionEyebrow": "#a6ffe2f1",
            "phaseBadge": "#fff1f8",
        }
        try:
            return known.get(label.objectName(), "#fff7fb")
        except RuntimeError:
            return "#fff7fb"

    @staticmethod
    def _alignment(label: QLabel) -> str:
        try:
            alignment = label.alignment()
        except RuntimeError:
            return "left"
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

    def _nearest_glass(
        self,
        widget: QWidget,
        frames: set[QFrame],
    ) -> QFrame | None:
        try:
            current: QWidget | None = widget
            while current is not None and current is not self.window:
                if isinstance(current, QFrame) and current in frames:
                    return current
                current = current.parentWidget()
        except RuntimeError:
            return None
        return None

    @staticmethod
    def _has_atomic_ancestor(widget: QWidget, stop: QWidget) -> bool:
        try:
            current = widget.parentWidget()
            while current is not None and current is not stop:
                if isinstance(current, _ATOMIC_TYPES):
                    return True
                current = current.parentWidget()
        except RuntimeError:
            return True
        return False

    def _register(self, widget: QObject) -> str:
        key = f"w{id(widget):x}"
        self._targets[key] = widget
        return key

    def _base(
        self,
        widget: QWidget,
        origin: QWidget,
        *,
        target: QObject | None = None,
    ) -> dict[str, Any] | None:
        if not self._layout_visible(widget, self.window):
            return None
        try:
            point = widget.mapTo(origin, QPoint(0, 0))
            width = int(widget.width())
            height = int(widget.height())
            enabled = bool(widget.isEnabled())
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
            "enabled": enabled,
        }
        data.update(self._font_data(widget))
        return data

    def _snapshot_widget(
        self,
        widget: QWidget,
        origin: QWidget,
    ) -> dict[str, Any] | None:
        if self._has_atomic_ancestor(widget, origin):
            return None

        try:
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
                    items=[tabs.tabText(i) for i in range(tabs.count())],
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
                        widget.item(row, column).text()
                        if widget.item(row, column) is not None
                        else ""
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
                    wrap=(
                        widget.lineWrapMode()
                        != QPlainTextEdit.LineWrapMode.NoWrap
                    ),
                    mono=True,
                )
                return data

            if isinstance(widget, QComboBox):
                data = self._base(widget, origin)
                if data is None:
                    return None
                data.update(
                    kind="combo",
                    items=[widget.itemText(i) for i in range(widget.count())],
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
                data.update(
                    kind="checkbox",
                    text=widget.text(),
                    checked=bool(widget.isChecked()),
                )
                return data

            if (
                isinstance(widget, QAbstractButton)
                and widget.objectName()
                in {"workspaceModeSwitch", "backgroundDriftSwitch"}
            ):
                data = self._base(widget, origin)
                if data is None:
                    return None
                data.update(kind="toggle", checked=bool(widget.isChecked()))
                return data

            if isinstance(widget, QAbstractButton):
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

            if isinstance(widget, QProgressBar):
                data = self._base(widget, origin)
                if data is None:
                    return None
                span = max(
                    1,
                    int(widget.maximum()) - int(widget.minimum()),
                )
                ratio = (
                    int(widget.value()) - int(widget.minimum())
                ) / span
                data.update(
                    kind="progress",
                    ratio=max(0.0, min(1.0, float(ratio))),
                )
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
                    rich=bool(
                        widget.textFormat() == Qt.TextFormat.RichText
                        or ("<" in text and ">" in text)
                    ),
                )
                return data

            if (
                isinstance(widget, QFrame)
                and widget.objectName()
                in {
                    "batchJobCard",
                    "batchJobDetails",
                    "cardDetailSection",
                }
            ):
                data = self._base(widget, origin)
                if data is None:
                    return None
                if widget.objectName() == "batchJobCard":
                    data.update(
                        kind="panel",
                        fill="#520d1d34",
                        border="#20ffffff",
                        radius=14,
                    )
                elif widget.objectName() == "batchJobDetails":
                    data.update(
                        kind="panel",
                        fill="#48050f1e",
                        border="#16ffffff",
                        radius=10,
                    )
                else:
                    data.update(
                        kind="panel",
                        fill="#16000000",
                        border="#14ffffff",
                        radius=10,
                    )
                return data
        except RuntimeError:
            return None

        return None

    def _controls_for(
        self,
        origin: QWidget,
        frames: set[QFrame],
    ) -> list[dict[str, Any]]:
        controls: list[dict[str, Any]] = []
        try:
            descendants = origin.findChildren(QWidget)
        except RuntimeError:
            return controls
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
        self._refresh_timer.stop()
        self._targets.clear()
        self._connect_widget_sources()

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
                name = frame.objectName()
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
                    "cardName": name,
                    "cardControls": self._controls_for(frame, frames),
                }
            )

        self.card_model.replace(rows)
        self._root_controls = self._controls_for(self.window, frames)
        self.sceneChanged.emit()

    def schedule_refresh(self, *_args: object) -> None:
        if not self._active or self._refresh_timer.isActive():
            return
        self._refresh_timer.start()

    @staticmethod
    def _connect(signal: Any, callback: Any) -> None:
        if signal is None or not hasattr(signal, "connect"):
            return
        try:
            signal.connect(callback)
        except (RuntimeError, TypeError):
            pass

    def _connect_state_sources(self) -> None:
        mode_stack = getattr(self.window, "mode_stack", None)
        self._connect(
            getattr(mode_stack, "currentChanged", None),
            self.schedule_refresh,
        )

        runner = getattr(self.window, "runner", None)
        for name in (
            "progress_changed",
            "result_updated",
            "running_changed",
            "completed",
            "failed",
        ):
            self._connect(getattr(runner, name, None), self.schedule_refresh)

        execution = getattr(self.window, "execution_runner", None)
        for name in (
            "progress_changed",
            "running_changed",
            "completed",
            "failed",
        ):
            self._connect(
                getattr(execution, name, None),
                self.schedule_refresh,
            )

        batch_workspace = getattr(self.window, "batch_workspace", None)
        controller = getattr(batch_workspace, "controller", None)
        for name in (
            "jobs_changed",
            "summary_changed",
            "running_changed",
            "state_changed",
        ):
            self._connect(
                getattr(controller, name, None),
                self.schedule_refresh,
            )

    def _connect_widget_sources(self) -> None:
        try:
            widgets = self.window.findChildren(QWidget)
        except RuntimeError:
            return
        for widget in widgets:
            identity = id(widget)
            if identity in self._connected_widget_ids:
                continue
            self._connected_widget_ids.add(identity)

            if isinstance(widget, QLineEdit):
                self._connect(widget.textChanged, self.schedule_refresh)
            elif isinstance(widget, QPlainTextEdit):
                self._connect(widget.textChanged, self.schedule_refresh)
            elif isinstance(widget, QSpinBox):
                self._connect(widget.valueChanged, self.schedule_refresh)
            elif isinstance(widget, QComboBox):
                self._connect(
                    widget.currentIndexChanged,
                    self.schedule_refresh,
                )
            elif isinstance(widget, QAbstractButton):
                self._connect(widget.toggled, self.schedule_refresh)
                self._connect(widget.clicked, self.schedule_refresh)
            elif isinstance(widget, QProgressBar):
                self._connect(widget.valueChanged, self.schedule_refresh)
            elif isinstance(widget, QTabWidget):
                self._connect(widget.currentChanged, self.schedule_refresh)

            if isinstance(widget, QTableWidget):
                try:
                    model = widget.model()
                except RuntimeError:
                    continue
                for name in (
                    "dataChanged",
                    "rowsInserted",
                    "rowsRemoved",
                    "modelReset",
                    "layoutChanged",
                ):
                    self._connect(
                        getattr(model, name, None),
                        self.schedule_refresh,
                    )

    def _target(self, key: str) -> QObject | None:
        return self._targets.get(str(key))

    @Slot(str)
    def click(self, key: str) -> None:
        target = self._target(key)
        try:
            if isinstance(target, QAbstractButton) and target.isEnabled():
                target.click()
        except RuntimeError:
            pass
        self.schedule_refresh()

    @Slot(str, str)
    def setText(self, key: str, text: str) -> None:  # noqa: N802
        target = self._target(key)
        try:
            if isinstance(target, QLineEdit) and not target.isReadOnly():
                target.setText(str(text))
            elif (
                isinstance(target, QPlainTextEdit)
                and not target.isReadOnly()
            ):
                target.setPlainText(str(text))
        except RuntimeError:
            pass
        self.schedule_refresh()

    @Slot(str, bool)
    def setChecked(self, key: str, checked: bool) -> None:  # noqa: N802
        target = self._target(key)
        try:
            if (
                isinstance(target, QAbstractButton)
                and target.isCheckable()
                and target.isEnabled()
            ):
                target.setChecked(bool(checked))
        except RuntimeError:
            pass
        self.schedule_refresh()

    @Slot(str, float)
    def setValue(self, key: str, value: float) -> None:  # noqa: N802
        target = self._target(key)
        try:
            if isinstance(target, QSpinBox) and target.isEnabled():
                target.setValue(int(value))
        except RuntimeError:
            pass
        self.schedule_refresh()

    @Slot(str, int)
    def setComboIndex(self, key: str, index: int) -> None:  # noqa: N802
        target = self._target(key)
        try:
            if (
                isinstance(target, QComboBox)
                and target.isEnabled()
                and 0 <= int(index) < target.count()
            ):
                target.setCurrentIndex(int(index))
        except RuntimeError:
            pass
        self.schedule_refresh()

    @Slot(str, int)
    def setTabIndex(self, key: str, index: int) -> None:  # noqa: N802
        target = self._target(key)
        try:
            if (
                isinstance(target, QTabWidget)
                and 0 <= int(index) < target.count()
            ):
                target.setCurrentIndex(int(index))
        except RuntimeError:
            pass
        self.schedule_refresh()


class StaticQmlViewController(QObject):
    """One Quick scene for drift-off; legacy HWND remains alive but unpresented."""

    def __init__(self, window: QMainWindow, visual: Any, startup_gate: Any) -> None:
        super().__init__(window)
        self.window = window
        self.visual = visual
        self.background = visual.background
        self.quick = self.background.quick_window
        self.engine = self.background.engine
        self.clock = getattr(window, "_presentation_clock", None)
        self.toggle = getattr(window, "_background_drift_switch", None)
        self.shell = getattr(window, "_native_window_shell", None)
        self._static_active = False

        if not isinstance(self.quick, QQuickWindow):
            raise RuntimeError(
                "static QML view requires the existing native QQuickWindow"
            )
        if not callable(getattr(self.shell, "set_overlay_presented", None)):
            raise RuntimeError(
                "static QML view requires native child presentation ownership"
            )

        self.card_model = StaticCardModel(self)
        self.bridge = StaticQmlBridge(
            window,
            visual,
            self.card_model,
            self,
        )
        context = self.engine.rootContext()
        context.setContextProperty("staticBridge", self.bridge)
        context.setContextProperty("staticCardModel", self.card_model)

        self.component = QQmlComponent(self.engine, self)
        self.component.setData(
            STATIC_QML_SOURCE.encode("utf-8"),
            QUrl("inmemory:/StaticRoot.qml"),
        )
        if self.component.isError():
            message = "\n".join(
                error.toString() for error in self.component.errors()
            )
            raise RuntimeError(
                f"static QML scene failed to compile:\n{message}"
            )

        created = self.component.create(context)
        if not isinstance(created, QQuickItem):
            message = "\n".join(
                error.toString() for error in self.component.errors()
            )
            if created is not None:
                created.deleteLater()
            raise RuntimeError(
                "static QML scene did not create a QQuickItem"
                + (f":\n{message}" if message else "")
            )

        self.item = created
        self.item.setParent(self)
        self.item.setParentItem(self.quick.contentItem())
        self.item.setVisible(False)
        self._fit()

        self.quick.widthChanged.connect(self._fit_and_refresh)
        self.quick.heightChanged.connect(self._fit_and_refresh)
        if self.toggle is not None:
            self.toggle.toggled.connect(self._on_drift_changed)

        handoff_ready = getattr(startup_gate, "handoffReady", None)
        if handoff_ready is None or not hasattr(handoff_ready, "connect"):
            raise RuntimeError(
                "static QML view requires explicit startup handoff signal"
            )
        handoff_ready.connect(self._activate_after_startup)

    @property
    def static_active(self) -> bool:
        return self._static_active

    def _fit(self) -> None:
        try:
            self.item.setWidth(float(max(1, self.quick.width())))
            self.item.setHeight(float(max(1, self.quick.height())))
        except RuntimeError:
            pass

    def _fit_and_refresh(self, *_args: object) -> None:
        self._fit()
        self.bridge.schedule_refresh()

    def _suspend_legacy(self) -> None:
        suspend = getattr(self.clock, "suspend", None)
        if callable(suspend):
            suspend("static_qml")

    def _resume_legacy(self) -> None:
        resume = getattr(self.clock, "resume", None)
        if callable(resume):
            resume("static_qml")

    def _enter_static(self) -> None:
        if self._static_active:
            return

        self.bridge.refresh()
        self._suspend_legacy()
        self._fit()
        self.bridge.set_active(True)
        self.item.setVisible(True)
        self._static_active = True
        try:
            self.quick.requestUpdate()
        except RuntimeError:
            pass

        # The QWidget object tree remains Qt-visible and fully alive. Only its
        # embedded child HWND stops being presented, so no Hide/Show cascade or
        # backing-store teardown can race the QML scene/startup event filters.
        self.shell.set_overlay_presented(False)

    def _leave_static(self) -> None:
        if not self._static_active:
            return

        self._static_active = False
        self.shell.set_overlay_presented(True)
        self.bridge.set_active(False)
        self.item.setVisible(False)
        schedule = getattr(self.background, "schedule_mask_update", None)
        if callable(schedule):
            schedule()
        self._resume_legacy()

    def _activate_after_startup(self) -> None:
        enabled = bool(
            getattr(self.clock, "background_drift_enabled", False)
        )
        if not enabled:
            self._enter_static()

    def _on_drift_changed(self, enabled: bool) -> None:
        if bool(enabled):
            self._leave_static()
        else:
            self._enter_static()


def install_static_qml_view(
    window: QMainWindow,
    visual: Any,
    startup_gate: Any,
) -> StaticQmlViewController:
    existing = getattr(window, "_static_qml_view_controller", None)
    if isinstance(existing, StaticQmlViewController):
        return existing
    controller = StaticQmlViewController(window, visual, startup_gate)
    window._static_qml_view_controller = controller  # type: ignore[attr-defined]
    return controller


__all__ = [
    "StaticCardModel",
    "StaticQmlBridge",
    "StaticQmlViewController",
    "install_static_qml_view",
]
