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
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QFontInfo, QPalette
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractScrollArea,
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
_REFRESH_FRAME_MS = 16
_MAX_MIRRORED_TEXT = 80_000


def _qml_color(color: QColor, fallback: str = "#ffffffff") -> str:
    if not color.isValid():
        return fallback
    return f"#{color.alpha():02x}{color.red():02x}{color.green():02x}{color.blue():02x}"


class _ReadOnlyTextControl(QObject):
    """Long-lived live model for read-only text panes in the Quick scene.

    Runtime log documents are high-frequency output streams. Keeping their QML
    control identity stable lets textChanged notify only the TextArea binding instead
    of replacing the containing cardControls QVariantList and rebuilding its Repeater.
    """

    changed = Signal()

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self._state: dict[str, Any] = {
            "key": "",
            "name": "",
            "x": 0,
            "y": 0,
            "w": 0,
            "h": 0,
            "enabled": True,
            "clipEnabled": False,
            "clipX": 0,
            "clipY": 0,
            "clipW": 0,
            "clipH": 0,
            "fontSize": 11,
            "kind": "textedit",
            "text": "",
            "readOnly": True,
            "wrap": False,
        }

    def _value(self, key: str, default: Any = None) -> Any:
        return self._state.get(key, default)

    key = Property(str, lambda self: str(self._value("key", "")), notify=changed)
    name = Property(str, lambda self: str(self._value("name", "")), notify=changed)
    x = Property(int, lambda self: int(self._value("x", 0)), notify=changed)
    y = Property(int, lambda self: int(self._value("y", 0)), notify=changed)
    w = Property(int, lambda self: int(self._value("w", 0)), notify=changed)
    h = Property(int, lambda self: int(self._value("h", 0)), notify=changed)
    enabled = Property(bool, lambda self: bool(self._value("enabled", True)), notify=changed)
    clipEnabled = Property(bool, lambda self: bool(self._value("clipEnabled", False)), notify=changed)
    clipX = Property(int, lambda self: int(self._value("clipX", 0)), notify=changed)
    clipY = Property(int, lambda self: int(self._value("clipY", 0)), notify=changed)
    clipW = Property(int, lambda self: int(self._value("clipW", 0)), notify=changed)
    clipH = Property(int, lambda self: int(self._value("clipH", 0)), notify=changed)
    fontSize = Property(int, lambda self: int(self._value("fontSize", 11)), notify=changed)
    kind = Property(str, lambda self: "textedit", constant=True)
    text = Property(str, lambda self: str(self._value("text", "")), notify=changed)
    readOnly = Property(bool, lambda self: True, constant=True)
    wrap = Property(bool, lambda self: bool(self._value("wrap", False)), notify=changed)

    def sync(self, data: dict[str, Any], *, text: str, wrap: bool) -> None:
        next_state = {
            "key": str(data.get("key") or ""),
            "name": str(data.get("name") or ""),
            "x": int(data.get("x") or 0),
            "y": int(data.get("y") or 0),
            "w": int(data.get("w") or 0),
            "h": int(data.get("h") or 0),
            "enabled": bool(data.get("enabled", True)),
            "clipEnabled": bool(data.get("clipEnabled", False)),
            "clipX": int(data.get("clipX") or 0),
            "clipY": int(data.get("clipY") or 0),
            "clipW": int(data.get("clipW") or 0),
            "clipH": int(data.get("clipH") or 0),
            "fontSize": int(data.get("fontSize") or 11),
            "kind": "textedit",
            "text": str(text),
            "readOnly": True,
            "wrap": bool(wrap),
        }
        if next_state == self._state:
            return
        self._state = next_state
        self.changed.emit()

    def sync_text(self, text: str) -> None:
        text = str(text)
        if text == self._state.get("text", ""):
            return
        self._state["text"] = text
        self.changed.emit()


