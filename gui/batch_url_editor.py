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
_VISIBLE_ROWS = 4
_ROW_HEIGHT = 38
_CONTROL_HEIGHT = 30
_ROW_SPACING = 4
_LIST_HEIGHT = (_VISIBLE_ROWS * _ROW_HEIGHT) + ((_VISIBLE_ROWS - 1) * _ROW_SPACING) + 8
_TOOLBAR_HEIGHT = 34
_EDITOR_HEIGHT = _TOOLBAR_HEIGHT + _LIST_HEIGHT + 7


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
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)

        self.index_label = QLabel(f"{index:02d}")
        self.index_label.setObjectName("batchUrlIndex")
        self.index_label.setFixedSize(34, _CONTROL_HEIGHT)
        self.index_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.toggle = QPushButton("启用")
        self.toggle.setObjectName("batchUrlToggle")
        self.toggle.setCheckable(True)
        self.toggle.setChecked(True)
        self.toggle.setFixedSize(58, _CONTROL_HEIGHT)
        self.toggle.toggled.connect(self._on_toggled)

        self.input = QLineEdit(url)
        self.input.setObjectName("batchUrlLineEdit")
        self.input.setFixedHeight(_CONTROL_HEIGHT)
        self.input.setPlaceholderText("粘贴 supplier product URL")
        self.input.textChanged.connect(lambda _text: self.editor._refresh_summary())

        self.remove_button = QPushButton("删除")
        self.remove_button.setObjectName("batchUrlRemoveButton")
        self.remove_button.setToolTip("删除此链接")
        self.remove_button.setFixedSize(48, _CONTROL_HEIGHT)
        self.remove_button.clicked.connect(lambda: self.editor.remove_row(self))

        layout.addWidget(self.index_label)
        layout.addWidget(self.toggle)
        layout.addWidget(self.input, 1)
        layout.addWidget(self.remove_button)

        self.setStyleSheet(
            "QFrame#batchUrlRow {"
            "  background: rgba(6, 22, 40, 80);"
            "  border: 1px solid rgba(255,255,255,24);"
            "  border-radius: 9px;"
            "}"
            "QFrame#batchUrlRow:hover {"
            "  background: rgba(9, 31, 54, 98);"
            "  border-color: rgba(164,219,255,58);"
            "}"
            "QLabel#batchUrlIndex {"
            "  color: rgba(230,244,255,180);"
            "  background: rgba(130,182,218,28);"
            "  border: 1px solid rgba(184,224,249,28);"
            "  border-radius: 7px;"
            "  font-weight: 760;"
            "}"
            "QPushButton#batchUrlToggle {"
            "  min-height: 30px; max-height: 30px;"
            "  border: 1px solid rgba(255,255,255,32);"
            "  border-radius: 8px;"
            "  background: rgba(10,28,46,112);"
            "  color: rgba(235,245,255,190);"
            "  padding: 0 8px;"
            "  font-weight: 720;"
            "}"
            "QPushButton#batchUrlToggle:checked {"
            "  background: rgba(68,177,128,122);"
            "  border-color: rgba(148,229,190,145);"
            "  color: rgb(230,255,242);"
            "}"
            "QPushButton#batchUrlToggle:hover {"
            "  border-color: rgba(184,224,249,72);"
            "}"
            "QLineEdit#batchUrlLineEdit {"
            "  min-height: 30px; max-height: 30px;"
            "  background: rgba(3,16,30,104);"
            "  border: 1px solid rgba(255,255,255,26);"
            "  border-radius: 8px;"
            "  padding: 0 11px;"
            "  selection-background-color: rgba(86,170,224,150);"
            "}"
            "QLineEdit#batchUrlLineEdit:hover {"
            "  border-color: rgba(160,210,244,52);"
            "}"
            "QLineEdit#batchUrlLineEdit:focus {"
            "  border-color: rgba(139,214,255,132);"
            "  background: rgba(4,20,37,120);"
            "}"
            "QPushButton#batchUrlRemoveButton {"
            "  min-height: 30px; max-height: 30px;"
            "  border: 1px solid rgba(255,145,164,48);"
            "  border-radius: 8px;"
            "  background: rgba(128,42,58,54);"
            "  color: rgba(255,213,220,220);"
            "  padding: 0 7px;"
            "  font-size: 11px;"
            "  font-weight: 720;"
            "}"
            "QPushButton#batchUrlRemoveButton:hover {"
            "  background: rgba(191,55,79,92);"
            "  border-color: rgba(255,164,180,105);"
            "  color: rgb(255,240,243);"
            "}"
            "QPushButton#batchUrlRemoveButton:pressed {"
            "  background: rgba(160,43,65,120);"
            "}"
            "QPushButton#batchUrlRemoveButton:disabled {"
            "  background: rgba(70,45,52,34);"
            "  border-color: rgba(255,255,255,16);"
            "  color: rgba(255,220,226,80);"
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
    """Compact always-visible multi-link Batch editor."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.rows: list[BatchUrlRow] = []
        self.locked = False
        self.setFixedHeight(_EDITOR_HEIGHT)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(5)

        toolbar = QFrame(self)
        toolbar.setObjectName("batchUrlToolbar")
        toolbar.setFixedHeight(_TOOLBAR_HEIGHT)
        toolbar.setStyleSheet(
            "QFrame#batchUrlToolbar {"
            "  background: rgba(5,20,37,74);"
            "  border: 1px solid rgba(255,255,255,23);"
            "  border-radius: 8px;"
            "}"
        )
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(8, 3, 7, 3)
        toolbar_layout.setSpacing(7)

        self.summary = QLabel("0 LINKS · 0 ON")
        self.summary.setObjectName("batchUrlSummaryChip")
        self.summary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.summary.setMinimumWidth(104)
        self.summary.setStyleSheet(
            "QLabel#batchUrlSummaryChip {"
            "  color: rgba(220,240,255,210);"
            "  background: rgba(90,168,215,36);"
            "  border: 1px solid rgba(154,215,250,40);"
            "  border-radius: 6px;"
            "  padding: 3px 8px;"
            "  font-size: 11px;"
            "  font-weight: 760;"
            "}"
        )
        hint = QLabel("每个链接独立任务 · 第 5 条起滚动")
        hint.setObjectName("cardHint")

        self.paste_button = QPushButton("批量粘贴")
        self.paste_button.setObjectName("quietButton")
        self.paste_button.setFixedHeight(28)
        self.paste_button.clicked.connect(self.paste_urls)
        self.add_button = QPushButton("+ 添加链接")
        self.add_button.setObjectName("quietButton")
        self.add_button.setFixedHeight(28)
        self.add_button.clicked.connect(lambda: self.add_row())

        toolbar_layout.addWidget(self.summary)
        toolbar_layout.addWidget(hint)
        toolbar_layout.addStretch(1)
        toolbar_layout.addWidget(self.paste_button)
        toolbar_layout.addWidget(self.add_button)
        root.addWidget(toolbar)

        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("batchUrlScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setFixedHeight(_LIST_HEIGHT)
        self.scroll.setStyleSheet(
            "QScrollArea#batchUrlScroll {"
            "  background: rgba(4,17,31,30);"
            "  border: 1px solid rgba(255,255,255,14);"
            "  border-radius: 9px;"
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

        self._ensure_min_rows()
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        nonempty = [row for row in self.rows if row.url()]
        enabled = [row for row in nonempty if row.is_enabled()]
        self.summary.setText(f"{len(nonempty)} LINKS · {len(enabled)} ON")

    def _renumber(self) -> None:
        for index, row in enumerate(self.rows, start=1):
            row.set_index(index)

    def _ensure_min_rows(self) -> None:
        while len(self.rows) < _VISIBLE_ROWS:
            self.add_row()

    def _scroll_to_row(self, row: BatchUrlRow) -> None:
        if len(self.rows) > _VISIBLE_ROWS:
            self.scroll.ensureWidgetVisible(row, 0, 4)

    def add_row(self, url: str = "", *, enabled: bool = True) -> BatchUrlRow:
        row = BatchUrlRow(self, len(self.rows) + 1, url)
        row.toggle.setChecked(bool(enabled))
        self.rows.append(row)
        self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
        row.set_locked(self.locked)
        self._refresh_summary()
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
        if last_row is not None:
            self._scroll_to_row(last_row)

    def paste_urls(self) -> None:
        if self.locked:
            return
        clipboard = QApplication.clipboard()
        text = clipboard.text() if clipboard is not None else ""
        self.add_urls(_extract_urls(text))

    def remove_row(self, row: BatchUrlRow) -> None:
        if self.locked or row not in self.rows:
            return
        self.rows.remove(row)
        self.rows_layout.removeWidget(row)
        row.deleteLater()
        self._ensure_min_rows()
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
        self._ensure_min_rows()
        self._refresh_summary()

    def set_locked(self, locked: bool) -> None:
        self.locked = bool(locked)
        self.paste_button.setEnabled(not self.locked)
        self.add_button.setEnabled(not self.locked)
        for row in self.rows:
            row.set_locked(self.locked)

    # Compatibility with the old QPlainTextEdit attribute used by BatchWorkspace.
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
    editor._refresh_summary()
    return editor


__all__ = [
    "BatchUrlEditor",
    "BatchUrlRow",
    "install_batch_url_editor",
]
