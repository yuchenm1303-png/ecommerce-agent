from __future__ import annotations

from collections import deque
from pathlib import Path
import re
import sys
from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QEvent,
    QModelIndex,
    QObject,
    QPoint,
    Property,
    Qt,
    QUrl,
    Signal,
    Slot,
    QTimer,
)
from PySide6.QtGui import QDesktopServices
from PySide6.QtQml import QQmlComponent
from PySide6.QtQuick import QQuickItem
from PySide6.QtWidgets import QBoxLayout, QFrame, QMainWindow, QSizePolicy, QWidget

from .batch_log_buffer import BATCH_LOG_PREVIEW_CHARS, display_log_line, log_buffer


_JOB_LOG_LINE = re.compile(r"^\[(JOB-\d+)(?:\s*·[^\]]+)?\]\s?(.*)$")
_TERMINAL = {"DONE", "REVIEW", "FAILED", "STOPPED"}
_STATUS_LABELS = {
    "QUEUED": "排队",
    "CAPTURING": "采集商品",
    "UNDERSTANDING": "识别商品",
    "SELECTING_VERTICAL": "选择类目",
    "SELECTING_BRAND": "选择品牌",
    "RESOLVING": "解析字段",
    "READY": "准备完成",
    "FILLING": "填写中",
    "UPLOADING_IMAGES": "上传图片",
    "SAVING": "保存中",
    "VERIFYING": "验证中",
    "DONE": "完成",
    "REVIEW": "需要复核",
    "FAILED": "失败",
    "STOPPED": "已停止",
}
_STATUS_COLORS = {
    "READY": ("#b4f1cf", "#3d28696a"),
    "DONE": ("#b4f1cf", "#3d28696a"),
    "REVIEW": ("#ffe0a0", "#3dbe8425"),
    "FAILED": ("#ffb2c0", "#40be3f57"),
    "STOPPED": ("#ffe0a0", "#38be8425"),
}
_PHASES = (
    ("SOURCE", 8),
    ("PRODUCT", 25),
    ("VERTICAL", 42),
    ("BRAND", 58),
    ("RESOLVE", 76),
    ("EXECUTE", 82),
    ("VERIFY", 94),
)

