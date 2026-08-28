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
from PySide6.QtGui import QColor, QFontInfo, QPalette
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
    QSpinBox,
    QTabBar,
    QTabWidget,
    QTableWidget,
    QWidget,
)

from .nekro_effects import _SAKURA_PNG_B64


_ATOMIC_TYPES = (
    QLineEdit,
    QSpinBox,
    QComboBox,
    QPlainTextEdit,
    QTableWidget,
    QAbstractButton,
)


def _qml_color(color: QColor, fallback: str = "#ffffffff") -> str:
    if not color.isValid():
        return fallback
    return f"#{color.alpha():02x}{color.red():02x}{color.green():02x}{color.blue():02x}"


class StaticCardModel(QAbstractListModel):
    _BASE = int(Qt.ItemDataRole.UserRole)
    X = _BASE + 1
    Y = _BASE + 2
    W = _BASE + 3
    H = _BASE + 4
    NAME = _BASE + 5
    HOVER_SCALE = _BASE + 6
    CONTROLS = _BASE + 7

    _KEYS = {
        X: "cardX",
        Y: "cardY",
        W: "cardW",
        H: "cardH",
        NAME: "cardName",
        HOVER_SCALE: "hoverScale",
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
    """Expose the existing QWidget UI as data; never invent presentation tokens."""

    activeChanged = Signal()
    sceneChanged = Signal()
    cardDetailRequested = Signal(int)

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
        self._card_frames: list[QFrame] = []
        self._connected_widget_ids: set[int] = set()
        self._runtime_sources_bound = False
        self._local_input_commit_ids: set[int] = set()

        self._sakura_url = "data:image/png;base64," + _SAKURA_PNG_B64
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(0)
        self._refresh_timer.timeout.connect(self.refresh)

    def _get_active(self) -> bool:
        return self._active

    active = Property(bool, _get_active, notify=activeChanged)

    def _get_root_controls(self):  # noqa: ANN201
        return self._root_controls

    rootControls = Property("QVariantList", _get_root_controls, notify=sceneChanged)

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

    def bind_runtime_sources(self) -> None:
        if self._runtime_sources_bound:
            return
        self._runtime_sources_bound = True
        self._connect_state_sources()
        self._connect_widget_sources()

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
            pixel_size = int(info.pixelSize())
            if pixel_size <= 0:
                pixel_size = max(9, int(round(info.pointSizeF() * 96.0 / 72.0)))
            return {
                "fontFamily": info.family() or "Microsoft YaHei UI",
                "fontSize": max(9, pixel_size),
                "fontWeight": int(widget.font().weight()),
            }
        except RuntimeError:
            return {
                "fontFamily": "Microsoft YaHei UI",
                "fontSize": 13,
                "fontWeight": 400,
            }

    @staticmethod
    def _text_color(widget: QWidget) -> str:
        try:
            return _qml_color(widget.palette().color(QPalette.ColorRole.WindowText))
        except RuntimeError:
            return "#ffffffff"

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
            name = widget.objectName()
        except RuntimeError:
            return None
        if width <= 0 or height <= 0:
            return None
        data: dict[str, Any] = {
            "key": self._register(target or widget),
            "name": name,
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
                data = self._base(tabs, origin, target=tabs)
                if data is None:
                    return None
                tab_bar = tabs.tabBar()
                data.update(
                    kind="tabs",
                    items=[tabs.tabText(i) for i in range(tabs.count())],
                    currentIndex=int(tabs.currentIndex()),
                    tabWidths=[int(tab_bar.tabRect(i).width()) for i in range(tabs.count())],
                    tabHeight=max(1, int(tab_bar.height())),
                    tabStyle="side" if tabs.objectName() == "sideDetailTabs" else "console",
                )
                return data

            if isinstance(widget, QTableWidget):
                data = self._base(widget, origin)
                if data is None:
                    return None
                columns = int(widget.columnCount())
                row_limit = min(int(widget.rowCount()), 160)
                data.update(
                    kind="table",
                    headers=[
                        widget.horizontalHeaderItem(column).text()
                        if widget.horizontalHeaderItem(column) is not None
                        else ""
                        for column in range(columns)
                    ],
                    columnWidths=[int(widget.columnWidth(column)) for column in range(columns)],
                    headerHeight=max(1, int(widget.horizontalHeader().height())),
                    rowHeight=max(1, int(widget.verticalHeader().defaultSectionSize())),
                    rows=[
                        [
                            widget.item(row, column).text()
                            if widget.item(row, column) is not None
                            else ""
                            for column in range(columns)
                        ]
                        for row in range(row_limit)
                    ],
                )
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
                    wrap=(widget.lineWrapMode() != QPlainTextEdit.LineWrapMode.NoWrap),
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

            if isinstance(widget, QAbstractButton):
                data = self._base(widget, origin)
                if data is None:
                    return None
                if widget.objectName() in {"workspaceModeSwitch", "backgroundDriftSwitch"}:
                    data.update(kind="toggle", checked=bool(widget.isChecked()))
                    return data
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
                span = max(1, int(widget.maximum()) - int(widget.minimum()))
                ratio = (int(widget.value()) - int(widget.minimum())) / span
                data.update(
                    kind="progress",
                    ratio=max(0.0, min(1.0, float(ratio))),
                    text=widget.text(),
                )
                return data

            if isinstance(widget, QLabel):
                data = self._base(widget, origin)
                if data is None:
                    return None
                text = widget.text()
                if widget.objectName() in {"phaseBadge", "appVersionBadge"}:
                    kind = "badge"
                else:
                    kind = "label"
                data.update(
                    kind=kind,
                    text=text,
                    color=self._text_color(widget),
                    wordWrap=bool(widget.wordWrap()),
                    align=self._alignment(widget),
                    rich=bool(
                        widget.textFormat() == Qt.TextFormat.RichText
                        or ("<" in text and ">" in text)
                    ),
                )
                return data

            if isinstance(widget, QFrame) and widget.objectName() in {
                "batchJobCard",
                "batchJobDetails",
                "cardDetailSection",
                "consolePhaseUnit",
            }:
                data = self._base(widget, origin)
                if data is None:
                    return None
                try:
                    fill = _qml_color(widget.palette().color(QPalette.ColorRole.Window), "#00000000")
                except RuntimeError:
                    fill = "#00000000"
                data.update(kind="panel", fill=fill)
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

    def _hover_scale(self, frame: QFrame) -> float:
        controller = getattr(self.window, "_nekro_card_fx", None)
        getter = getattr(controller, "_hover_scale_for", None)
        if callable(getter):
            try:
                return float(getter(frame))
            except RuntimeError:
                pass
        return 1.02

    def refresh(self) -> None:
        self._refresh_timer.stop()
        self._targets.clear()
        self._connect_widget_sources()

        frames_list = self._glass_frames()
        frames = set(frames_list)
        rows: list[dict[str, Any]] = []
        row_frames: list[QFrame] = []
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
                    "hoverScale": self._hover_scale(frame),
                    "cardControls": self._controls_for(frame, frames),
                }
            )
            row_frames.append(frame)

        self._card_frames = row_frames
        self.card_model.replace(rows)
        self._root_controls = self._controls_for(self.window, frames)
        self.sceneChanged.emit()

    def schedule_refresh(self, *_args: object) -> None:
        if not self._active or self._refresh_timer.isActive():
            return
        self._refresh_timer.start()

    def _schedule_input_refresh(self, widget: QWidget) -> None:
        if id(widget) in self._local_input_commit_ids:
            return
        self.schedule_refresh()

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
        self._connect(getattr(mode_stack, "currentChanged", None), self.schedule_refresh)

        for source_name in ("runner", "execution_runner"):
            source = getattr(self.window, source_name, None)
            for signal_name in (
                "progress_changed",
                "result_updated",
                "running_changed",
                "completed",
                "failed",
            ):
                self._connect(getattr(source, signal_name, None), self.schedule_refresh)

        batch_workspace = getattr(self.window, "batch_workspace", None)
        controller = getattr(batch_workspace, "controller", None)
        for signal_name in (
            "jobs_changed",
            "summary_changed",
            "running_changed",
            "state_changed",
        ):
            self._connect(getattr(controller, signal_name, None), self.schedule_refresh)

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
                self._connect(
                    widget.textChanged,
                    lambda *_args, source=widget: self._schedule_input_refresh(source),
                )
            elif isinstance(widget, QPlainTextEdit):
                self._connect(
                    widget.textChanged,
                    lambda *_args, source=widget: self._schedule_input_refresh(source),
                )
            elif isinstance(widget, QSpinBox):
                self._connect(
                    widget.valueChanged,
                    lambda *_args, source=widget: self._schedule_input_refresh(source),
                )
            elif isinstance(widget, QComboBox):
                self._connect(
                    widget.currentIndexChanged,
                    lambda *_args, source=widget: self._schedule_input_refresh(source),
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
                for signal_name in (
                    "dataChanged",
                    "rowsInserted",
                    "rowsRemoved",
                    "modelReset",
                    "layoutChanged",
                ):
                    self._connect(getattr(model, signal_name, None), self.schedule_refresh)

    def _target(self, key: str) -> QObject | None:
        return self._targets.get(str(key))

    @Slot(int)
    def requestCardDetail(self, row: int) -> None:  # noqa: N802
        row = int(row)
        if self._active and 0 <= row < len(self._card_frames):
            self.cardDetailRequested.emit(row)

    @Slot(int, bool, bool)
    def setCardInteraction(self, row: int, hovered: bool, pressed: bool) -> None:  # noqa: N802
        if not self._active or not 0 <= int(row) < len(self._card_frames):
            return
        frame = self._card_frames[int(row)]
        if pressed:
            scale, alpha = 1.0, 102.0
        elif hovered:
            scale, alpha = self._hover_scale(frame), 102.0
        else:
            scale, alpha = 1.0, 64.0
        try:
            self.background.set_card_presentation(frame, scale=scale, alpha=alpha)
        except RuntimeError:
            pass

    @Slot()
    def resetCardInteractions(self) -> None:  # noqa: N802
        for frame in tuple(self._card_frames):
            try:
                self.background.set_card_presentation(frame, scale=1.0, alpha=64.0)
            except RuntimeError:
                continue

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
        if not isinstance(target, (QLineEdit, QPlainTextEdit)):
            return
        identity = id(target)
        self._local_input_commit_ids.add(identity)
        try:
            if isinstance(target, QLineEdit) and not target.isReadOnly():
                target.setText(str(text))
            elif isinstance(target, QPlainTextEdit) and not target.isReadOnly():
                target.setPlainText(str(text))
        except RuntimeError:
            pass
        finally:
            self._local_input_commit_ids.discard(identity)

    @Slot(str, bool)
    def setChecked(self, key: str, checked: bool) -> None:  # noqa: N802
        target = self._target(key)
        try:
            if isinstance(target, QAbstractButton) and target.isCheckable() and target.isEnabled():
                target.setChecked(bool(checked))
        except RuntimeError:
            pass
        self.schedule_refresh()

    @Slot(str, float)
    def setValue(self, key: str, value: float) -> None:  # noqa: N802
        target = self._target(key)
        if not isinstance(target, QSpinBox):
            return
        identity = id(target)
        self._local_input_commit_ids.add(identity)
        try:
            if target.isEnabled():
                target.setValue(int(value))
        except RuntimeError:
            pass
        finally:
            self._local_input_commit_ids.discard(identity)

    @Slot(str, int)
    def setComboIndex(self, key: str, index: int) -> None:  # noqa: N802
        target = self._target(key)
        if not isinstance(target, QComboBox):
            return
        identity = id(target)
        self._local_input_commit_ids.add(identity)
        try:
            if target.isEnabled() and 0 <= int(index) < target.count():
                target.setCurrentIndex(int(index))
        except RuntimeError:
            pass
        finally:
            self._local_input_commit_ids.discard(identity)

    @Slot(str, int)
    def setTabIndex(self, key: str, index: int) -> None:  # noqa: N802
        target = self._target(key)
        try:
            if isinstance(target, QTabWidget) and 0 <= int(index) < target.count():
                target.setCurrentIndex(int(index))
        except RuntimeError:
            pass
        self.schedule_refresh()


__all__ = ["StaticCardModel", "StaticQmlBridge"]