class _ProgressControl(QObject):
    """Long-lived progress model so value changes never wake the whole mirror tree."""

    changed = Signal()

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self._state: dict[str, Any] = {
            "key": "",
            "name": "",
            "x": 0,
            "y": 0,
            "w": 0,
            "h": 0,
            "enabled": True,
            "clipEnabled": False,
            "clipX": 0,
            "clipY": 0,
            "clipW": 0,
            "clipH": 0,
            "kind": "progress",
            "ratio": 0.0,
            "text": "",
        }

    def _value(self, key: str, default: Any = None) -> Any:
        return self._state.get(key, default)

    key = Property(str, lambda self: str(self._value("key", "")), notify=changed)
    name = Property(str, lambda self: str(self._value("name", "")), notify=changed)
    x = Property(int, lambda self: int(self._value("x", 0)), notify=changed)
    y = Property(int, lambda self: int(self._value("y", 0)), notify=changed)
    w = Property(int, lambda self: int(self._value("w", 0)), notify=changed)
    h = Property(int, lambda self: int(self._value("h", 0)), notify=changed)
    enabled = Property(bool, lambda self: bool(self._value("enabled", True)), notify=changed)
    clipEnabled = Property(bool, lambda self: bool(self._value("clipEnabled", False)), notify=changed)
    clipX = Property(int, lambda self: int(self._value("clipX", 0)), notify=changed)
    clipY = Property(int, lambda self: int(self._value("clipY", 0)), notify=changed)
    clipW = Property(int, lambda self: int(self._value("clipW", 0)), notify=changed)
    clipH = Property(int, lambda self: int(self._value("clipH", 0)), notify=changed)
    kind = Property(str, lambda self: "progress", constant=True)
    ratio = Property(float, lambda self: float(self._value("ratio", 0.0)), notify=changed)
    text = Property(str, lambda self: str(self._value("text", "")), notify=changed)

    def sync(self, data: dict[str, Any], *, ratio: float, text: str) -> None:
        next_state = {
            "key": str(data.get("key") or ""),
            "name": str(data.get("name") or ""),
            "x": int(data.get("x") or 0),
            "y": int(data.get("y") or 0),
            "w": int(data.get("w") or 0),
            "h": int(data.get("h") or 0),
            "enabled": bool(data.get("enabled", True)),
            "clipEnabled": bool(data.get("clipEnabled", False)),
            "clipX": int(data.get("clipX") or 0),
            "clipY": int(data.get("clipY") or 0),
            "clipW": int(data.get("clipW") or 0),
            "clipH": int(data.get("clipH") or 0),
            "kind": "progress",
            "ratio": max(0.0, min(1.0, float(ratio))),
            "text": str(text),
        }
        if next_state == self._state:
            return
        self._state = next_state
        self.changed.emit()

    def sync_value(self, *, ratio: float, text: str) -> None:
        ratio = max(0.0, min(1.0, float(ratio)))
        text = str(text)
        if abs(float(self._state.get("ratio", 0.0)) - ratio) <= 1e-9 and self._state.get("text", "") == text:
            return
        self._state["ratio"] = ratio
        self._state["text"] = text
        self.changed.emit()


class StaticCardModel(QAbstractListModel):
    """Stable card model that updates roles without resetting steady-state delegates."""

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
        return {role: QByteArray(key.encode("ascii")) for role, key in self._KEYS.items()}

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802, B008
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        key = self._KEYS.get(role)
        return self._rows[index.row()].get(key) if key is not None else None

    @staticmethod
    def _topology_key(row: dict[str, Any]) -> object:
        return row.get("_sourceKey")

    def replace(self, rows: list[dict[str, Any]]) -> bool:
        same_topology = (
            len(rows) == len(self._rows)
            and all(
                self._topology_key(old) == self._topology_key(new)
                and self._topology_key(new) is not None
                for old, new in zip(self._rows, rows)
            )
        )
        if not same_topology:
            if rows == self._rows:
                return False
            self.beginResetModel()
            self._rows = rows
            self.endResetModel()
            return True

        changed = False
        for row_index, (old, new) in enumerate(zip(self._rows, rows)):
            roles = [role for role, key in self._KEYS.items() if old.get(key) != new.get(key)]
            self._rows[row_index] = new
            if not roles:
                continue
            index = self.index(row_index, 0)
            self.dataChanged.emit(index, index, roles)
            changed = True
        return changed