_QUICK_BATCH_QML_URL = QUrl("inmemory:/QuickBatchList.qml")
_QUICK_BATCH_QML = r'''
import QtQuick
import QtQuick.Controls

Item {
    id: root
    objectName: "quickBatchList"
    x: quickBatchList.x
    y: quickBatchList.y
    width: quickBatchList.width
    height: quickBatchList.height
    visible: quickBatchList.active && quickBatchList.cardAttached && width > 0 && height > 0
    clip: true
    z: 1200

    function buttonFill(kind, hovered, pressed, enabled) {
        if (!enabled)
            return Qt.rgba(0, 0, 0, 32/255)
        if (kind === "primary")
            return Qt.rgba(1, 1, 1, (hovered && !pressed ? 72 : 48)/255)
        if (kind === "danger")
            return Qt.rgba(100/255, 18/255, 38/255, (hovered && !pressed ? 118 : 86)/255)
        return Qt.rgba(0, 0, 0, (hovered && !pressed ? 92 : 62)/255)
    }

    ListView {
        id: jobsView
        anchors.fill: parent
        model: quickBatchList
        spacing: 8
        clip: true
        reuseItems: true
        cacheBuffer: Math.max(height, 640)
        boundsBehavior: Flickable.StopAtBounds
        flickDeceleration: 2600
        maximumFlickVelocity: 5600
        interactive: true

        ScrollBar.vertical: ScrollBar {
            policy: ScrollBar.AsNeeded
            width: 6
        }

        delegate: Rectangle {
            id: jobCard
            required property string jobId
            required property string productText
            required property string urlText
            required property int progressValue
            required property string statusText
            required property string statusForeground
            required property string statusBackground
            required property string phaseText
            required property string metaText
            required property string detailText
            required property string errorText
            required property string logPreview
            required property string detailsText
            required property string logText
            required property bool expanded
            required property bool canOpenUrl
            required property bool canOpenDir
            required property bool canFill
            required property bool canStop
            required property bool canDelete

            width: ListView.view ? ListView.view.width : 0
            height: body.implicitHeight + 16
            radius: 12
            color: Qt.rgba(13/255, 29/255, 52/255, 82/255)
            border.width: 1
            border.color: Qt.rgba(1, 1, 1, 30/255)

            Column {
                id: body
                x: 12
                y: 8
                width: Math.max(0, parent.width - 24)
                spacing: 5

                Item {
                    width: parent.width
                    height: 31

                    Column {
                        x: 0
                        y: 0
                        width: Math.max(120, parent.width - 210)
                        height: parent.height
                        spacing: 0
                        Text {
                            width: parent.width
                            height: 13
                            text: jobCard.jobId + " · OWNED PRODUCT TASK"
                            color: Qt.rgba(218/255, 232/255, 250/255, 150/255)
                            font.family: "Microsoft YaHei UI"
                            font.pixelSize: 9
                            font.weight: Font.DemiBold
                            elide: Text.ElideRight
                        }
                        Text {
                            width: parent.width
                            height: 18
                            text: jobCard.productText
                            color: "white"
                            font.family: "Microsoft YaHei UI"
                            font.pixelSize: 13
                            font.weight: Font.DemiBold
                            elide: Text.ElideRight
                            verticalAlignment: Text.AlignVCenter
                        }
                    }

                    Text {
                        x: Math.max(0, parent.width - 198)
                        width: 52
                        height: parent.height
                        text: String(jobCard.progressValue) + "%"
                        color: Qt.rgba(1, 1, 1, 210/255)
                        font.family: "Microsoft YaHei UI"
                        font.pixelSize: 11
                        font.weight: Font.Bold
                        horizontalAlignment: Text.AlignRight
                        verticalAlignment: Text.AlignVCenter
                    }

                    Rectangle {
                        x: Math.max(0, parent.width - 136)
                        width: 136
                        height: 24
                        y: 3
                        radius: 9
                        color: jobCard.statusBackground
                        border.width: 1
                        border.color: Qt.rgba(1, 1, 1, 26/255)
                        Text {
                            anchors.fill: parent
                            text: jobCard.statusText
                            color: jobCard.statusForeground
                            font.family: "Microsoft YaHei UI"
                            font.pixelSize: 10
                            font.weight: Font.DemiBold
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                            elide: Text.ElideRight
                        }
                    }
                }

                Text {
                    width: parent.width
                    height: 18
                    text: jobCard.urlText
                    color: Qt.rgba(1, 1, 1, 150/255)
                    font.family: "Microsoft YaHei UI"
                    font.pixelSize: 10
                    elide: Text.ElideMiddle
                    verticalAlignment: Text.AlignVCenter
                }

                Text {
                    width: parent.width
                    height: 18
                    text: jobCard.phaseText
                    color: Qt.rgba(192/255, 222/255, 241/255, 185/255)
                    font.family: "Microsoft YaHei UI"
                    font.pixelSize: 9
                    elide: Text.ElideRight
                    verticalAlignment: Text.AlignVCenter
                }

                Item {
                    width: parent.width
                    height: 5
                    Rectangle {
                        anchors.fill: parent
                        radius: 2.5
                        color: Qt.rgba(1, 1, 1, 22/255)
                    }
                    Rectangle {
                        width: parent.width * Math.max(0, Math.min(100, jobCard.progressValue)) / 100.0
                        height: parent.height
                        radius: 2.5
                        color: Qt.rgba(150/255, 220/255, 1, 190/255)
                        Behavior on width { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
                    }
                }

                Text {
                    width: parent.width
                    height: 18
                    text: jobCard.metaText
                    color: Qt.rgba(1, 1, 1, 150/255)
                    font.family: "Microsoft YaHei UI"
                    font.pixelSize: 10
                    elide: Text.ElideRight
                    verticalAlignment: Text.AlignVCenter
                }

                Text {
                    width: parent.width
                    height: 18
                    text: jobCard.detailText
                    color: Qt.rgba(1, 1, 1, 215/255)
                    font.family: "Microsoft YaHei UI"
                    font.pixelSize: 10
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                    verticalAlignment: Text.AlignVCenter
                }

                Rectangle {
                    width: parent.width
                    height: jobCard.errorText.length > 0 ? 25 : 0
                    visible: height > 0
                    radius: 7
                    color: Qt.rgba(180/255, 45/255, 72/255, 45/255)
                    Text {
                        anchors.fill: parent
                        anchors.leftMargin: 8
                        anchors.rightMargin: 8
                        text: jobCard.errorText
                        color: "#ffc1cc"
                        font.family: "Microsoft YaHei UI"
                        font.pixelSize: 9
                        elide: Text.ElideRight
                        verticalAlignment: Text.AlignVCenter
                    }
                }

                Item {
                    width: parent.width
                    height: 18
                    Text {
                        x: 0
                        width: 38
                        height: parent.height
                        text: "LIVE"
                        color: Qt.rgba(190/255, 224/255, 247/255, 165/255)
                        font.family: "Microsoft YaHei UI"
                        font.pixelSize: 9
                        font.weight: Font.DemiBold
                        verticalAlignment: Text.AlignVCenter
                    }
                    Text {
                        x: 42
                        width: Math.max(0, parent.width - 42)
                        height: parent.height
                        text: jobCard.logPreview.length > 0 ? jobCard.logPreview : "等待任务日志…"
                        color: Qt.rgba(1, 1, 1, 145/255)
                        font.family: "Consolas"
                        font.pixelSize: 9
                        elide: Text.ElideRight
                        verticalAlignment: Text.AlignVCenter
                    }
                }

                Row {
                    width: parent.width
                    height: 28
                    spacing: 7

                    Button {
                        width: 86; height: 28; text: "商品链接"; enabled: jobCard.canOpenUrl; hoverEnabled: true
                        onClicked: quickBatchList.openUrl(jobCard.jobId)
                        background: Rectangle { radius: 7; color: root.buttonFill("quiet", parent.hovered, parent.down, parent.enabled); border.width: 1; border.color: Qt.rgba(1,1,1,20/255) }
                        contentItem: Text { text: parent.text; color: parent.enabled ? "white" : Qt.rgba(1,1,1,70/255); font.family: "Microsoft YaHei UI"; font.pixelSize: 10; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                    }
                    Button {
                        width: 78; height: 28; text: "Job 目录"; enabled: jobCard.canOpenDir; hoverEnabled: true
                        onClicked: quickBatchList.openDirectory(jobCard.jobId)
                        background: Rectangle { radius: 7; color: root.buttonFill("quiet", parent.hovered, parent.down, parent.enabled); border.width: 1; border.color: Qt.rgba(1,1,1,20/255) }
                        contentItem: Text { text: parent.text; color: parent.enabled ? "white" : Qt.rgba(1,1,1,70/255); font.family: "Microsoft YaHei UI"; font.pixelSize: 10; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                    }
                    Button {
                        width: 82; height: 28; text: "详情窗口"; hoverEnabled: true
                        onClicked: quickBatchList.openDetails(jobCard.jobId)
                        background: Rectangle { radius: 7; color: root.buttonFill("quiet", parent.hovered, parent.down, parent.enabled); border.width: 1; border.color: Qt.rgba(1,1,1,20/255) }
                        contentItem: Text { text: parent.text; color: "white"; font.family: "Microsoft YaHei UI"; font.pixelSize: 10; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                    }
                    Item { width: Math.max(0, parent.width - 86 - 78 - 82 - 112 - 21); height: 1 }
                    Button {
                        width: 112; height: 28; text: jobCard.expanded ? "收起详情 / 日志" : "展开详情 / 日志"; hoverEnabled: true
                        onClicked: quickBatchList.toggleExpanded(jobCard.jobId)
                        background: Rectangle { radius: 7; color: root.buttonFill("quiet", parent.hovered, parent.down, parent.enabled); border.width: 1; border.color: Qt.rgba(1,1,1,20/255) }
                        contentItem: Text { text: parent.text; color: "white"; font.family: "Microsoft YaHei UI"; font.pixelSize: 10; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                    }
                }

                Rectangle {
                    width: parent.width
                    height: 36
                    radius: 8
                    color: Qt.rgba(0, 0, 0, 42/255)
                    border.width: 1
                    border.color: Qt.rgba(1, 1, 1, 18/255)

                    Text {
                        x: 9; width: 74; height: parent.height
                        text: "JOB CONTROL"
                        color: Qt.rgba(190/255, 224/255, 247/255, 165/255)
                        font.family: "Microsoft YaHei UI"; font.pixelSize: 9; font.weight: Font.DemiBold
                        verticalAlignment: Text.AlignVCenter
                    }
                    Text {
                        x: 88; width: Math.max(20, parent.width - 88 - 254); height: parent.height
                        text: "独立任务控制"
                        color: Qt.rgba(1,1,1,135/255)
                        font.family: "Microsoft YaHei UI"; font.pixelSize: 9
                        verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
                    }
                    Button {
                        x: parent.width - 246; y: 4; width: 82; height: 28; text: "单独填写"; enabled: jobCard.canFill; hoverEnabled: true
                        onClicked: quickBatchList.startFill(jobCard.jobId)
                        background: Rectangle { radius: 7; color: root.buttonFill("primary", parent.hovered, parent.down, parent.enabled); border.width: 1; border.color: Qt.rgba(1,1,1,24/255) }
                        contentItem: Text { text: parent.text; color: parent.enabled ? "white" : Qt.rgba(1,1,1,70/255); font.family: "Microsoft YaHei UI"; font.pixelSize: 10; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                    }
                    Button {
                        x: parent.width - 158; y: 4; width: 68; height: 28; text: "停止"; enabled: jobCard.canStop; hoverEnabled: true
                        onClicked: quickBatchList.stopJob(jobCard.jobId)
                        background: Rectangle { radius: 7; color: root.buttonFill("quiet", parent.hovered, parent.down, parent.enabled); border.width: 1; border.color: Qt.rgba(1,1,1,20/255) }
                        contentItem: Text { text: parent.text; color: parent.enabled ? "white" : Qt.rgba(1,1,1,70/255); font.family: "Microsoft YaHei UI"; font.pixelSize: 10; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                    }
                    Button {
                        x: parent.width - 84; y: 4; width: 76; height: 28; text: "删除"; enabled: jobCard.canDelete; hoverEnabled: true
                        onClicked: quickBatchList.deleteJob(jobCard.jobId)
                        background: Rectangle { radius: 7; color: root.buttonFill("danger", parent.hovered, parent.down, parent.enabled); border.width: 1; border.color: Qt.rgba(1,1,1,20/255) }
                        contentItem: Text { text: parent.text; color: parent.enabled ? "white" : Qt.rgba(1,1,1,70/255); font.family: "Microsoft YaHei UI"; font.pixelSize: 10; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                    }
                }

                Loader {
                    width: parent.width
                    height: jobCard.expanded ? 216 : 0
                    active: height > 0
                    visible: height > 0
                    clip: true
                    Behavior on height {
                        NumberAnimation { duration: 150; easing.type: Easing.InOutCubic }
                    }
                    sourceComponent: Component {
                        Rectangle {
                            anchors.fill: parent
                            radius: 9
                            color: Qt.rgba(5/255, 15/255, 30/255, 72/255)
                            border.width: 1
                            border.color: Qt.rgba(1,1,1,18/255)
                            Text {
                                x: 10; y: 8; width: parent.width - 20; height: 70
                                text: jobCard.detailsText
                                color: Qt.rgba(1,1,1,175/255)
                                font.family: "Microsoft YaHei UI"; font.pixelSize: 9
                                wrapMode: Text.Wrap; elide: Text.ElideRight; maximumLineCount: 4
                            }
                            ScrollView {
                                x: 8; y: 82; width: parent.width - 16; height: 126
                                clip: true
                                TextArea {
                                    text: jobCard.logText
                                    readOnly: true
                                    wrapMode: TextEdit.NoWrap
                                    color: Qt.rgba(1,1,1,170/255)
                                    font.family: "Consolas"; font.pixelSize: 9
                                    background: Rectangle { color: Qt.rgba(0,0,0,35/255); radius: 6 }
                                }
                            }
                        }
                    }
                }
            }
        }

        footer: Item { width: 1; height: 2 }

        Text {
            anchors.centerIn: parent
            visible: jobsView.count === 0
            width: Math.max(120, parent.width - 40)
            text: "尚未创建商品任务\n批量准备后，每个链接会生成独立任务卡、owned tab 状态、实时进度和独立日志。"
            color: Qt.rgba(1,1,1,130/255)
            font.family: "Microsoft YaHei UI"
            font.pixelSize: 11
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
        }
    }
}
'''


