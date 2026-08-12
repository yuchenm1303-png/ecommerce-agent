from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


_SUMMARY_META = (
    ("TOTAL · 全部任务", "本批次全部商品"),
    ("PROCESSING · 处理中", "采集 / 准备 / 填写 / 保存"),
    ("READY · 可执行", "可单独或批量真实填写"),
    ("DONE · 已完成", "Save + reopen 已验证"),
    ("REVIEW · 待复核", "等待补充或人工确认"),
    ("FAILED · 失败", "查看 Job 错误与实时日志"),
)


def _hide_layout_tree(layout: QLayout | None) -> None:
    if layout is None:
        return
    for index in range(layout.count()):
        item = layout.itemAt(index)
        widget = item.widget()
        child_layout = item.layout()
        if widget is not None:
            widget.hide()
        if child_layout is not None:
            _hide_layout_tree(child_layout)


def install_batch_workspace_density(workspace: QWidget) -> None:
    """Give Batch job cards most of the viewport without flattening telemetry.

    The source area becomes a thin input/control rail. Summary cards remain
    medium-sized and information-dense. The owned-job surface keeps the only
    expanding vertical stretch.
    """

    if bool(getattr(workspace, "_batch_density_installed", False)):
        return

    root = workspace.layout()
    editor = getattr(workspace, "_batch_url_editor", None)
    source_card = editor.parentWidget() if isinstance(editor, QWidget) else None

    if isinstance(source_card, QFrame):
        source_layout = source_card.layout()
        if isinstance(source_layout, QVBoxLayout):
            # The old two-line hero heading duplicates the BATCH mode header and
            # used a disproportionate amount of vertical space. The new URL rail
            # carries its own count and controls, so keep only the rail + runtime
            # controls in this card.
            hero_item = source_layout.itemAt(0)
            if hero_item is not None:
                _hide_layout_tree(hero_item.layout())
                hero_widget = hero_item.widget()
                if hero_widget is not None:
                    hero_widget.hide()
            source_layout.setContentsMargins(12, 8, 12, 8)
            source_layout.setSpacing(6)
        source_card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Maximum,
        )

    # Keep the six overview cards visible and useful instead of reducing them to
    # tiny pills. They remain one row, with an additional explanatory line.
    summary_layout = None
    if root is not None and root.count() > 1:
        summary_layout = root.itemAt(1).layout()
    detail_labels: list[QLabel] = []
    if isinstance(summary_layout, QHBoxLayout):
        summary_layout.setSpacing(8)
        for index, (caption_text, detail_text) in enumerate(_SUMMARY_META):
            if index >= summary_layout.count():
                break
            card = summary_layout.itemAt(index).widget()
            if not isinstance(card, QFrame):
                continue
            card.setMinimumWidth(150)
            card.setMinimumHeight(70)
            card.setMaximumHeight(74)
            box = card.layout()
            if not isinstance(box, QVBoxLayout):
                continue
            box.setContentsMargins(13, 7, 13, 7)
            box.setSpacing(1)

            value = box.itemAt(0).widget() if box.count() > 0 else None
            caption = box.itemAt(1).widget() if box.count() > 1 else None
            if isinstance(value, QLabel):
                value.setStyleSheet("font-size: 20px; font-weight: 760;")
                value.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            if isinstance(caption, QLabel):
                caption.setText(caption_text)
                caption.setStyleSheet(
                    "font-size: 10px; font-weight: 720; color: rgba(235,245,255,170);"
                )

            detail = QLabel(detail_text)
            detail.setObjectName("batchSummaryDetail")
            detail.setStyleSheet(
                "font-size: 10px; color: rgba(225,240,250,125);"
            )
            detail.setTextFormat(Qt.TextFormat.PlainText)
            detail.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            box.addWidget(detail)
            detail_labels.append(detail)

    # The Job Control card is the actual workspace. Make it the sole expanding
    # region and keep a generous minimum viewport on normal desktop windows.
    queue_card = None
    action_card = None
    if root is not None:
        if root.count() > 2:
            queue_card = root.itemAt(2).widget()
        if root.count() > 3:
            action_card = root.itemAt(3).widget()
        root.setSpacing(8)
        root.setStretch(0, 0)
        root.setStretch(2, 1)
        root.setStretch(3, 0)

    if isinstance(queue_card, QFrame):
        queue_card.setMinimumHeight(430)
        queue_card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        queue_layout = queue_card.layout()
        if isinstance(queue_layout, QVBoxLayout):
            queue_layout.setContentsMargins(14, 10, 14, 12)
            queue_layout.setSpacing(7)

    if isinstance(action_card, QFrame):
        action_card.setMaximumHeight(52)
        action_card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Maximum,
        )

    setattr(workspace, "_batch_summary_detail_labels", detail_labels)
    setattr(workspace, "_batch_density_installed", True)


__all__ = ["install_batch_workspace_density"]