class StaticQmlBridge(QObject):
    """Expose QWidget business state to one persistent Quick presentation tree.

    Stable UI topology is cached. High-frequency read-only logs and progress bars use
    long-lived QObject controls, so their updates notify only the affected QML binding.
    """

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
        self._root_controls: list[Any] = []
        self._targets: dict[str, QObject] = {}
        self._card_frames: list[QFrame] = []
        self._connected_widget_ids: set[int] = set()
        self._runtime_sources_bound = False
        self._local_input_commit_ids: set[int] = set()

        self._structure_dirty = True
        self._cached_frames: list[QFrame] = []
        self._cached_controls: dict[int, tuple[QWidget, ...]] = {}
        self._cached_atomic_blocked: set[int] = set()
        self._batch_job_ids: tuple[str, ...] | None = None
        self._read_only_text_controls: dict[int, _ReadOnlyTextControl] = {}
        self._progress_controls: dict[int, _ProgressControl] = {}

        self._sakura_url = "data:image/png;base64," + _SAKURA_PNG_B64
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(_REFRESH_FRAME_MS)
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
        self._rebuild_structure_cache()

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
            return {"fontFamily": "Microsoft YaHei UI", "fontSize": 13, "fontWeight": 400}

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

    def _nearest_glass(self, widget: QWidget, frames: set[QFrame]) -> QFrame | None:
        try:
            current: QWidget | None = widget
            while current is not None and current is not self.window:
                if isinstance(current, QFrame) and current in frames:
                    return current
                current = current.parentWidget()
        except RuntimeError:
            return None
        return None

    def _has_atomic_ancestor(self, widget: QWidget, stop: QWidget) -> bool:
        if id(widget) in self._cached_atomic_blocked:
            return True
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

    @staticmethod
    def _plain_text(widget: QPlainTextEdit) -> str:
        try:
            text = widget.toPlainText()
        except RuntimeError:
            return ""
        if len(text) > _MAX_MIRRORED_TEXT:
            return text[-_MAX_MIRRORED_TEXT:]
        return text

    def _read_only_text_control(self, widget: QPlainTextEdit, data: dict[str, Any]) -> _ReadOnlyTextControl:
        identity = id(widget)
        control = self._read_only_text_controls.get(identity)
        if control is None:
            control = _ReadOnlyTextControl(self)
            self._read_only_text_controls[identity] = control
        control.sync(
            data,
            text=self._plain_text(widget),
            wrap=(widget.lineWrapMode() != QPlainTextEdit.LineWrapMode.NoWrap),
        )
        return control

    def _sync_read_only_text(self, widget: QPlainTextEdit) -> None:
        control = self._read_only_text_controls.get(id(widget))
        if control is not None:
            control.sync_text(self._plain_text(widget))

    @staticmethod
    def _progress_values(widget: QProgressBar) -> tuple[float, str]:
        span = max(1, int(widget.maximum()) - int(widget.minimum()))
        ratio = (int(widget.value()) - int(widget.minimum())) / span
        return max(0.0, min(1.0, float(ratio))), widget.text()

    def _progress_control(self, widget: QProgressBar, data: dict[str, Any]) -> _ProgressControl:
        identity = id(widget)
        control = self._progress_controls.get(identity)
        if control is None:
            control = _ProgressControl(self)
            self._progress_controls[identity] = control
        ratio, text = self._progress_values(widget)
        control.sync(data, ratio=ratio, text=text)
        return control

    def _sync_progress(self, widget: QProgressBar) -> None:
        control = self._progress_controls.get(id(widget))
        if control is None:
            return
        try:
            ratio, text = self._progress_values(widget)
        except RuntimeError:
            return
        control.sync_value(ratio=ratio, text=text)

    def _uses_live_controls(self, origin: QWidget) -> bool:
        if origin is self.window:
            return True
        return any(origin is frame for frame in self._cached_frames)

    @staticmethod
    def _control_position(control: Any) -> tuple[int, int]:
        if isinstance(control, dict):
            return int(control.get("y") or 0), int(control.get("x") or 0)
        try:
            return int(control.property("y") or 0), int(control.property("x") or 0)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return 0, 0

    def _scroll_clip_data(self, widget: QWidget, origin: QWidget) -> dict[str, Any] | None:
        clip: tuple[int, int, int, int] | None = None
        try:
            current = widget.parentWidget()
            while current is not None and current is not origin:
                if isinstance(current, QAbstractScrollArea):
                    viewport = current.viewport()
                    point = viewport.mapTo(origin, QPoint(0, 0))
                    left = int(point.x())
                    top = int(point.y())
                    right = left + int(viewport.width())
                    bottom = top + int(viewport.height())
                    if clip is None:
                        clip = (left, top, right, bottom)
                    else:
                        clip = (
                            max(clip[0], left),
                            max(clip[1], top),
                            min(clip[2], right),
                            min(clip[3], bottom),
                        )
                    if clip[2] <= clip[0] or clip[3] <= clip[1]:
                        return None
                current = current.parentWidget()
        except RuntimeError:
            return None

        if clip is None:
            return None
        return {
            "clipEnabled": True,
            "clipX": clip[0],
            "clipY": clip[1],
            "clipW": max(0, clip[2] - clip[0]),
            "clipH": max(0, clip[3] - clip[1]),
        }

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
            "clipEnabled": False,
        }
        clip_data = self._scroll_clip_data(widget, origin)
        if clip_data is not None:
            data.update(clip_data)
        data.update(self._font_data(widget))
        return data

    def _snapshot_widget(self, widget: QWidget, origin: QWidget) -> Any:
        if self._has_atomic_ancestor(widget, origin):
            return None

        live_controls = self._uses_live_controls(origin)
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
                wrap = widget.lineWrapMode() != QPlainTextEdit.LineWrapMode.NoWrap
                if widget.isReadOnly():
                    if live_controls:
                        return self._read_only_text_control(widget, data)
                    data.update(
                        kind="textedit",
                        text=self._plain_text(widget),
                        readOnly=True,
                        wrap=wrap,
                    )
                    return data
                data.update(
                    kind="textedit",
                    text=self._plain_text(widget),
                    readOnly=False,
                    wrap=wrap,
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
                data.update(kind="checkbox", text=widget.text(), checked=bool(widget.isChecked()))
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
                if live_controls:
                    return self._progress_control(widget, data)
                ratio, text = self._progress_values(widget)
                data.update(kind="progress", ratio=ratio, text=text)
                return data

            if isinstance(widget, QLabel):
                data = self._base(widget, origin)
                if data is None:
                    return None
                text = widget.text()
                kind = "badge" if widget.objectName() in {"phaseBadge", "appVersionBadge"} else "label"
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

    def _rebuild_structure_cache(self) -> None:
        try:
            widgets = self.window.findChildren(QWidget)
        except RuntimeError:
            widgets = []

        frames_list = self._glass_frames()
        frames = set(frames_list)
        controls: dict[int, list[QWidget]] = {id(self.window): []}
        for frame in frames_list:
            controls[id(frame)] = []

        atomic_blocked: set[int] = set()
        for widget in widgets:
            if isinstance(widget, QFrame) and widget in frames:
                continue
            try:
                current = widget.parentWidget()
                nearest: QFrame | None = None
                blocked = False
                while current is not None and current is not self.window:
                    if isinstance(current, _ATOMIC_TYPES):
                        blocked = True
                        break
                    if nearest is None and isinstance(current, QFrame) and current in frames:
                        nearest = current
                    current = current.parentWidget()
            except RuntimeError:
                continue
            if blocked:
                atomic_blocked.add(id(widget))
                continue
            origin = nearest if nearest is not None else self.window
            controls.setdefault(id(origin), []).append(widget)

        live_widget_ids = {id(widget) for widget in widgets}
        for pool in (self._read_only_text_controls, self._progress_controls):
            for identity, control in tuple(pool.items()):
                if identity in live_widget_ids:
                    continue
                pool.pop(identity, None)
                control.deleteLater()

        self._cached_frames = frames_list
        self._cached_controls = {origin_id: tuple(items) for origin_id, items in controls.items()}
        self._cached_atomic_blocked = atomic_blocked
        self._targets.clear()
        self._structure_dirty = False
        self._connect_widget_sources(widgets)

    def _controls_for(self, origin: QWidget, frames: set[QFrame]) -> list[Any]:
        controls: list[Any] = []
        cached = self._cached_controls.get(id(origin))
        if cached is None:
            try:
                descendants = origin.findChildren(QWidget)
            except RuntimeError:
                return controls
            candidates: tuple[QWidget, ...] = tuple(descendants)
            cached_scope = False
        else:
            candidates = cached
            cached_scope = True

        for widget in candidates:
            if not cached_scope:
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
        controls.sort(key=self._control_position)
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
        structure_changed = self._structure_dirty
        if self._structure_dirty:
            self._rebuild_structure_cache()

        frames_list = list(self._cached_frames)
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
                    "_sourceKey": id(frame),
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

        root_controls = self._controls_for(self.window, frames)
        root_changed = root_controls != self._root_controls
        if root_changed:
            self._root_controls = root_controls

        self._card_frames = row_frames
        card_changed = self.card_model.replace(rows)
        if structure_changed or card_changed or root_changed:
            self.sceneChanged.emit()

    def schedule_refresh(self, *_args: object) -> None:
        if not self._active or self._refresh_timer.isActive():
            return
        self._refresh_timer.start()

    def schedule_structure_refresh(self, *_args: object) -> None:
        self._structure_dirty = True
        self.schedule_refresh()

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

    @staticmethod
    def _job_ids(jobs: object) -> tuple[str, ...] | None:
        if not isinstance(jobs, (list, tuple)):
            return None
        output: list[str] = []
        for job in jobs:
            job_id = getattr(job, "job_id", None)
            if job_id is None:
                return None
            output.append(str(job_id))
        return tuple(output)

    @staticmethod
    def _button_changes_structure(widget: QAbstractButton) -> bool:
        try:
            if bool(widget.property("quickStructureMutation")):
                return True
            name = widget.objectName()
            text = widget.text().strip()
        except RuntimeError:
            return False
        if name == "batchUrlRemoveButton":
            return True
        if name == "batchToolbarButton" and text in {"+ 添加链接", "批量粘贴"}:
            return True
        return text in {"展开详情 / 日志", "收起详情 / 日志"}

    def _on_batch_jobs_changed(self, jobs: object = None, *_args: object) -> None:
        job_ids = self._job_ids(jobs)
        if job_ids is None:
            self.schedule_structure_refresh()
            return
        if self._batch_job_ids != job_ids:
            self._batch_job_ids = job_ids
            self.schedule_structure_refresh()
            return
        self.schedule_refresh()

    def _connect_state_sources(self) -> None:
        mode_stack = getattr(self.window, "mode_stack", None)
        self._connect(getattr(mode_stack, "currentChanged", None), self.schedule_refresh)

        for source_name in ("runner", "execution_runner"):
            source = getattr(self.window, source_name, None)
            for signal_name in ("result_updated", "running_changed", "completed", "failed"):
                self._connect(getattr(source, signal_name, None), self.schedule_refresh)

        batch_workspace = getattr(self.window, "batch_workspace", None)
        current_jobs = getattr(batch_workspace, "_jobs", None)
        self._batch_job_ids = self._job_ids(current_jobs)
        controller = getattr(batch_workspace, "controller", None)
        self._connect(getattr(controller, "jobs_changed", None), self._on_batch_jobs_changed)
        for signal_name in ("summary_changed", "running_changed", "state_changed"):
            self._connect(getattr(controller, signal_name, None), self.schedule_refresh)

    def _connect_widget_sources(self, widgets: list[QWidget] | None = None) -> None:
        if widgets is None:
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
                if widget.isReadOnly():
                    self._connect(
                        widget.textChanged,
                        lambda *_args, source=widget: self._sync_read_only_text(source),
                    )
                else:
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
                callback = (
                    self.schedule_structure_refresh
                    if self._button_changes_structure(widget)
                    else self.schedule_refresh
                )
                self._connect(widget.clicked, callback)
            elif isinstance(widget, QProgressBar):
                self._connect(
                    widget.valueChanged,
                    lambda *_args, source=widget: self._sync_progress(source),
                )
                self._connect(getattr(widget, "rangeChanged", None), self.schedule_refresh)
            elif isinstance(widget, QTabWidget):
                self._connect(widget.currentChanged, self.schedule_refresh)

            if isinstance(widget, QAbstractScrollArea):
                if isinstance(widget, QPlainTextEdit) and widget.isReadOnly():
                    continue
                try:
                    vertical = widget.verticalScrollBar()
                    horizontal = widget.horizontalScrollBar()
                except RuntimeError:
                    vertical = None
                    horizontal = None
                for bar in (vertical, horizontal):
                    if bar is None:
                        continue
                    self._connect(bar.valueChanged, self.schedule_refresh)
                    self._connect(bar.rangeChanged, self.schedule_refresh)

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