class QuickBatchList(QAbstractListModel):
    """Quick-owned Batch task list with one native ListView scroll transform.

    BatchController remains the business-state owner. The legacy QWidget QScrollArea
    is retained only as fallback; while Quick owns presentation it is detached from
    the main window tree so StaticQmlBridge cannot mirror or scroll its descendants.
    """

    _BASE = int(Qt.ItemDataRole.UserRole)
    _ROLE_NAMES = (
        "jobId",
        "productText",
        "urlText",
        "progressValue",
        "statusText",
        "statusForeground",
        "statusBackground",
        "phaseText",
        "metaText",
        "detailText",
        "errorText",
        "logPreview",
        "detailsText",
        "logText",
        "expanded",
        "canOpenUrl",
        "canOpenDir",
        "canFill",
        "canStop",
        "canDelete",
    )
    _ROLES = {
        name: int(Qt.ItemDataRole.UserRole) + index + 1
        for index, name in enumerate(_ROLE_NAMES)
    }
    _ROLE_KEYS = {role: name for name, role in _ROLES.items()}

    presentationChanged = Signal()

    def __init__(self, window: QMainWindow, visual: Any, engine: Any, parent: QObject) -> None:
        super().__init__(parent)
        self.window = window
        self.visual = visual
        self.engine = engine
        self.workspace = getattr(window, "batch_workspace", None)
        self.controller = getattr(self.workspace, "controller", None)
        self.scroll = getattr(self.workspace, "job_scroll", None)
        self._card_frame = self._find_card_frame()
        self._card_item: QQuickItem | None = None
        self._host_item: QQuickItem | None = None
        self._item: QQuickItem | None = None
        self._component: QQmlComponent | None = None
        self._component_failed = False

        self._rows: list[dict[str, Any]] = []
        self._expanded: set[str] = set()
        self._logs: dict[str, deque[str]] = {}
        self._dirty_log_text: set[str] = set()
        self._log_text_timer = QTimer(self)
        self._log_text_timer.setSingleShot(True)
        self._log_text_timer.setInterval(250)
        self._log_text_timer.timeout.connect(self._flush_log_text)

        self._active = False
        self._card_attached = False
        self._x = 0
        self._y = 0
        self._width = 0
        self._height = 0

        self._placeholder: QWidget | None = None
        self._layout: QBoxLayout | None = None
        self._layout_index = -1
        self._layout_stretch = 1
        self._legacy_parent: QWidget | None = None

        self._load_existing_logs()
        if self.controller is not None:
            jobs_changed = getattr(self.controller, "jobs_changed", None)
            log_signal = getattr(self.controller, "log", None)
            if jobs_changed is not None and hasattr(jobs_changed, "connect"):
                jobs_changed.connect(self.sync_jobs)
            if log_signal is not None and hasattr(log_signal, "connect"):
                log_signal.connect(self.append_log)
        self.sync_jobs(list(getattr(self.workspace, "_jobs", []) or []))

    def roleNames(self) -> dict[int, QByteArray]:  # noqa: N802
        return {role: QByteArray(name.encode("ascii")) for role, name in self._ROLE_KEYS.items()}

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802, B008
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        key = self._ROLE_KEYS.get(role)
        return self._rows[index.row()].get(key) if key is not None else None

    def _get_active(self) -> bool:
        return self._active

    active = Property(bool, _get_active, notify=presentationChanged)

    def _get_card_attached(self) -> bool:
        return self._card_attached

    cardAttached = Property(bool, _get_card_attached, notify=presentationChanged)

    x = Property(int, lambda self: self._x, notify=presentationChanged)
    y = Property(int, lambda self: self._y, notify=presentationChanged)
    width = Property(int, lambda self: self._width, notify=presentationChanged)
    height = Property(int, lambda self: self._height, notify=presentationChanged)

    def _find_card_frame(self) -> QFrame | None:
        scroll = self.scroll
        glass = getattr(self.visual, "_glass", None)
        if not isinstance(scroll, QWidget) or not isinstance(glass, dict):
            return None
        frames = {frame for frame in glass if isinstance(frame, QFrame)}
        try:
            current = scroll.parentWidget()
            while current is not None and current is not self.window:
                if isinstance(current, QFrame) and current in frames:
                    return current
                current = current.parentWidget()
        except RuntimeError:
            return None
        return None

    def _load_existing_logs(self) -> None:
        cards = getattr(self.workspace, "_job_cards", None)
        if not isinstance(cards, dict):
            return
        for job_id, card in cards.items():
            getter = getattr(card, "log_text", None)
            if not callable(getter):
                continue
            try:
                lines = str(getter() or "").splitlines()
            except RuntimeError:
                continue
            if lines:
                self._logs[str(job_id)] = log_buffer(lines)

    @staticmethod
    def _phase_text(progress: int) -> str:
        pieces: list[str] = []
        for label, threshold in _PHASES:
            marker = "●" if progress >= threshold else "○"
            pieces.append(f"{marker} {label}")
        return "   ".join(pieces)

    @staticmethod
    def _details_text(job: Any) -> str:
        return "\n".join(
            (
                f"Supplier URL: {getattr(job, 'product_url', '') or '—'}",
                f"Makro targetId: {getattr(job, 'makro_target_id', '') or '—'}",
                f"Run directory: {getattr(job, 'run_dir', '') or '—'}",
                f"Execution report: {getattr(job, 'execution_report', '') or '—'}",
                f"Updated: {getattr(job, 'updated_at', '') or '—'}",
                f"Error / review reason: {getattr(job, 'error', '') or '—'}",
            )
        )

    def _snapshot_job(self, job: Any, *, refresh_log_text: bool = False) -> dict[str, Any]:
        job_id = str(getattr(job, "job_id", ""))
        status = str(getattr(job, "status", "QUEUED") or "QUEUED")
        progress = max(0, min(100, int(getattr(job, "progress", 0) or 0)))
        foreground, background = _STATUS_COLORS.get(status, ("#ccecff", "#386997c9"))
        logs = self._logs.get(job_id)
        log_preview = logs[-1] if logs else ""
        expanded = job_id in self._expanded
        run_dir = str(getattr(job, "run_dir", "") or "")
        return {
            "jobId": job_id,
            "productText": str(getattr(job, "product_name", "") or "等待商品信息"),
            "urlText": str(getattr(job, "product_url", "") or "—"),
            "progressValue": progress,
            "statusText": _STATUS_LABELS.get(status, status),
            "statusForeground": foreground,
            "statusBackground": background,
            "phaseText": self._phase_text(progress),
            "metaText": (
                f"Vertical  {getattr(job, 'vertical', '') or '—'}    ·    "
                f"Brand  {getattr(job, 'brand', '') or '—'}    ·    "
                f"READY  {int(getattr(job, 'ready', 0) or 0)}    ·    "
                f"BLOCKED  {int(getattr(job, 'blocked', 0) or 0)}    ·    "
                f"Required  {int(getattr(job, 'required_blocked', 0) or 0)}    ·    "
                f"Images  {int(getattr(job, 'image_count', 0) or 0)}"
            ),
            "detailText": (
                f"{_STATUS_LABELS.get(status, status)}  ·  "
                f"{str(getattr(job, 'stage_detail', '') or _STATUS_LABELS.get(status, status))}"
            ),
            "errorText": str(getattr(job, "error", "") or ""),
            "logPreview": log_preview,
            "detailsText": self._details_text(job),
            "logText": (
                "\n".join(logs)
                if refresh_log_text and expanded and logs
                else self._existing_log_text(job_id) if expanded else ""
            ),
            "expanded": expanded,
            "canOpenUrl": bool(str(getattr(job, "product_url", "") or "")),
            "canOpenDir": bool(run_dir),
            "canFill": status == "READY",
            "canStop": status not in _TERMINAL and status != "READY",
            "canDelete": status in _TERMINAL or status == "READY",
        }

    @Slot(object)
    def sync_jobs(self, jobs: object) -> None:
        if not isinstance(jobs, (list, tuple)):
            return
        next_rows = [self._snapshot_job(job) for job in jobs]
        same_topology = (
            len(next_rows) == len(self._rows)
            and all(old.get("jobId") == new.get("jobId") for old, new in zip(self._rows, next_rows))
        )
        if not same_topology:
            next_rows = [self._snapshot_job(job, refresh_log_text=True) for job in jobs]
            self.beginResetModel()
            self._rows = next_rows
            self.endResetModel()
            return

        for row_index, (old, new) in enumerate(zip(self._rows, next_rows)):
            changed_roles = [
                self._ROLES[key]
                for key in self._ROLE_NAMES
                if old.get(key) != new.get(key)
            ]
            self._rows[row_index] = new
            if not changed_roles:
                continue
            index = self.index(row_index, 0)
            self.dataChanged.emit(index, index, changed_roles)

    @Slot(str)
    def append_log(self, line: str) -> None:
        clean = display_log_line(line)
        if not clean:
            return
        match = _JOB_LOG_LINE.match(clean)
        if match is None:
            return
        job_id = match.group(1)
        message = match.group(2).strip() or clean
        logs = self._logs.setdefault(job_id, log_buffer())
        logs.append(message)
        row_index = self._row_index(job_id)
        if row_index < 0:
            return
        row = self._rows[row_index]
        changed = [self._ROLES["logPreview"]]
        row["logPreview"] = (
            message if len(message) <= BATCH_LOG_PREVIEW_CHARS else message[: BATCH_LOG_PREVIEW_CHARS - 3] + "..."
        )
        if row.get("expanded"):
            self._dirty_log_text.add(job_id)
            if not self._log_text_timer.isActive():
                self._log_text_timer.start()
        index = self.index(row_index, 0)
        self.dataChanged.emit(index, index, changed)

    def _existing_log_text(self, job_id: str) -> str:
        row_index = self._row_index(job_id)
        if row_index < 0:
            return ""
        return str(self._rows[row_index].get("logText", "") or "")

    def _flush_log_text(self) -> None:
        dirty = tuple(self._dirty_log_text)
        self._dirty_log_text.clear()
        for job_id in dirty:
            row_index = self._row_index(job_id)
            if row_index < 0:
                continue
            row = self._rows[row_index]
            if not row.get("expanded"):
                continue
            row["logText"] = "\n".join(self._logs.get(job_id, ()))
            index = self.index(row_index, 0)
            self.dataChanged.emit(index, index, [self._ROLES["logText"]])

    def _row_index(self, job_id: str) -> int:
        for index, row in enumerate(self._rows):
            if row.get("jobId") == str(job_id):
                return index
        return -1

    def _job(self, job_id: str) -> Any | None:
        jobs = getattr(self.workspace, "_jobs", None)
        if not isinstance(jobs, list):
            return None
        return next((job for job in jobs if str(getattr(job, "job_id", "")) == str(job_id)), None)

    @Slot(str)
    def toggleExpanded(self, job_id: str) -> None:  # noqa: N802
        job_id = str(job_id)
        if job_id in self._expanded:
            self._expanded.remove(job_id)
        else:
            self._expanded.add(job_id)
        row_index = self._row_index(job_id)
        job = self._job(job_id)
        if row_index < 0 or job is None:
            return
        self._dirty_log_text.discard(job_id)
        new = self._snapshot_job(job, refresh_log_text=True)
        self._rows[row_index] = new
        index = self.index(row_index, 0)
        self.dataChanged.emit(
            index,
            index,
            [self._ROLES["expanded"], self._ROLES["logText"]],
        )

    @Slot(str)
    def openUrl(self, job_id: str) -> None:  # noqa: N802
        job = self._job(job_id)
        url = str(getattr(job, "product_url", "") or "") if job is not None else ""
        if url:
            QDesktopServices.openUrl(QUrl(url))

    @Slot(str)
    def openDirectory(self, job_id: str) -> None:  # noqa: N802
        job = self._job(job_id)
        run_dir = str(getattr(job, "run_dir", "") or "") if job is not None else ""
        if not run_dir:
            return
        target = Path(run_dir).expanduser().resolve().parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    @Slot(str)
    def openDetails(self, job_id: str) -> None:  # noqa: N802
        callback = getattr(self.workspace, "_open_job_detail", None)
        if callable(callback):
            callback(str(job_id))

    def _individual_controls(self) -> Any:
        return getattr(self.workspace, "_batch_individual_controls", None)

    @Slot(str)
    def startFill(self, job_id: str) -> None:  # noqa: N802
        callback = getattr(self._individual_controls(), "start_job_execution", None)
        if callable(callback):
            callback(str(job_id))

    @Slot(str)
    def stopJob(self, job_id: str) -> None:  # noqa: N802
        callback = getattr(self._individual_controls(), "stop_job", None)
        if callable(callback):
            callback(str(job_id))

    @Slot(str)
    def deleteJob(self, job_id: str) -> None:  # noqa: N802
        callback = getattr(self._individual_controls(), "_confirm_delete_job", None)
        if callable(callback):
            callback(str(job_id))

    @staticmethod
    def _descendant_items(root: QQuickItem) -> list[QQuickItem]:
        output: list[QQuickItem] = []
        pending = list(root.childItems())
        while pending:
            item = pending.pop()
            output.append(item)
            try:
                pending.extend(item.childItems())
            except RuntimeError:
                continue
        return output

    def _quick_card_item(self) -> QQuickItem | None:
        cached = self._card_item
        if cached is not None:
            try:
                if cached.parentItem() is not None:
                    return cached
            except RuntimeError:
                pass
            self._card_item = None

        host = self._host_item
        frame = self._card_frame
        if host is None or frame is None:
            return None
        try:
            point = frame.mapTo(self.window, QPoint(0, 0))
            expected = (
                float(point.x()),
                float(point.y()),
                float(frame.width()),
                float(frame.height()),
            )
        except RuntimeError:
            return None

        for item in self._descendant_items(host):
            try:
                values = (
                    item.property("cardX"),
                    item.property("cardY"),
                    item.property("cardW"),
                    item.property("cardH"),
                )
                if any(value is None for value in values):
                    continue
                actual = tuple(float(value) for value in values)
            except (RuntimeError, TypeError, ValueError):
                continue
            if all(abs(actual[index] - expected[index]) <= 0.5 for index in range(4)):
                self._card_item = item
                return item
        return None

    def attach(self, host_item: QQuickItem) -> None:
        self._host_item = host_item
        self._card_item = None
        if self._item is not None or self._component_failed:
            return
        component = QQmlComponent(self.engine, self)
        self._component = component
        component.statusChanged.connect(self._on_component_status_changed)
        component.setData(_QUICK_BATCH_QML.encode("utf-8"), _QUICK_BATCH_QML_URL)
        self._advance_component_status(component.status())

    def _on_component_status_changed(self, status: QQmlComponent.Status) -> None:
        self._advance_component_status(status)

    def _advance_component_status(self, status: QQmlComponent.Status) -> None:
        component = self._component
        if component is None or self._item is not None or self._component_failed:
            return
        if status in (QQmlComponent.Status.Null, QQmlComponent.Status.Loading):
            return
        if status == QQmlComponent.Status.Error:
            self._component_failed = True
            errors = "\n".join(error.toString() for error in component.errors())
            print("[quick-batch] QML unavailable; retaining legacy Batch list:\n" + errors, file=sys.stderr)
            return
        if status != QQmlComponent.Status.Ready or self._host_item is None:
            return
        created = component.create(self.engine.rootContext())
        if not isinstance(created, QQuickItem):
            if created is not None:
                created.deleteLater()
            self._component_failed = True
            return
        created.setParent(self)
        created.setParentItem(self._host_item)
        self._item = created
        self.refresh_geometry()

    def _ensure_placeholder(self) -> bool:
        if self._placeholder is not None:
            return True
        scroll = self.scroll
        card = self._card_frame
        if not isinstance(scroll, QWidget) or not isinstance(card, QWidget):
            return False
        layout = card.layout()
        if not isinstance(layout, QBoxLayout):
            return False
        index = layout.indexOf(scroll)
        if index < 0:
            return False

        placeholder = QWidget(card)
        placeholder.setObjectName("quickBatchListHost")
        placeholder.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        placeholder.setStyleSheet("background: transparent;")
        placeholder.setMinimumSize(scroll.minimumSize())
        placeholder.setMaximumSize(scroll.maximumSize())
        placeholder.setSizePolicy(scroll.sizePolicy())
        placeholder.installEventFilter(self)

        self._placeholder = placeholder
        self._layout = layout
        self._layout_index = index
        self._layout_stretch = int(layout.stretch(index))
        self._legacy_parent = scroll.parentWidget()
        return True

    def prepare_quick(self) -> bool:
        if self._active:
            return True
        if self._item is None or self._component_failed or not self._ensure_placeholder():
            return False
        scroll = self.scroll
        placeholder = self._placeholder
        layout = self._layout
        if not isinstance(scroll, QWidget) or placeholder is None or layout is None:
            return False

        try:
            layout.removeWidget(scroll)
            scroll.hide()
            scroll.setParent(None)
            layout.insertWidget(self._layout_index, placeholder, self._layout_stretch)
            placeholder.show()
            layout.invalidate()
            layout.activate()
        except RuntimeError:
            return False

        self._active = True
        self.sync_jobs(list(getattr(self.workspace, "_jobs", []) or []))
        self.refresh_geometry()
        self.presentationChanged.emit()
        return True

    def restore_legacy(self) -> None:
        if not self._active:
            return
        scroll = self.scroll
        placeholder = self._placeholder
        layout = self._layout
        parent = self._legacy_parent
        try:
            if layout is not None and placeholder is not None:
                layout.removeWidget(placeholder)
                placeholder.hide()
            if isinstance(scroll, QWidget) and isinstance(parent, QWidget):
                scroll.setParent(parent)
                if layout is not None:
                    layout.insertWidget(self._layout_index, scroll, self._layout_stretch)
                scroll.show()
            if layout is not None:
                layout.invalidate()
                layout.activate()
        except RuntimeError:
            pass
        self._active = False
        self._card_attached = False
        self._card_item = None
        self.presentationChanged.emit()
        responsive = getattr(self.workspace, "_batch_card_responsive", None)
        commit = getattr(responsive, "commit_now", None)
        if callable(commit):
            try:
                commit()
            except RuntimeError:
                pass

    def refresh_geometry(self, *_args: object) -> None:
        if not self._active:
            return
        item = self._item
        placeholder = self._placeholder
        frame = self._card_frame
        if item is None or placeholder is None or frame is None:
            return

        card_item = self._quick_card_item()
        attached = card_item is not None
        if card_item is not None:
            try:
                if item.parentItem() is not card_item:
                    item.setParentItem(card_item)
            except RuntimeError:
                attached = False
                self._card_item = None

        try:
            point = placeholder.mapTo(frame, QPoint(0, 0))
            snapshot = (
                int(point.x()),
                int(point.y()),
                max(0, int(placeholder.width())),
                max(0, int(placeholder.height())),
                bool(attached),
            )
        except RuntimeError:
            return
        current = (self._x, self._y, self._width, self._height, self._card_attached)
        if snapshot == current:
            return
        self._x, self._y, self._width, self._height, self._card_attached = snapshot
        self.presentationChanged.emit()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self._placeholder and event.type() in {
            QEvent.Type.Move,
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.LayoutRequest,
        }:
            self.refresh_geometry()
        return False

    def cleanup(self) -> None:
        self.restore_legacy()
        placeholder = self._placeholder
        self._placeholder = None
        if placeholder is not None:
            try:
                placeholder.removeEventFilter(self)
                placeholder.deleteLater()
            except RuntimeError:
                pass
        item = self._item
        self._item = None
        if item is not None:
            try:
                item.setParentItem(None)
                item.deleteLater()
            except RuntimeError:
                pass
        self._host_item = None
        self._card_item = None


__all__ = ["QuickBatchList"]
