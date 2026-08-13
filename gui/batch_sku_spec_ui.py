from __future__ import annotations

from types import MethodType
from typing import Any

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget


_SKU_PLACEHOLDER = "SKU规格（可选）"
_SKU_TOOLTIP = (
    "用自然语言描述这个链接本次实际售卖的规格 / 颜色 / 数量 / 套装。\n"
    "例如：此商品卖的是黑色净化器 + 2瓶香氛精油。\n"
    "每一行独立传给对应 Batch Job，AI 会结合商品页面一起理解；不要求特殊格式。"
)


class BatchSkuSpecUi(QObject):
    """Make the existing per-listing offer input explicit on every Batch row.

    ListingOfferSupport remains the owner of the actual listing-intent semantics
    and process handoff. This layer only makes that already-existing input a clear,
    first-class part of the Batch row so users cannot miss it.
    """

    def __init__(self, window: Any) -> None:
        super().__init__(window)
        self.window = window
        self.workspace = window.batch_workspace
        self.controller = self.workspace.controller
        self.editor = getattr(self.workspace, "_batch_url_editor", None)
        self.support = getattr(window, "_listing_offer_support", None)
        if self.editor is None or self.support is None:
            raise RuntimeError("Batch SKU UI requires BatchUrlEditor + ListingOfferSupport")

        for row in list(self.editor.rows):
            self._polish_row(row)

        original_add_row = self.editor.add_row

        def add_row(_editor: Any, *args: Any, **kwargs: Any):
            row = original_add_row(*args, **kwargs)
            self._polish_row(row)
            return row

        self.editor.add_row = MethodType(add_row, self.editor)
        self._update_toolbar_hint()

        # ListingOfferSupport owns the confirmation semantics. Its panel creator
        # already creates and wires the confirm button, but the current source
        # forgot to add that button to the panel layout. Repair presentation only:
        # after ListingOfferSupport handles jobs_changed and creates/rebuilds a
        # panel, attach the existing wired button to the visible panel.
        self.controller.jobs_changed.connect(lambda _jobs: self._ensure_confirm_buttons())
        self._ensure_confirm_buttons()

    def _polish_row(self, row: Any) -> None:
        offer = getattr(row, "offer_input", None)
        layout = row.layout()
        if not isinstance(offer, QLineEdit) or not isinstance(layout, QHBoxLayout):
            return

        offer.setObjectName("batchSkuSpecInput")
        offer.setPlaceholderText(_SKU_PLACEHOLDER)
        offer.setToolTip(_SKU_TOOLTIP)
        offer.setMinimumWidth(250)
        offer.setMaximumWidth(420)
        offer.setFixedHeight(28)
        offer.setStyleSheet(
            "QLineEdit#batchSkuSpecInput {"
            "  min-height: 28px; max-height: 28px;"
            "  background: rgba(20,31,50,112);"
            "  border: 1px solid rgba(166,211,255,45);"
            "  border-radius: 8px;"
            "  padding: 0 10px;"
            "  color: rgba(239,247,255,225);"
            "  selection-background-color: rgba(86,170,224,150);"
            "}"
            "QLineEdit#batchSkuSpecInput:hover {"
            "  border-color: rgba(170,220,255,78);"
            "  background: rgba(22,39,62,126);"
            "}"
            "QLineEdit#batchSkuSpecInput:focus {"
            "  border-color: rgba(139,214,255,150);"
            "  background: rgba(16,35,57,138);"
            "}"
            "QLineEdit#batchSkuSpecInput:disabled {"
            "  color: rgba(225,237,247,80);"
            "  border-color: rgba(255,255,255,14);"
            "  background: rgba(18,25,36,48);"
            "}"
        )

        label = getattr(row, "offer_label", None)
        if not isinstance(label, QLabel):
            label = QLabel("SKU规格", row)
            label.setObjectName("batchSkuSpecLabel")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setFixedSize(58, 28)
            label.setToolTip(_SKU_TOOLTIP)
            label.setStyleSheet(
                "QLabel#batchSkuSpecLabel {"
                "  color: rgba(205,231,250,205);"
                "  background: rgba(93,153,195,34);"
                "  border: 1px solid rgba(166,211,255,35);"
                "  border-radius: 7px;"
                "  font-size: 11px;"
                "  font-weight: 720;"
                "}"
            )
            index = layout.indexOf(offer)
            layout.insertWidget(max(0, index), label, 0, Qt.AlignmentFlag.AlignVCenter)
            row.offer_label = label

        enabled = bool(row.is_enabled()) and not bool(row.editor.locked)
        offer.setEnabled(enabled)
        offer.setReadOnly(bool(row.editor.locked))
        label.setEnabled(bool(row.is_enabled()))

        if not bool(row.property("batchSkuSpecToggleConnected")):
            row.setProperty("batchSkuSpecToggleConnected", True)
            row.toggle.toggled.connect(
                lambda checked, current=row: self._sync_row_enabled(current, bool(checked))
            )

    @staticmethod
    def _sync_row_enabled(row: Any, checked: bool) -> None:
        offer = getattr(row, "offer_input", None)
        label = getattr(row, "offer_label", None)
        locked = bool(getattr(row.editor, "locked", False))
        if isinstance(offer, QLineEdit):
            offer.setEnabled(bool(checked) and not locked)
            offer.setReadOnly(locked)
        if isinstance(label, QLabel):
            label.setEnabled(bool(checked))

    def _update_toolbar_hint(self) -> None:
        for label in self.editor.findChildren(QLabel):
            if label.text() == "每个链接独立任务 · 第 5 条起滚动":
                label.setText("每条链接独立 SKU规格 · 自然语言即可 · 第 5 条起滚动")
                label.setToolTip(_SKU_TOOLTIP)
                break

    def _ensure_confirm_buttons(self) -> None:
        panels = getattr(self.support, "_batch_required_panels", None)
        if not isinstance(panels, dict):
            return
        for panel in panels.values():
            host = getattr(panel, "host", None)
            layout = host.layout() if isinstance(host, QWidget) else None
            button = getattr(host, "_confirm_button", None) if host is not None else None
            if not isinstance(layout, QVBoxLayout) or not isinstance(button, QPushButton):
                continue
            if layout.indexOf(button) < 0:
                layout.addWidget(button, 0, Qt.AlignmentFlag.AlignRight)
            button.show()


def install_batch_sku_spec_ui(window: Any) -> BatchSkuSpecUi:
    existing = getattr(window, "_batch_sku_spec_ui", None)
    if isinstance(existing, BatchSkuSpecUi):
        return existing
    layer = BatchSkuSpecUi(window)
    window._batch_sku_spec_ui = layer
    return layer


__all__ = ["BatchSkuSpecUi", "install_batch_sku_spec_ui"]
