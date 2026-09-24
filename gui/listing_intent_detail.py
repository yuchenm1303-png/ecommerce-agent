from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, QSignalBlocker, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .listing_offer_support import _INTENT_LIMIT, _clean_intent


_DETAIL_HEIGHT = 104


def _contains_widget(layout: Any, target: QWidget) -> bool:
    if layout is None:
        return False
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item.widget() is target:
            return True
        child = item.layout()
        if child is not None and _contains_widget(child, target):
            return True
    return False


def _direct_row(parent: QVBoxLayout, target: QWidget) -> QBoxLayout | None:
    for index in range(parent.count()):
        child = parent.itemAt(index).layout()
        if isinstance(child, QBoxLayout) and _contains_widget(child, target):
            return child
    return None


def _row_index(parent: QVBoxLayout, row: QBoxLayout) -> int:
    for index in range(parent.count()):
        if parent.itemAt(index).layout() is row:
            return index
    return -1


class ListingIntentDetailEditor(QObject):
    """Expandable editor for the existing Single listing-intent value.

    The compact QLineEdit remains the one canonical business value consumed by
    ListingOfferSupport, hardening, photo ranking and process handoff. This
    multiline editor is presentation only: it mirrors that same value and never
    creates a second AI prompt or a second persistence contract.
    """

    def __init__(
        self,
        window: Any,
        *,
        on_expanded: Callable[[bool], None] | None = None,
    ) -> None:
        super().__init__(window)
        self.window = window
        self._on_expanded = on_expanded
        self._syncing = False
        self._line_read_only_before_expand = False

        line = getattr(window, "listing_intent_input", None)
        if not isinstance(line, QLineEdit):
            raise RuntimeError("listing intent detail requires listing_intent_input")
        card = line.parentWidget()
        layout = card.layout() if card is not None else None
        if not isinstance(layout, QVBoxLayout):
            raise RuntimeError("listing intent detail requires the Single source card layout")
        row = _direct_row(layout, line)
        if row is None:
            raise RuntimeError("listing intent detail could not locate the compact intent row")

        self.line = line
        self.line.setMaxLength(_INTENT_LIMIT)

        self.button = QPushButton("详情", card)
        self.button.setObjectName("quietButton")
        self.button.setCheckable(True)
        self.button.setFixedHeight(30)
        self.button.setMinimumWidth(58)
        self.button.setToolTip("展开多行编辑器；与左侧 SKU / 规格输入是同一份 AI 商品意图。")
        row.addWidget(self.button, 0, Qt.AlignVCenter)

        self.host = QWidget(card)
        self.host.setObjectName("listingIntentDetailHost")
        detail_row = QHBoxLayout(self.host)
        detail_row.setContentsMargins(105, 0, 0, 0)
        detail_row.setSpacing(0)

        self.editor = QPlainTextEdit(self.host)
        self.editor.setObjectName("listingIntentDetailInput")
        self.editor.setFixedHeight(_DETAIL_HEIGHT)
        self.editor.setPlaceholderText(
            "详细描述本次要卖的颜色、尺寸、数量、套装、型号、版本或排除项……（最多 600 字符）"
        )
        self.editor.setToolTip(
            "这里和上方 SKU / 规格是同一个 listing intent。换行仅用于编辑阅读；"
            "提交给 AI 时沿用现有 600 字符清洗规则。"
        )
        detail_row.addWidget(self.editor, 1)
        self.host.hide()

        row_index = _row_index(layout, row)
        layout.insertWidget(row_index + 1 if row_index >= 0 else layout.count(), self.host)

        self.editor.setPlainText(self.line.text())
        self.line.textChanged.connect(self._sync_from_line)
        self.editor.textChanged.connect(self._sync_from_detail)
        self.button.toggled.connect(self._toggle)

        window.listing_intent_detail_input = self.editor
        window.listing_intent_detail_button = self.button
        window.listing_intent_detail_host = self.host

    def _sync_from_line(self, text: str) -> None:
        if self._syncing:
            return
        clean = str(text or "")[:_INTENT_LIMIT]
        if self.editor.toPlainText() == clean:
            return
        blocker = QSignalBlocker(self.editor)
        self.editor.setPlainText(clean)
        del blocker

    def _sync_from_detail(self) -> None:
        if self._syncing:
            return
        raw = self.editor.toPlainText()
        if len(raw) > _INTENT_LIMIT:
            raw = raw[:_INTENT_LIMIT]
            blocker = QSignalBlocker(self.editor)
            self.editor.setPlainText(raw)
            cursor = self.editor.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.editor.setTextCursor(cursor)
            del blocker

        canonical = _clean_intent(raw)
        if self.line.text() == canonical:
            return
        # Do not block the QLineEdit signal: ListingOfferHardening deliberately
        # listens to it so edits after preparation invalidate the old Fill Plan.
        self._syncing = True
        try:
            self.line.setText(canonical)
        finally:
            self._syncing = False

    def _toggle(self, expanded: bool) -> None:
        expanded = bool(expanded)
        if expanded:
            self._line_read_only_before_expand = self.line.isReadOnly()
            self.editor.setReadOnly(self._line_read_only_before_expand)
            self.host.show()
            self.line.setReadOnly(True)
            self.button.setText("收起")
            if not self.editor.isReadOnly():
                self.editor.setFocus(Qt.FocusReason.MouseFocusReason)
        else:
            self.host.hide()
            self.line.setReadOnly(self._line_read_only_before_expand)
            self.button.setText("详情")

        if self._on_expanded is not None:
            self._on_expanded(expanded)


def install_listing_intent_detail(
    window: Any,
    *,
    on_expanded: Callable[[bool], None] | None = None,
) -> ListingIntentDetailEditor:
    existing = getattr(window, "_listing_intent_detail_editor", None)
    if isinstance(existing, ListingIntentDetailEditor):
        return existing
    editor = ListingIntentDetailEditor(window, on_expanded=on_expanded)
    window._listing_intent_detail_editor = editor
    return editor


__all__ = ["ListingIntentDetailEditor", "install_listing_intent_detail"]
