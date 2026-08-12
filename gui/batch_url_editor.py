from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .batch_model import normalize_batch_urls


_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_VISIBLE_ROWS = 3
_ROW_HEIGHT = 46


def _extract_urls(text: str) -> list[str]:
    """Extract pasted http(s) URLs while preserving their original order."""

    return [match.group(0).strip() for match in _URL_RE.finditer(str(text or ""))]


class BatchUrlRow(QFrame):
    """One independently editable and independently enabled Batch source URL."""

    def __init__(self, editor: "BatchUrlEditor", index: int, url: str = "") -> None:
        super().__init__(editor.content)
        self.editor = editor
        self.setObjectName("batchUrlRow")
        self.setFixedHeight(_ROW_HEIGHT)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 8, 6)
        layout.setSpacing(8)

        self.index_label = QLabel(f"{index:02d}")
        self.index_label.setObjectName("batchUrlIndex")
        self.index_label.setFixedWidth(28)
        self.index_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.toggle = QPushButton("启用")
        self.toggle.setObjectName("batchUrlToggle")
        self.toggle.setCheckable(True)
        self.toggle.setChecked(True)
        self.toggle.setFixedWidth(68)
        self.toggle.toggled.connect(self._on_toggled)

        self.input = QLineEdit(url)
        self.input.setObjectName("batchUrlLineEdit")
        self.input.setPlaceholderText("https://detail.1688.com/offer/... 或其他 supplier URL")
        self.input.textChanged.connect(lambda _text: self.editor._refresh_summary())

        self.remove_button = QPushButton("删除")
        self.remove_button.setObjectName("batchUrlRemoveButton")
        self.remove_button.setFixedWidth(58)
        self.remove_button.clicked.connect(lambda: self.editor.remove_row(self))

        layout.addWidget(self.index_label)
        layout.addWidget(self.toggle)
        layout.addWidget(self.input, 1)
        layout.addWidget(self.remove_button)

        self.setStyleSheet(
            "QFrame#batchUrlRow {"
            "  background: rgba(9, 27, 45, 72);"
            "  border: 1px solid rgba(255, 255, 255, 34);"
            "  border-radius: 9px;"
            "}"
            "QLabel#batchUrlIndex {"
            "  color: rgba(235, 245, 255, 150);"
            "  font-weight: 700;"
            "}"
            "QPushButton#batchUrlToggle {"
            "  border: 1px solid rgba(255, 255, 255, 38);"
            "  border-radius: 10px;"
            "  background: rgba(10, 27, 43, 110);"
            "  color: rgba(235, 245, 255, 170);"
            "  padding: 4px 9px;"
            "  font-weight: 700;"
            "}"
            "QPushButton#batchUrlToggle:checked {"
            "  background: rgba(77, 179, 132, 115);"
            "  border-color: rgba(143, 225, 185, 150);"
            "  color: rgb(226, 255, 241);"
            "}"
            "QLineEdit#batchUrlLineEdit {"
            "  background: rgba(7, 21, 36, 90);"
            "  border: 1px solid rgba(255, 255, 255, 30);"
            "  border-radius: 8px;"
            "  padding: 6px 9px;"
            "}"
            "QPushButton#batchUrlRemoveButton {"
            "  border: 0;"
            "  background: transparent;"
            "  color: rgba(255, 205, 214, 190);"
            "  padding: 4px 6px;"
            "}"
            "QPushButton#batchUrlRemoveButton:hover {"
            "  color: rgb(255, 225, 231);"
            "  background: rgba(205, 75, 98, 55);"
            "  border-radius: 7px;"
            "}"
        )
        self._on_toggled(True)

    def _on_toggled(self, checked: bool) -> None:
        self.toggle.setText("启用" if checked else "停用")
        self.input.setEnabled(checked and not self.editor.locked)
        self.editor._refresh_summary()

    def set_index(self, index: int) -> None:
        self.index_label.setText(f"{index:02d}")

    def set_locked(self, locked: bool) -> None:
        self.toggle.setEnabled(not locked)
        self.input.setReadOnly(locked)
        self.input.setEnabled(self.toggle.isChecked())
        self.remove_button.setEnabled(not locked)

    def url(self) -> str:
        return self.input.text().strip()

    def is_enabled(self) -> bool:
        return self.toggle.isChecked()


