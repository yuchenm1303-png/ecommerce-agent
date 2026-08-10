from __future__ import annotations

from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .batch_model import BatchJob, normalize_batch_urls
from .batch_runner import BatchController
from .readonly_runner import RunnerConfig


_STATUS_COLORS = {
    "READY": QColor("#8fe1b9"),
    "DONE": QColor("#8fe1b9"),
    "REVIEW": QColor("#f4cb7a"),
    "FAILED": QColor("#f18da0"),
    "STOPPED": QColor("#f4cb7a"),
}

_STAGE_LABELS = {
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


class _JobDetailDialog(QDialog):
    def __init__(self, job: BatchJob, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{job.job_id} · Batch Job")
        self.setModal(True)
        self.resize(720, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)

        eyebrow = QLabel("BATCH JOB · FULL TRACE")
        eyebrow.setObjectName("sectionEyebrow")
        title = QLabel(job.product_name or _product_label(job.product_url))
        title.setObjectName("appTitle")
        title.setStyleSheet("font-size: 24px; font-weight: 740;")
        layout.addWidget(eyebrow)
        layout.addWidget(title)

        summary = QLabel(
            f"{job.job_id}   ·   {_STAGE_LABELS.get(job.status, job.status)}   ·   "
            f"READY {job.ready}   ·   BLOCKED {job.blocked}"
        )
        summary.setObjectName("phaseBadge")
        layout.addWidget(summary)

        detail = QPlainTextEdit()
        detail.setReadOnly(True)
        detail.setPlainText(
            "\n".join(
                (
                    f"Supplier URL\n{job.product_url}",
                    f"\nVertical\n{job.vertical or '—'}",
                    f"\nBrand\n{job.brand or '—'}",
                    f"\nRequired blocked\n{job.required_blocked}",
                    f"\nProduct images\n{job.image_count}",
                    f"\nMakro targetId\n{job.makro_target_id or '—'}",
                    f"\nRun directory\n{job.run_dir or '—'}",
                    f"\nExecution report\n{job.execution_report or '—'}",
                    f"\nLast detail\n{job.stage_detail or '—'}",
                    f"\nError / review reason\n{job.error or '—'}",
                )
            )
        )
        layout.addWidget(detail, 1)

        row = QHBoxLayout()
        open_dir = QPushButton("打开 Job 目录")
        open_dir.setObjectName("quietButton")
        open_dir.clicked.connect(lambda: _open_path(Path(job.run_dir).parent))
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        row.addWidget(open_dir)
        row.addStretch(1)
        row.addWidget(close)
        layout.addLayout(row)


class BatchWorkspace(QWidget):
    """Production-oriented multi-product control tower.

    It deliberately keeps the main surface job-centric. Field traces and raw
    logs stay inside per-job artifacts instead of overwhelming the queue view.
    """

    def __init__(
        self,
        project_root: Path,
        *,
        busy_guard: Callable[[], bool] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.project_root = project_root.resolve()
        self.busy_guard = busy_guard or (lambda: False)
        self.controller = BatchController(self.project_root, self)
        self._jobs: list[BatchJob] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(self._build_source_card())
        layout.addLayout(self._build_summary_row())
        layout.addWidget(self._build_queue_card(), 1)
        layout.addWidget(self._build_action_card())

        self.controller.jobs_changed.connect(self._apply_jobs)
        self.controller.summary_changed.connect(self._apply_summary)
        self.controller.running_changed.connect(self._set_running)
        self.controller.state_changed.connect(self._set_state)
        self.controller.failed.connect(self._show_failure)

    @property
    def is_running(self) -> bool:
        return self.controller.is_running

    def _build_source_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("heroCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 15, 18, 15)
        layout.setSpacing(9)

        top = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        eyebrow = QLabel("BATCH LISTING · MULTI PRODUCT QUEUE")
        eyebrow.setObjectName("sectionEyebrow")
        title = QLabel("批量商品队列")
        title.setObjectName("cardTitle")
        title_box.addWidget(eyebrow)
        title_box.addWidget(title)
        hint = QLabel("每行一个 1688 / supplier URL。Source 串行预取；准备完成后最多 2 个 owned Makro Tab 并行工作。")
        hint.setObjectName("cardHint")
        hint.setWordWrap(True)
        top.addLayout(title_box)
        top.addSpacing(14)
        top.addWidget(hint, 1, Qt.AlignBottom)
        layout.addLayout(top)

        self.url_input = QPlainTextEdit()
        self.url_input.setPlaceholderText(
            "https://detail.1688.com/offer/AAA.html\n"
            "https://detail.1688.com/offer/BBB.html\n"
            "https://detail.1688.com/offer/CCC.html"
        )
        self.url_input.setMaximumHeight(112)
        layout.addWidget(self.url_input)

        row = QHBoxLayout()
        self.makro_port = QSpinBox()
        self.makro_port.setRange(1, 65535)
        self.makro_port.setValue(9222)
        self.makro_port.setPrefix("Makro CDP  ")
        self.makro_port.setMaximumWidth(170)
        self.source_port = QSpinBox()
        self.source_port.setRange(1, 65535)
        self.source_port.setValue(9333)
        self.source_port.setPrefix("Source CDP  ")
        self.source_port.setMaximumWidth(175)
        self.worker_count = QSpinBox()
        self.worker_count.setRange(1, 3)
        self.worker_count.setValue(2)
        self.worker_count.setPrefix("Makro Workers  ")
        self.worker_count.setMaximumWidth(190)
        row.addWidget(self.makro_port)
        row.addWidget(self.source_port)
        row.addWidget(self.worker_count)
        row.addStretch(1)

        self.clear_button = QPushButton("清空输入")
        self.clear_button.setObjectName("quietButton")
        self.clear_button.clicked.connect(self.url_input.clear)
        self.prepare_button = QPushButton("批量准备")
        self.prepare_button.setObjectName("primaryButton")
        self.prepare_button.clicked.connect(self._start_prepare)
        row.addWidget(self.clear_button)
        row.addWidget(self.prepare_button)
        layout.addLayout(row)
        return card

    def _build_summary_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        self.summary_labels: dict[str, QLabel] = {}
        for key, title in (
            ("total", "TOTAL"),
            ("processing", "PROCESSING"),
            ("ready", "READY"),
            ("done", "DONE"),
            ("review", "REVIEW"),
            ("failed", "FAILED"),
        ):
            card = QFrame()
            card.setObjectName("statusCard")
            box = QVBoxLayout(card)
            box.setContentsMargins(14, 9, 14, 9)
            value = QLabel("0")
            value.setStyleSheet("font-size: 21px; font-weight: 740;")
            caption = QLabel(title)
            caption.setObjectName("sectionEyebrow")
            box.addWidget(value)
            box.addWidget(caption)
            self.summary_labels[key] = value
            row.addWidget(card, 1)
        return row

    def _build_queue_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("glassCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(15, 13, 15, 14)
        layout.setSpacing(8)

        header_row = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        eyebrow = QLabel("JOB QUEUE · OWNED TAB ISOLATION")
        eyebrow.setObjectName("sectionEyebrow")
        title = QLabel("商品任务")
        title.setObjectName("cardTitle")
        title_box.addWidget(eyebrow)
        title_box.addWidget(title)
        self.state_label = QLabel("Idle · 等待批量链接")
        self.state_label.setObjectName("cardHint")
        header_row.addLayout(title_box)
        header_row.addStretch(1)
        header_row.addWidget(self.state_label)
        layout.addLayout(header_row)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            ["Job", "Product", "Stage", "Vertical", "Brand", "READY", "BLOCKED", "Progress", "Reason"]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(False)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(8, QHeaderView.Stretch)
        self.table.doubleClicked.connect(self._open_selected_job)
        layout.addWidget(self.table, 1)
        return card

    def _build_action_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("glassCard")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(16, 11, 16, 11)
        layout.setSpacing(10)

        self.save_check = QCheckBox("允许 Save + reopen")
        self.images_check = QCheckBox("上传本次商品图")
        self.qc_check = QCheckBox("Send to QC · LOCKED")
        self.qc_check.setChecked(False)
        self.qc_check.setEnabled(False)
        layout.addWidget(self.save_check)
        layout.addWidget(self.images_check)
        layout.addWidget(self.qc_check)
        layout.addStretch(1)

        self.open_batch_button = QPushButton("打开 Batch 目录")
        self.open_batch_button.setObjectName("quietButton")
        self.open_batch_button.setEnabled(False)
        self.open_batch_button.clicked.connect(self._open_batch_dir)
        self.stop_button = QPushButton("停止")
        self.stop_button.setObjectName("dangerButton")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.controller.stop)
        self.execute_button = QPushButton("批量填写 READY")
        self.execute_button.setObjectName("primaryButton")
        self.execute_button.setEnabled(False)
        self.execute_button.clicked.connect(self._start_execution)
        layout.addWidget(self.open_batch_button)
        layout.addWidget(self.stop_button)
        layout.addWidget(self.execute_button)
        return card

    def _start_prepare(self) -> None:
        if self.busy_guard():
            QMessageBox.warning(self, "无法开始 Batch", "Single workflow / real execution 仍在运行。")
            return
        try:
            urls = normalize_batch_urls(self.url_input.toPlainText())
            config = RunnerConfig(
                product_url=urls[0],
                makro_cdp_port=int(self.makro_port.value()),
                source_cdp_port=int(self.source_port.value()),
                source_use_current_page=False,
            )
            self.save_check.setChecked(False)
            self.images_check.setChecked(False)
            self.controller.start_prepare(
                urls,
                config,
                prepare_concurrency=int(self.worker_count.value()),
            )
            self.open_batch_button.setEnabled(True)
        except Exception as exc:
            QMessageBox.critical(self, "批量准备无法启动", str(exc))

    def _start_execution(self) -> None:
        if self.busy_guard():
            QMessageBox.warning(self, "无法开始填写", "Single workflow / real execution 仍在运行。")
            return
        batch = self.controller.batch
        if batch is None:
            return
        ready = [job for job in batch.jobs if job.status == "READY"]
        if not ready:
            QMessageBox.information(self, "没有 READY 商品", "当前 Batch 没有可执行的 READY Job。")
            return
        if not self.save_check.isChecked():
            QMessageBox.warning(
                self,
                "需要 Save 授权",
                "批量 Full Step 3 会持久化草稿。请显式勾选“允许 Save + reopen”。",
            )
            return
        answer = QMessageBox.question(
            self,
            "确认批量真实填写",
            f"即将执行 {len(ready)} 个 READY 商品。\n\n"
            f"Makro workers: {self.worker_count.value()}\n"
            f"Save + reopen: ON\n"
            f"Product Photos: {'ON' if self.images_check.isChecked() else 'OFF'}\n"
            "Send to QC: LOCKED / FALSE\n\n"
            "确认开始？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self.controller.start_execution(
                allow_save=True,
                upload_images=self.images_check.isChecked(),
                execute_concurrency=int(self.worker_count.value()),
            )
        except Exception as exc:
            QMessageBox.critical(self, "批量填写无法启动", str(exc))

    def _apply_jobs(self, jobs: list[BatchJob]) -> None:
        self._jobs = list(jobs)
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(len(jobs))
            for row, job in enumerate(jobs):
                values = (
                    job.job_id,
                    job.product_name or _product_label(job.product_url),
                    _STAGE_LABELS.get(job.status, job.status),
                    job.vertical or "—",
                    job.brand or "—",
                    str(job.ready),
                    str(job.blocked),
                    f"{max(0, min(100, job.progress))}%",
                    job.error or job.stage_detail,
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setToolTip(value)
                    if column == 2 and job.status in _STATUS_COLORS:
                        item.setForeground(_STATUS_COLORS[job.status])
                    self.table.setItem(row, column, item)
        finally:
            self.table.setUpdatesEnabled(True)
        self.execute_button.setEnabled(
            not self.controller.is_running and any(job.status == "READY" for job in jobs)
        )

    def _apply_summary(self, summary: dict[str, int]) -> None:
        for key, label in self.summary_labels.items():
            label.setText(str(summary.get(key, 0)))

    def _set_running(self, running: bool) -> None:
        self.prepare_button.setEnabled(not running)
        self.clear_button.setEnabled(not running)
        self.url_input.setReadOnly(running)
        self.makro_port.setEnabled(not running)
        self.source_port.setEnabled(not running)
        self.worker_count.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.save_check.setEnabled(not running)
        self.images_check.setEnabled(not running)
        if running:
            self.execute_button.setEnabled(False)
        else:
            self.execute_button.setEnabled(any(job.status == "READY" for job in self._jobs))

    def _set_state(self, text: str) -> None:
        self.state_label.setText(text)

    def _show_failure(self, text: str) -> None:
        QMessageBox.warning(self, "Batch", text)

    def _open_selected_job(self) -> None:
        row = self.table.currentRow()
        if not (0 <= row < len(self._jobs)):
            return
        _JobDetailDialog(self._jobs[row], self).exec()

    def _open_batch_dir(self) -> None:
        batch = self.controller.batch
        if batch is not None:
            _open_path(Path(batch.root_dir))


def _product_label(url: str) -> str:
    parsed = urlparse(url)
    tail = Path(parsed.path).name or parsed.netloc
    if len(tail) > 42:
        tail = tail[:39] + "..."
    return tail


def _open_path(path: Path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))


__all__ = ["BatchWorkspace"]
