from __future__ import annotations

import csv
import io
from datetime import datetime
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QKeyEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QWidget,
)


class FieldTableTransfer(QObject):
    """Clipboard and CSV transfer for the canonical field-resolution table."""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self.window = window
        table = getattr(window, "field_table", None)
        if not isinstance(table, QTableWidget):
            raise RuntimeError("Field table transfer requires window.field_table")
        self.table = table

        self.copy_button = QPushButton("复制字段表")
        self.copy_button.setObjectName("quietButton")
        self.copy_button.setToolTip("复制完整字段表（含表头）为 TSV，可直接粘贴到 Excel / 表格软件。")
        self.copy_button.setFixedSize(92, 26)
        self.copy_button.setStyleSheet("QPushButton { min-height: 26px; max-height: 26px; padding: 0 8px; }")
        self.copy_button.clicked.connect(self.copy_all)

        self.export_button = QPushButton("导出 CSV")
        self.export_button.setObjectName("quietButton")
        self.export_button.setToolTip("把完整字段表导出为 Excel 兼容的 UTF-8 CSV。")
        self.export_button.setFixedSize(80, 26)
        self.export_button.setStyleSheet("QPushButton { min-height: 26px; max-height: 26px; padding: 0 8px; }")
        self.export_button.clicked.connect(self.export_csv)

        self._install_header_actions()
        self.table.installEventFilter(self)
        model = self.table.model()
        model.rowsInserted.connect(self._sync_actions)
        model.rowsRemoved.connect(self._sync_actions)
        model.modelReset.connect(self._sync_actions)
        self._sync_actions()

    def _install_header_actions(self) -> None:
        """Mount transfer controls inside the existing field-card header row.

        ui_polish compacts FIELD REVIEW into one horizontal header before this
        controller is installed. Reusing that row keeps the table as the only
        vertically expanding body instead of spending another line on utilities.
        """

        parent = self.table.parentWidget()
        layout = parent.layout() if parent is not None else None
        if layout is None:
            raise RuntimeError("Field table parent has no layout")

        table_index = layout.indexOf(self.table)
        header_row = None
        if table_index >= 0:
            for index in range(table_index):
                candidate = layout.itemAt(index).layout()
                if isinstance(candidate, QHBoxLayout):
                    header_row = candidate
                    break

        actions = QWidget(parent)
        actions.setObjectName("fieldTableTransferActions")
        actions.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        actions.setFixedHeight(26)
        row = QHBoxLayout(actions)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(self.copy_button)
        row.addWidget(self.export_button)

        if header_row is not None:
            header_row.addWidget(actions, 0, Qt.AlignmentFlag.AlignTop)
        elif table_index >= 0:
            # Source/debug builds may omit ui_polish. Keep the controls usable,
            # but keep the fallback row at the compact toolbar height.
            layout.insertWidget(table_index, actions, 0, Qt.AlignmentFlag.AlignRight)
        else:
            layout.addWidget(actions, 0, Qt.AlignmentFlag.AlignRight)
        self.action_host = actions

    @staticmethod
    def _headers(table: QTableWidget) -> tuple[str, ...]:
        headers: list[str] = []
        for column in range(table.columnCount()):
            item = table.horizontalHeaderItem(column)
            headers.append(item.text() if item is not None else f"Column {column + 1}")
        return tuple(headers)

    @staticmethod
    def _row_values(table: QTableWidget, row: int) -> tuple[str, ...]:
        return tuple(
            table.item(row, column).text() if table.item(row, column) is not None else ""
            for column in range(table.columnCount())
        )

    def _rows(self, row_indexes: Iterable[int] | None = None) -> tuple[tuple[str, ...], ...]:
        if row_indexes is None:
            indexes = range(self.table.rowCount())
        else:
            indexes = sorted({int(row) for row in row_indexes if 0 <= int(row) < self.table.rowCount()})
        return tuple(self._row_values(self.table, row) for row in indexes)

    def _selected_rows(self) -> tuple[int, ...]:
        selection = self.table.selectionModel()
        if selection is None:
            return ()
        return tuple(sorted({index.row() for index in selection.selectedRows()}))

    @staticmethod
    def _tsv(headers: tuple[str, ...], rows: tuple[tuple[str, ...], ...]) -> str:
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
        writer.writerow(headers)
        writer.writerows(rows)
        return stream.getvalue()

    @staticmethod
    def _write_csv(path: Path, headers: tuple[str, ...], rows: tuple[tuple[str, ...], ...]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(headers)
            writer.writerows(rows)

    def _sync_actions(self, *_args: object) -> None:
        enabled = self.table.rowCount() > 0
        self.copy_button.setEnabled(enabled)
        self.export_button.setEnabled(enabled)

    def _flash_button(self, button: QPushButton, text: str, restore: str) -> None:
        button.setText(text)
        QTimer.singleShot(1400, lambda: button.setText(restore))

    def copy_all(self, _checked: bool = False) -> None:
        rows = self._rows()
        if not rows:
            return
        QApplication.clipboard().setText(self._tsv(self._headers(self.table), rows))
        self._flash_button(self.copy_button, f"已复制 {len(rows)} 行", "复制字段表")

    def copy_selected(self) -> None:
        selected = self._selected_rows()
        rows = self._rows(selected if selected else None)
        if not rows:
            return
        QApplication.clipboard().setText(self._tsv(self._headers(self.table), rows))
        self._flash_button(self.copy_button, f"已复制 {len(rows)} 行", "复制字段表")

    def _default_export_path(self) -> Path:
        result = getattr(self.window, "current_result", None)
        run_dir = getattr(result, "run_dir", None)
        base = Path(run_dir) if run_dir else Path(getattr(self.window, "project_root", Path.cwd()))
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return base / f"field_resolution_{stamp}.csv"

    def export_csv(self, _checked: bool = False) -> None:
        rows = self._rows()
        if not rows:
            return
        filename, _selected_filter = QFileDialog.getSaveFileName(
            self.window,
            "导出字段表",
            str(self._default_export_path()),
            "CSV (*.csv)",
        )
        if not filename:
            return
        path = Path(filename)
        if path.suffix.casefold() != ".csv":
            path = path.with_suffix(".csv")
        try:
            self._write_csv(path, self._headers(self.table), rows)
        except OSError as exc:
            QMessageBox.warning(self.window, "字段表导出失败", str(exc))
            return
        self._flash_button(self.export_button, f"已导出 {len(rows)} 行", "导出 CSV")

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[override]
        if watched is self.table and event.type() == QEvent.Type.KeyPress:
            key_event = event
            if isinstance(key_event, QKeyEvent) and key_event.matches(QKeySequence.StandardKey.Copy):
                self.copy_selected()
                return True
        return super().eventFilter(watched, event)


def install_field_table_transfer(window: QWidget) -> FieldTableTransfer:
    existing = getattr(window, "_field_table_transfer", None)
    if isinstance(existing, FieldTableTransfer):
        return existing
    controller = FieldTableTransfer(window)
    window._field_table_transfer = controller  # type: ignore[attr-defined]
    return controller