class BatchUrlEditor(QWidget):
    """Compact per-link Batch editor with independent enable/disable controls."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.rows: list[BatchUrlRow] = []
        self.locked = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(7)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.summary = QLabel("0 条链接 · 启用 0")
        self.summary.setObjectName("cardHint")
        toolbar.addWidget(self.summary)
        toolbar.addStretch(1)

        self.paste_button = QPushButton("粘贴并拆分")
        self.paste_button.setObjectName("quietButton")
        self.paste_button.clicked.connect(self.paste_urls)
        self.add_button = QPushButton("+ 添加链接")
        self.add_button.setObjectName("quietButton")
        self.add_button.clicked.connect(lambda: self.add_row())
        toolbar.addWidget(self.paste_button)
        toolbar.addWidget(self.add_button)
        root.addLayout(toolbar)

        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("batchUrlScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setMinimumHeight(_ROW_HEIGHT + 8)
        self.scroll.setMaximumHeight(_VISIBLE_ROWS * _ROW_HEIGHT + 22)
        self.scroll.setStyleSheet(
            "QScrollArea#batchUrlScroll { background: transparent; border: 0; }"
            "QScrollArea#batchUrlScroll > QWidget > QWidget { background: transparent; }"
        )
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.viewport().setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.content = QWidget()
        self.content.setObjectName("batchUrlContent")
        self.content.setAutoFillBackground(False)
        self.content.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.rows_layout = QVBoxLayout(self.content)
        self.rows_layout.setContentsMargins(0, 0, 4, 0)
        self.rows_layout.setSpacing(6)
        self.rows_layout.addStretch(1)
        self.scroll.setWidget(self.content)
        root.addWidget(self.scroll)

        self.add_row()

    def _refresh_summary(self) -> None:
        nonempty = [row for row in self.rows if row.url()]
        enabled = [row for row in nonempty if row.is_enabled()]
        self.summary.setText(f"{len(nonempty)} 条链接 · 启用 {len(enabled)}")

    def _renumber(self) -> None:
        for index, row in enumerate(self.rows, start=1):
            row.set_index(index)

    def add_row(self, url: str = "", *, enabled: bool = True) -> BatchUrlRow:
        row = BatchUrlRow(self, len(self.rows) + 1, url)
        row.toggle.setChecked(bool(enabled))
        self.rows.append(row)
        self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
        row.set_locked(self.locked)
        self._refresh_summary()
        return row

    def add_urls(self, urls: list[str]) -> None:
        cleaned = [str(url).strip() for url in urls if str(url).strip()]
        if not cleaned:
            return

        existing = {row.url().casefold() for row in self.rows if row.url()}
        target_rows = [row for row in self.rows if not row.url()]
        for url in cleaned:
            key = url.casefold()
            if key in existing:
                continue
            existing.add(key)
            if target_rows:
                row = target_rows.pop(0)
                row.input.setText(url)
                row.toggle.setChecked(True)
            else:
                self.add_row(url)
        self._renumber()
        self._refresh_summary()

    def paste_urls(self) -> None:
        clipboard = QApplication.clipboard()
        text = clipboard.text() if clipboard is not None else ""
        self.add_urls(_extract_urls(text))

    def remove_row(self, row: BatchUrlRow) -> None:
        if self.locked or row not in self.rows:
            return
        self.rows.remove(row)
        self.rows_layout.removeWidget(row)
        row.deleteLater()
        if not self.rows:
            self.add_row()
        self._renumber()
        self._refresh_summary()

    def enabled_urls(self) -> list[str]:
        values = [row.url() for row in self.rows if row.is_enabled() and row.url()]
        return normalize_batch_urls("\n".join(values))

    def all_urls(self) -> list[str]:
        values = [row.url() for row in self.rows if row.url()]
        if not values:
            return []
        return normalize_batch_urls("\n".join(values))

    def clear(self) -> None:
        if self.locked:
            return
        for row in self.rows:
            self.rows_layout.removeWidget(row)
            row.deleteLater()
        self.rows.clear()
        self.add_row()

    def set_locked(self, locked: bool) -> None:
        self.locked = bool(locked)
        self.paste_button.setEnabled(not self.locked)
        self.add_button.setEnabled(not self.locked)
        for row in self.rows:
            row.set_locked(self.locked)

    # Compatibility with the old QPlainTextEdit attribute used by a few callers.
    def setReadOnly(self, read_only: bool) -> None:  # noqa: N802
        self.set_locked(bool(read_only))

    def toPlainText(self) -> str:  # noqa: N802
        return "\n".join(row.url() for row in self.rows if row.url())


__all__ = ["BatchUrlEditor", "BatchUrlRow"]
