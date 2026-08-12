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
_VISIBLE_ROWS = 5
_ROW_HEIGHT = 40
_ROW_SPACING = 5
_LIST_HEIGHT = (_VISIBLE_ROWS * _ROW_HEIGHT) + ((_VISIBLE_ROWS - 1) * _ROW_SPACING) + 4
_EDITOR_MIN_HEIGHT = _LIST_HEIGHT + 72


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
        layout.setContentsMargins(7, 4, 7, 4)
        layout.setSpacing(7)

        self.index_label = QLabel(f"{index:02d}")
        self.index_label.setObjectName("batchUrlIndex")
        self.index_label.setFixedWidth(30)
        self.index_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.toggle = QPushButton("启用")
        self.toggle.setObjectName("batchUrlToggle")
        self.toggle.setCheckable(True)
        self.toggle.setChecked(True)
        self.toggle.setFixedWidth(58)
        self.toggle.toggled.connect(self._on_toggled)

        self.input = QLineEdit(url)
        self.input.setObjectName("batchUrlLineEdit")
        self.input.setPlaceholderText("https://detail.1688.com/offer/... 或其他 supplier URL")
        self.input.textChanged.connect(lambda _text: self.editor._refresh_summary())

        self.remove_button = QPushButton("删除")
        self.remove_button.setObjectName("batchUrlRemoveButton")
        self.remove_button.setFixedWidth(50)
        self.remove_button.clicked.connect(lambda: self.editor.remove_row(self))

        layout.addWidget(self.index_label)
        layout.addWidget(self.toggle)
        layout.addWidget(self.input, 1)
        layout.addWidget(self.remove_button)

        self.setStyleSheet(
            "QFrame#batchUrlRow {"
            "  background: rgba(8, 27, 48, 76);"
            "  border: 1px solid rgba(255, 255, 255, 31);"
            "  border-radius: 8px;"
            "}"
            "QFrame#batchUrlRow:hover {"
            "  background: rgba(11, 34, 58, 91);"
            "  border-color: rgba(180, 224, 255, 52);"
            "}"
            "QLabel#batchUrlIndex {"
            "  color: rgba(235, 245, 255, 142);"
            "  font-weight: 720;"
            "}"
            "QPushButton#batchUrlToggle {"
            "  border: 1px solid rgba(255, 255, 255, 34);"
            "  border-radius: 8px;"
            "  background: rgba(10, 27, 43, 104);"
            "  color: rgba(235, 245, 255, 165);"
            "  padding: 3px 6px;"
            "  font-weight: 700;"
            "}"
            "QPushButton#batchUrlToggle:checked {"
            "  background: rgba(77, 179, 132, 112);"
            "  border-color: rgba(143, 225, 185, 142);"
            "  color: rgb(226, 255, 241);"
            "}"
            "QLineEdit#batchUrlLineEdit {"
            "  background: rgba(6, 21, 38, 86);"
            "  border: 1px solid rgba(255, 255, 255, 27);"
            "  border-radius: 7px;"
            "  padding: 5px 9px;"
            "}"
            "QLineEdit#batchUrlLineEdit:focus {"
            "  border-color: rgba(150, 220, 255, 116);"
            "  background: rgba(7, 24, 43, 103);"
            "}"
            "QPushButton#batchUrlRemoveButton {"
            "  border: 0;"
            "  background: transparent;"
            "  color: rgba(255, 205, 214, 184);"
            "  padding: 3px 5px;"
            "}"
            "QPushButton#batchUrlRemoveButton:hover {"
            "  color: rgb(255, 225, 231);"
            "  background: rgba(205, 75, 98, 55);"
            "  border-radius: 6px;"
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
    """Per-link Batch editor with a stable five-row working viewport."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.rows: list[BatchUrlRow] = []
        self.locked = False
        self.setMinimumHeight(_EDITOR_MIN_HEIGHT)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.summary = QLabel("链接 0 · 启用 0")
        self.summary.setObjectName("cardHint")
        self.summary.setStyleSheet("font-weight: 650;")
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

        columns = QHBoxLayout()
        columns.setContentsMargins(7, 0, 7, 0)
        columns.setSpacing(7)
        index_head = QLabel("NO.")
        index_head.setObjectName("sectionEyebrow")
        index_head.setFixedWidth(30)
        index_head.setAlignment(Qt.AlignmentFlag.AlignCenter)
        state_head = QLabel("状态")
        state_head.setObjectName("sectionEyebrow")
        state_head.setFixedWidth(58)
        state_head.setAlignment(Qt.AlignmentFlag.AlignCenter)
        url_head = QLabel("SUPPLIER PRODUCT URL")
        url_head.setObjectName("sectionEyebrow")
        action_head = QLabel("操作")
        action_head.setObjectName("sectionEyebrow")
        action_head.setFixedWidth(50)
        action_head.setAlignment(Qt.AlignmentFlag.AlignCenter)
        columns.addWidget(index_head)
        columns.addWidget(state_head)
        columns.addWidget(url_head, 1)
        columns.addWidget(action_head)
        root.addLayout(columns)

        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("batchUrlScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setFixedHeight(_LIST_HEIGHT)
        self.scroll.setStyleSheet(
            "QScrollArea#batchUrlScroll {"
            "  background: rgba(5, 18, 33, 36);"
            "  border: 1px solid rgba(255, 255, 255, 18);"
            "  border-radius: 10px;"
            "}"
            "QScrollArea#batchUrlScroll > QWidget > QWidget { background: transparent; }"
        )
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.viewport().setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.content = QWidget()
        self.content.setObjectName("batchUrlContent")
        self.content.setAutoFillBackground(False)
        self.content.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.rows_layout = QVBoxLayout(self.content)
        self.rows_layout.setContentsMargins(3, 3, 4, 3)
        self.rows_layout.setSpacing(_ROW_SPACING)
        self.rows_layout.addStretch(1)
        self.scroll.setWidget(self.content)
        root.addWidget(self.scroll)

        self.add_row()

    def _refresh_summary(self) -> None:
        nonempty = [row for row in self.rows if row.url()]
        enabled = [row for row in nonempty if row.is_enabled()]
        self.summary.setText(f"链接 {len(nonempty)} · 启用 {len(enabled)}")

    def _renumber(self) -> None:
        for index, row in enumerate(self.rows, start=1):
            row.set_index(index)

    def _scroll_to_row(self, row: BatchUrlRow) -> None:
        self.scroll.ensureWidgetVisible(row, 0, 6)

    def add_row(self, url: str = "", *, enabled: bool = True) -> BatchUrlRow:
        row = BatchUrlRow(self, len(self.rows) + 1, url)
        row.toggle.setChecked(bool(enabled))
        self.rows.append(row)
        self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
        row.set_locked(self.locked)
        self._refresh_summary()
        if len(self.rows) > _VISIBLE_ROWS:
            self._scroll_to_row(row)
        return row

    def add_urls(self, urls: list[str]) -> None:
        cleaned = [str(url).strip() for url in urls if str(url).strip()]
        if not cleaned:
            return

        existing = {row.url().casefold() for row in self.rows if row.url()}
        target_rows = [row for row in self.rows if not row.url()]
        last_row: BatchUrlRow | None = None
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
                row = self.add_row(url)
            last_row = row
        self._renumber()
        self._refresh_summary()
        if last_row is not None and len(self.rows) > _VISIBLE_ROWS:
            self._scroll_to_row(last_row)

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

    # Compatibility with the old QPlainTextEdit attribute used by BatchWorkspace.
    # Only enabled rows are exposed through this legacy text interface, so the
    # existing normalize_batch_urls()/start_prepare path automatically ignores
    # paused links without any runner changes.
    def setReadOnly(self, read_only: bool) -> None:  # noqa: N802
        self.set_locked(bool(read_only))

    def toPlainText(self) -> str:  # noqa: N802
        return "\n".join(
            row.url()
            for row in self.rows
            if row.is_enabled() and row.url()
        )


def install_batch_url_editor(workspace: QWidget) -> BatchUrlEditor:
    """Replace the legacy multiline Batch input in-place, preserving its API."""

    existing = getattr(workspace, "_batch_url_editor", None)
    if isinstance(existing, BatchUrlEditor):
        return existing

    old = getattr(workspace, "url_input", None)
    if not isinstance(old, QWidget):
        raise RuntimeError("Batch URL editor requires the existing url_input widget")
    host = old.parentWidget()
    layout = host.layout() if host is not None else None
    if host is None or not isinstance(layout, QVBoxLayout):
        raise RuntimeError("Batch URL editor could not resolve the source-card layout")

    try:
        initial_text = str(old.toPlainText())
    except (AttributeError, RuntimeError):
        initial_text = ""
    insert_at = layout.indexOf(old)
    if insert_at < 0:
        raise RuntimeError("Batch URL editor could not locate the legacy input")

    layout.removeWidget(old)
    old.hide()
    editor = BatchUrlEditor(host)
    layout.insertWidget(insert_at, editor)
    if initial_text.strip():
        editor.add_urls(_extract_urls(initial_text))

    # Keep the public attribute intact so existing BatchWorkspace methods continue
    # to call toPlainText()/setReadOnly() without knowing the presentation changed.
    setattr(workspace, "_legacy_batch_url_input", old)
    setattr(workspace, "url_input", editor)
    setattr(workspace, "_batch_url_editor", editor)

    clear_button = getattr(workspace, "clear_button", None)
    if isinstance(clear_button, QPushButton):
        try:
            clear_button.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        clear_button.clicked.connect(editor.clear)

    is_running = bool(getattr(workspace, "is_running", False))
    editor.set_locked(is_running)
    return editor


__all__ = [
    "BatchUrlEditor",
    "BatchUrlRow",
    "install_batch_url_editor",
]
