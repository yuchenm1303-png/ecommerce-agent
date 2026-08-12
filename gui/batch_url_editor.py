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
_ROW_HEIGHT = 36
_ROW_SPACING = 4
_LIST_HEIGHT = (_VISIBLE_ROWS * _ROW_HEIGHT) + ((_VISIBLE_ROWS - 1) * _ROW_SPACING) + 8
_COMPACT_HEIGHT = 42
_EXPANDED_HEIGHT = _COMPACT_HEIGHT + _LIST_HEIGHT + 35


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
        layout.setContentsMargins(7, 3, 7, 3)
        layout.setSpacing(6)

        self.index_label = QLabel(f"{index:02d}")
        self.index_label.setObjectName("batchUrlIndex")
        self.index_label.setFixedWidth(28)
        self.index_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.toggle = QPushButton("启用")
        self.toggle.setObjectName("batchUrlToggle")
        self.toggle.setCheckable(True)
        self.toggle.setChecked(True)
        self.toggle.setFixedWidth(54)
        self.toggle.toggled.connect(self._on_toggled)

        self.input = QLineEdit(url)
        self.input.setObjectName("batchUrlLineEdit")
        self.input.setPlaceholderText("supplier product URL")
        self.input.textChanged.connect(lambda _text: self.editor._refresh_summary())

        self.remove_button = QPushButton("×")
        self.remove_button.setObjectName("batchUrlRemoveButton")
        self.remove_button.setToolTip("删除此链接")
        self.remove_button.setFixedWidth(30)
        self.remove_button.clicked.connect(lambda: self.editor.remove_row(self))

        layout.addWidget(self.index_label)
        layout.addWidget(self.toggle)
        layout.addWidget(self.input, 1)
        layout.addWidget(self.remove_button)

        self.setStyleSheet(
            "QFrame#batchUrlRow {"
            "  background: rgba(7, 23, 42, 88);"
            "  border: 1px solid rgba(255, 255, 255, 25);"
            "  border-radius: 8px;"
            "}"
            "QFrame#batchUrlRow:hover {"
            "  background: rgba(10, 31, 53, 105);"
            "  border-color: rgba(164, 219, 255, 55);"
            "}"
            "QLabel#batchUrlIndex {"
            "  color: rgba(230, 243, 255, 145);"
            "  font-weight: 720;"
            "}"
            "QPushButton#batchUrlToggle {"
            "  border: 1px solid rgba(255, 255, 255, 28);"
            "  border-radius: 7px;"
            "  background: rgba(9, 27, 44, 108);"
            "  color: rgba(235, 245, 255, 170);"
            "  padding: 2px 5px;"
            "  font-weight: 700;"
            "}"
            "QPushButton#batchUrlToggle:checked {"
            "  background: rgba(73, 176, 129, 118);"
            "  border-color: rgba(143, 225, 185, 135);"
            "  color: rgb(226, 255, 241);"
            "}"
            "QLineEdit#batchUrlLineEdit {"
            "  background: rgba(4, 17, 31, 92);"
            "  border: 1px solid rgba(255, 255, 255, 22);"
            "  border-radius: 6px;"
            "  padding: 4px 8px;"
            "}"
            "QLineEdit#batchUrlLineEdit:focus {"
            "  border-color: rgba(150, 220, 255, 110);"
            "  background: rgba(6, 22, 39, 110);"
            "}"
            "QPushButton#batchUrlRemoveButton {"
            "  border: 0;"
            "  background: transparent;"
            "  color: rgba(255, 199, 211, 195);"
            "  font-size: 16px;"
            "  font-weight: 700;"
            "}"
            "QPushButton#batchUrlRemoveButton:hover {"
            "  color: rgb(255, 232, 237);"
            "  background: rgba(205, 75, 98, 58);"
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
    """Compact Batch input rail with an on-demand per-link management drawer."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.rows: list[BatchUrlRow] = []
        self.locked = False
        self.expanded = False
        self.setFixedHeight(_COMPACT_HEIGHT)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(5)

        self.rail = QFrame(self)
        self.rail.setObjectName("batchUrlRail")
        self.rail.setFixedHeight(_COMPACT_HEIGHT)
        self.rail.setStyleSheet(
            "QFrame#batchUrlRail {"
            "  background: rgba(5, 20, 37, 86);"
            "  border: 1px solid rgba(255, 255, 255, 27);"
            "  border-radius: 10px;"
            "}"
        )
        rail_layout = QHBoxLayout(self.rail)
        rail_layout.setContentsMargins(8, 5, 7, 5)
        rail_layout.setSpacing(7)

        self.summary = QLabel("0 LINKS · 0 ON")
        self.summary.setObjectName("batchUrlSummaryChip")
        self.summary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.summary.setMinimumWidth(102)
        self.summary.setStyleSheet(
            "QLabel#batchUrlSummaryChip {"
            "  color: rgba(220, 240, 255, 210);"
            "  background: rgba(90, 168, 215, 38);"
            "  border: 1px solid rgba(154, 215, 250, 42);"
            "  border-radius: 7px;"
            "  padding: 4px 8px;"
            "  font-size: 11px;"
            "  font-weight: 760;"
            "}"
        )

        self.quick_input = QLineEdit()
        self.quick_input.setObjectName("batchQuickUrlInput")
        self.quick_input.setPlaceholderText("输入 supplier URL，Enter 加入任务队列…")
        self.quick_input.returnPressed.connect(self.add_quick_url)
        self.quick_input.setStyleSheet(
            "QLineEdit#batchQuickUrlInput {"
            "  background: rgba(4, 16, 30, 92);"
            "  border: 1px solid rgba(255,255,255,23);"
            "  border-radius: 7px;"
            "  padding: 5px 10px;"
            "}"
            "QLineEdit#batchQuickUrlInput:focus {"
            "  border-color: rgba(148, 218, 255, 112);"
            "  background: rgba(5, 21, 38, 112);"
            "}"
        )

        self.add_button = QPushButton("加入")
        self.add_button.setObjectName("quietButton")
        self.add_button.clicked.connect(self.add_quick_url)
        self.paste_button = QPushButton("批量粘贴")
        self.paste_button.setObjectName("quietButton")
        self.paste_button.clicked.connect(self.paste_urls)
        self.manage_button = QPushButton("管理 0 ▾")
        self.manage_button.setObjectName("quietButton")
        self.manage_button.clicked.connect(self.toggle_expanded)

        rail_layout.addWidget(self.summary)
        rail_layout.addWidget(self.quick_input, 1)
        rail_layout.addWidget(self.add_button)
        rail_layout.addWidget(self.paste_button)
        rail_layout.addWidget(self.manage_button)
        root.addWidget(self.rail)

        self.drawer = QFrame(self)
        self.drawer.setObjectName("batchUrlDrawer")
        self.drawer.setStyleSheet(
            "QFrame#batchUrlDrawer {"
            "  background: rgba(5, 18, 33, 58);"
            "  border: 1px solid rgba(255, 255, 255, 18);"
            "  border-radius: 9px;"
            "}"
        )
        drawer_layout = QVBoxLayout(self.drawer)
        drawer_layout.setContentsMargins(6, 5, 6, 6)
        drawer_layout.setSpacing(4)

        columns = QHBoxLayout()
        columns.setContentsMargins(7, 0, 7, 0)
        columns.setSpacing(6)
        index_head = QLabel("NO.")
        index_head.setObjectName("sectionEyebrow")
        index_head.setFixedWidth(28)
        index_head.setAlignment(Qt.AlignmentFlag.AlignCenter)
        state_head = QLabel("状态")
        state_head.setObjectName("sectionEyebrow")
        state_head.setFixedWidth(54)
        state_head.setAlignment(Qt.AlignmentFlag.AlignCenter)
        url_head = QLabel("SUPPLIER PRODUCT URL")
        url_head.setObjectName("sectionEyebrow")
        action_head = QLabel("操作")
        action_head.setObjectName("sectionEyebrow")
        action_head.setFixedWidth(30)
        action_head.setAlignment(Qt.AlignmentFlag.AlignCenter)
        columns.addWidget(index_head)
        columns.addWidget(state_head)
        columns.addWidget(url_head, 1)
        columns.addWidget(action_head)
        drawer_layout.addLayout(columns)

        self.scroll = QScrollArea(self.drawer)
        self.scroll.setObjectName("batchUrlScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setFixedHeight(_LIST_HEIGHT)
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
        self.rows_layout.setContentsMargins(0, 0, 3, 0)
        self.rows_layout.setSpacing(_ROW_SPACING)
        self.rows_layout.addStretch(1)
        self.scroll.setWidget(self.content)
        drawer_layout.addWidget(self.scroll)
        root.addWidget(self.drawer)
        self.drawer.hide()

    def _refresh_summary(self) -> None:
        nonempty = [row for row in self.rows if row.url()]
        enabled = [row for row in nonempty if row.is_enabled()]
        self.summary.setText(f"{len(nonempty)} LINKS · {len(enabled)} ON")
        self.manage_button.setText(
            f"管理 {len(nonempty)} {'▴' if self.expanded else '▾'}"
        )

    def _renumber(self) -> None:
        for index, row in enumerate(self.rows, start=1):
            row.set_index(index)

    def _scroll_to_row(self, row: BatchUrlRow) -> None:
        if self.expanded:
            self.scroll.ensureWidgetVisible(row, 0, 5)

    def set_expanded(self, expanded: bool) -> None:
        self.expanded = bool(expanded)
        self.drawer.setVisible(self.expanded)
        self.setFixedHeight(_EXPANDED_HEIGHT if self.expanded else _COMPACT_HEIGHT)
        self._refresh_summary()

    def toggle_expanded(self) -> None:
        self.set_expanded(not self.expanded)

    def add_quick_url(self) -> None:
        if self.locked:
            return
        text = self.quick_input.text().strip()
        if not text:
            return
        urls = _extract_urls(text)
        if not urls:
            try:
                urls = normalize_batch_urls(text)
            except ValueError:
                self.quick_input.setToolTip("请输入完整 http(s) 商品链接")
                return
        self.add_urls(urls)
        self.quick_input.clear()
        self.quick_input.setToolTip("")

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
        last_row: BatchUrlRow | None = None
        for url in cleaned:
            key = url.casefold()
            if key in existing:
                continue
            existing.add(key)
            last_row = self.add_row(url)
        self._renumber()
        self._refresh_summary()
        if last_row is not None:
            self._scroll_to_row(last_row)

    def paste_urls(self) -> None:
        if self.locked:
            return
        clipboard = QApplication.clipboard()
        text = clipboard.text() if clipboard is not None else ""
        urls = _extract_urls(text)
        self.add_urls(urls)

    def remove_row(self, row: BatchUrlRow) -> None:
        if self.locked or row not in self.rows:
            return
        self.rows.remove(row)
        self.rows_layout.removeWidget(row)
        row.deleteLater()
        self._renumber()
        self._refresh_summary()
        if not self.rows:
            self.set_expanded(False)

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
        self.quick_input.clear()
        self.set_expanded(False)
        self._refresh_summary()

    def set_locked(self, locked: bool) -> None:
        self.locked = bool(locked)
        self.quick_input.setReadOnly(self.locked)
        self.quick_input.setEnabled(not self.locked)
        self.paste_button.setEnabled(not self.locked)
        self.add_button.setEnabled(not self.locked)
        self.manage_button.setEnabled(bool(self.rows))
        for row in self.rows:
            row.set_locked(self.locked)
        if self.locked:
            self.set_expanded(False)

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
