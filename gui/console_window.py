from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .acceptance_console import AcceptanceConsole
from .main_window import MainWindow as BaseMainWindow
from .main_window import STATUS_COLORS
from .result_loader import PhaseStats, RunResult


_AI_STATUS_COLORS = {
    **STATUS_COLORS,
    "BUSINESS_LOCKED": QColor("#d9a2c5"),
    "REVIEW": QColor("#b8b6ef"),
}


class MainWindow(BaseMainWindow):
    """Primary local acceptance UI: dense telemetry without changing core logic."""

    def __init__(self, project_root: Path) -> None:
        super().__init__(project_root)
        self.setWindowTitle("ecommerce-agent · Acceptance Control Console")
        self.resize(1600, 1040)
        self.setMinimumSize(1240, 820)

    def _build_log_card(self) -> QFrame:
        self.console = AcceptanceConsole(self.runner)
        # Keep the existing buffered log presenter contract: it receives the
        # console's Live Console view as the one canonical log widget.
        self.log_view = self.console.log_view
        return self.console

    def _build_fields_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("glassCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(15, 13, 15, 14)
        layout.setSpacing(9)

        eyebrow = QLabel("FIELD RESOLUTION · FULL TRACE")
        eyebrow.setObjectName("sectionEyebrow")
        title = QLabel("字段决策与最终 Gate")
        title.setObjectName("cardTitle")
        self.fields_hint = QLabel("等待只读测试结果")
        self.fields_hint.setObjectName("cardHint")
        layout.addWidget(eyebrow)
        layout.addWidget(title)
        layout.addWidget(self.fields_hint)

        self.field_table = QTableWidget(0, 7)
        self.field_table.setHorizontalHeaderLabels(
            ["字段名", "AI 状态", "AI 结果", "最终状态", "blocked / gate 原因", "来源", "Field ID"]
        )
        self.field_table.setAlternatingRowColors(True)
        self.field_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.field_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.field_table.setSortingEnabled(False)
        self.field_table.verticalHeader().setVisible(False)
        header = self.field_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.field_table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.field_table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        layout.addWidget(self.field_table, 1)
        return card

    def _build_runtime_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("glassCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(15, 13, 15, 14)
        layout.setSpacing(5)

        eyebrow = QLabel("RUN DIAGNOSTICS · MODEL / CACHE")
        eyebrow.setObjectName("sectionEyebrow")
        title = QLabel("Resolver Telemetry")
        title.setObjectName("cardTitle")
        layout.addWidget(eyebrow)
        layout.addWidget(title)

        self.cold_label = self._diag_label("Cold Local  · waiting")
        self.cold_web_label = self._diag_label("Cold Web    · waiting")
        self.hot_label = self._diag_label("Hot Local   · waiting")
        self.hot_web_label = self._diag_label("Hot Web     · waiting")
        self.source_cache_label = self._diag_label("Source cache · waiting")
        self.web_cache_label = self._diag_label("Web cache    · waiting")
        self.model_total_label = self._diag_label("Model calls  · waiting")
        self.pipeline_detail_label = self._diag_label("Fields / Plan · waiting")

        for label in (
            self.cold_label,
            self.cold_web_label,
            self.hot_label,
            self.hot_web_label,
            self.source_cache_label,
            self.web_cache_label,
            self.model_total_label,
            self.pipeline_detail_label,
        ):
            layout.addWidget(label)
        return card

    def _diag_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setObjectName("cardHint")
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        return label

    def _populate_fields(self, result: RunResult) -> None:
        self.field_table.setUpdatesEnabled(False)
        try:
            self.field_table.setRowCount(len(result.fields))
            for row_index, row in enumerate(result.fields):
                values = [
                    row.field_name,
                    row.ai_status,
                    row.ai_result,
                    row.final_status,
                    row.blocked_reason,
                    row.source,
                    row.field_id,
                ]
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setToolTip(value)
                    if column == 1:
                        color = _AI_STATUS_COLORS.get(row.ai_status)
                        if color:
                            item.setForeground(color)
                    elif column == 3:
                        color = STATUS_COLORS.get(row.final_status)
                        if color:
                            item.setForeground(color)
                    self.field_table.setItem(row_index, column, item)
        finally:
            self.field_table.setUpdatesEnabled(True)
        self.field_table.resizeRowsToContents()

    def _populate_runtime(self, cold: PhaseStats, hot: PhaseStats) -> None:
        self.cold_label.setText(self._local_text("Cold Local", cold))
        self.cold_web_label.setText(self._web_text("Cold Web", cold))
        self.hot_label.setText(self._local_text("Hot Local", hot))
        self.hot_web_label.setText(self._web_text("Hot Web", hot))
        self.source_cache_label.setText(
            "Source cache · Cold={} · Hot={}".format(
                "HIT" if cold.source_cache_hit else "MISS",
                "HIT" if hot.source_cache_hit else "MISS",
            )
        )
        self.web_cache_label.setText(
            "Web cache · Cold {}/{} · Hot {}/{}".format(
                cold.web_cache_hits,
                cold.web_batch_count,
                hot.web_cache_hits,
                hot.web_batch_count,
            )
        )
        local_calls = cold.model_calls + hot.model_calls
        web_calls = cold.web_model_calls + hot.web_model_calls
        self.model_total_label.setText(
            f"Model calls · Local={local_calls} · Web={web_calls} · Total={local_calls + web_calls}"
        )

    def _local_text(self, name: str, stats: PhaseStats) -> str:
        return (
            f"{name} · batches={stats.batch_count} · calls={stats.model_calls} · "
            f"cache={stats.cache_hits}/{stats.batch_count} · failed={stats.failed_batches}"
        )

    def _web_text(self, name: str, stats: PhaseStats) -> str:
        return (
            f"{name} · batches={stats.web_batch_count} · calls={stats.web_model_calls} · "
            f"cache={stats.web_cache_hits}/{stats.web_batch_count} · failed={stats.web_failed_batches}"
        )

    def _reset_result_views(self) -> None:
        super()._reset_result_views()
        self.cold_web_label.setText("Cold Web    · waiting")
        self.hot_web_label.setText("Hot Web     · waiting")
        self.model_total_label.setText("Model calls  · waiting")
        self.pipeline_detail_label.setText("Fields / Plan · waiting")

    def _apply_result(self, result: RunResult) -> None:
        super()._apply_result(result)
        ai_counts: dict[str, int] = {}
        for row in result.fields:
            ai_counts[row.ai_status] = ai_counts.get(row.ai_status, 0) + 1
        ai_text = ", ".join(f"{key}={value}" for key, value in sorted(ai_counts.items())) or "waiting"
        self.pipeline_detail_label.setText(
            f"Fields / Plan · live={result.live_field_count} · final READY={result.ready} · "
            f"BLOCKED={result.blocked} · AI[{ai_text}]"
        )
