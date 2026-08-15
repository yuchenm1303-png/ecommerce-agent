from __future__ import annotations

import re
from collections import deque
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .batch_model import (
    BATCH_WORKER_DEFAULT,
    BATCH_WORKER_MAX,
    BATCH_WORKER_MIN,
    BatchJob,
    normalize_batch_urls,
)
from .batch_runner import BatchController
from .readonly_runner import RunnerConfig


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

_STATUS_PALETTE = {
    "READY": ("#b4f1cf", "rgba(40, 150, 105, 0.24)"),
    "DONE": ("#b4f1cf", "rgba(40, 150, 105, 0.24)"),
    "REVIEW": ("#ffe0a0", "rgba(190, 132, 37, 0.24)"),
    "FAILED": ("#ffb2c0", "rgba(190, 63, 87, 0.25)"),
    "STOPPED": ("#ffe0a0", "rgba(190, 132, 37, 0.22)"),
}

_PHASES = (
    ("SOURCE", 8),
    ("PRODUCT", 25),
    ("VERTICAL", 42),
    ("BRAND", 58),
    ("RESOLVE", 76),
    ("EXECUTE", 82),
    ("VERIFY", 94),
)

_JOB_LOG_LINE = re.compile(r"^\[(JOB-\d+)(?:\s*·[^\]]+)?\]\s?(.*)$")
_BATCH_DETAIL_RATIO = (0.88, 0.86)
_MAX_JOB_LOG_LINES = 180


class BatchJobCard(QFrame):
    """Lightweight persistent card for one supplier URL / owned Makro tab."""

    def __init__(
        self,
        job_id: str,
        *,
        details_callback: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.job_id = job_id
        self._job: BatchJob | None = None
        self._details_callback = details_callback
        self._logs: deque[str] = deque(maxlen=_MAX_JOB_LOG_LINES)
        self._log_view: QPlainTextEdit | None = None
        self._expanded = False

        self.setObjectName("batchJobCard")
        self.setStyleSheet(
            """
            QFrame#batchJobCard {
                background: rgba(13, 29, 52, 82);
                border: 1px solid rgba(255, 255, 255, 32);
                border-radius: 14px;
            }
            QFrame#batchJobDetails {
                background: rgba(5, 15, 30, 72);
                border: 1px solid rgba(255, 255, 255, 22);
                border-radius: 10px;
            }
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(10)
        heading = QVBoxLayout()
        heading.setSpacing(1)
        self.job_label = QLabel(job_id)
        self.job_label.setObjectName("sectionEyebrow")
        self.product_label = QLabel("等待商品信息")
        self.product_label.setObjectName("cardTitle")
        self.product_label.setTextFormat(Qt.PlainText)
        self.product_label.setWordWrap(True)
        heading.addWidget(self.job_label)
        heading.addWidget(self.product_label)
        header.addLayout(heading, 1)

        self.progress_text = QLabel("0%")
        self.progress_text.setObjectName("cardHint")
        self.progress_text.setStyleSheet("font-weight: 700;")
        self.status_chip = QLabel("排队")
        self.status_chip.setTextFormat(Qt.PlainText)
        self.status_chip.setAlignment(Qt.AlignCenter)
        self.status_chip.setMinimumWidth(82)
        header.addWidget(self.progress_text, 0, Qt.AlignTop)
        header.addWidget(self.status_chip, 0, Qt.AlignTop)
        layout.addLayout(header)

        self.url_label = QLabel("—")
        self.url_label.setObjectName("cardHint")
        self.url_label.setTextFormat(Qt.PlainText)
        self.url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.url_label.setToolTip("Supplier URL")
        layout.addWidget(self.url_label)

        self.phase_label = QLabel()
        self.phase_label.setTextFormat(Qt.RichText)
        self.phase_label.setObjectName("cardHint")
        layout.addWidget(self.phase_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(7)
        self.progress_bar.setStyleSheet(
            """
            QProgressBar {
                border: 0;
                border-radius: 3px;
                background: rgba(255, 255, 255, 24);
            }
            QProgressBar::chunk {
                border-radius: 3px;
                background: rgba(150, 220, 255, 190);
            }
            """
        )
        layout.addWidget(self.progress_bar)

        self.meta_label = QLabel("Vertical —   ·   Brand —   ·   READY 0   ·   BLOCKED 0")
        self.meta_label.setObjectName("cardHint")
        self.meta_label.setTextFormat(Qt.PlainText)
        self.meta_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.meta_label.setWordWrap(True)
        layout.addWidget(self.meta_label)

        self.detail_label = QLabel("等待调度")
        self.detail_label.setTextFormat(Qt.PlainText)
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet("font-weight: 650; color: rgba(255,255,255,220);")
        layout.addWidget(self.detail_label)

        self.error_label = QLabel()
        self.error_label.setTextFormat(Qt.PlainText)
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(
            "color: #ffc1cc; background: rgba(180,45,72,45);"
            "border-radius: 7px; padding: 5px 8px;"
        )
        self.error_label.hide()
        layout.addWidget(self.error_label)

        log_row = QHBoxLayout()
        log_row.setSpacing(8)
        log_tag = QLabel("LIVE")
        log_tag.setObjectName("sectionEyebrow")
        self.log_preview = QLabel("等待任务日志…")
        self.log_preview.setTextFormat(Qt.PlainText)
        self.log_preview.setObjectName("cardHint")
        self.log_preview.setToolTip("最近一条任务日志")
        self.log_preview.setStyleSheet(
            "font-family: Consolas, 'Cascadia Mono', monospace; font-size: 11px;"
        )
        log_row.addWidget(log_tag)
        log_row.addWidget(self.log_preview, 1)
        layout.addLayout(log_row)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.open_url_button = QPushButton("商品链接")
        self.open_url_button.setObjectName("quietButton")
        self.open_url_button.clicked.connect(self._open_url)
        self.open_dir_button = QPushButton("Job 目录")
        self.open_dir_button.setObjectName("quietButton")
        self.open_dir_button.clicked.connect(self._open_dir)
        self.modal_button = QPushButton("详情窗口")
        self.modal_button.setObjectName("quietButton")
        self.modal_button.clicked.connect(lambda: self._details_callback(self.job_id))
        self.toggle_button = QPushButton("展开详情 / 日志")
        self.toggle_button.setObjectName("quietButton")
        self.toggle_button.clicked.connect(self._toggle_details)
        actions.addWidget(self.open_url_button)
        actions.addWidget(self.open_dir_button)
        actions.addWidget(self.modal_button)
        actions.addStretch(1)
        actions.addWidget(self.toggle_button)
        layout.addLayout(actions)

        self.details_box = QFrame()
        self.details_box.setObjectName("batchJobDetails")
        self.details_box.hide()
        self.details_layout = QVBoxLayout(self.details_box)
        self.details_layout.setContentsMargins(11, 9, 11, 10)
        self.details_layout.setSpacing(7)
        self.details_meta = QLabel()
        self.details_meta.setTextFormat(Qt.PlainText)
        self.details_meta.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.details_meta.setWordWrap(True)
        self.details_meta.setObjectName("cardHint")
        self.details_layout.addWidget(self.details_meta)
        layout.addWidget(self.details_box)

        self._render_phase(0)
        self._render_status("QUEUED")

    def update_job(self, job: BatchJob) -> None:
        self._job = job
        progress = max(0, min(100, int(job.progress)))
        product = job.product_name or _product_label(job.product_url)
        self.job_label.setText(f"{job.job_id} · OWNED PRODUCT TASK")
        self.product_label.setText(product)
        self.product_label.setToolTip(job.product_name or job.product_url)
        self.url_label.setText(job.product_url)
        self.url_label.setToolTip(job.product_url)
        self.progress_text.setText(f"{progress}%")
        self.progress_bar.setValue(progress)
        self._render_status(job.status)
        self._render_phase(progress)
        self.meta_label.setText(
            f"Vertical  {job.vertical or '—'}    ·    "
            f"Brand  {job.brand or '—'}    ·    "
            f"READY  {job.ready}    ·    "
            f"BLOCKED  {job.blocked}    ·    "
            f"Required  {job.required_blocked}    ·    "
            f"Images  {job.image_count}"
        )
        detail = job.stage_detail or _STAGE_LABELS.get(job.status, job.status)
        updated = _short_timestamp(job.updated_at)
        self.detail_label.setText(
            f"{_STAGE_LABELS.get(job.status, job.status)}  ·  {detail}"
            + (f"  ·  {updated}" if updated else "")
        )
        self.error_label.setVisible(bool(job.error))
        self.error_label.setText(job.error or "")
        self.open_dir_button.setEnabled(bool(job.run_dir))
        self.open_url_button.setEnabled(bool(job.product_url))
        self._update_details_meta()

    def append_log(self, line: str) -> None:
        clean = str(line or "").strip()
        if not clean:
            return
        self._logs.append(clean)
        preview = clean if len(clean) <= 190 else clean[:187] + "..."
        self.log_preview.setText(preview)
        self.log_preview.setToolTip(clean)
        if self._log_view is not None:
            self._log_view.appendPlainText(clean)

    def log_text(self) -> str:
        return "\n".join(self._logs)

    def _render_status(self, status: str) -> None:
        label = _STAGE_LABELS.get(status, status)
        foreground, background = _STATUS_PALETTE.get(status, ("#ccecff", "rgba(69, 151, 201, 0.22)"))
        self.status_chip.setText(label)
        self.status_chip.setStyleSheet(
            f"color: {foreground}; background: {background};"
            "border: 1px solid rgba(255,255,255,30);"
            "border-radius: 9px; padding: 4px 9px; font-weight: 720;"
        )

    def _render_phase(self, progress: int) -> None:
        active = 0
        for index, (_label, threshold) in enumerate(_PHASES):
            if progress >= threshold:
                active = index
        pieces: list[str] = []
        for index, (label, threshold) in enumerate(_PHASES):
            if progress >= min(100, threshold + 14):
                color = "rgba(180,241,207,0.94)"
                marker = "●"
            elif index == active:
                color = "rgba(183,226,255,0.98)"
                marker = "●"
            else:
                color = "rgba(255,255,255,0.38)"
                marker = "○"
            pieces.append(f'<span style="color:{color}; font-weight:650;">{marker} {label}</span>')
        self.phase_label.setText("&nbsp;&nbsp;&nbsp;".join(pieces))

    def _update_details_meta(self) -> None:
        job = self._job
        if job is None:
            return
        self.details_meta.setText(
            "\n".join(
                (
                    f"Supplier URL: {job.product_url}",
                    f"Makro targetId: {job.makro_target_id or '—'}",
                    f"Run directory: {job.run_dir or '—'}",
                    f"Execution report: {job.execution_report or '—'}",
                    f"Created: {job.created_at or '—'}",
                    f"Updated: {job.updated_at or '—'}",
                    f"Error / review reason: {job.error or '—'}",
                )
            )
        )

    def _ensure_log_view(self) -> None:
        if self._log_view is not None:
            return
        viewer = QPlainTextEdit()
        viewer.setObjectName("cardDetailTextView")
        viewer.setReadOnly(True)
        viewer.setMinimumHeight(170)
        viewer.document().setMaximumBlockCount(_MAX_JOB_LOG_LINES)
        viewer.setPlainText(self.log_text())
        self.details_layout.addWidget(viewer)
        self._log_view = viewer

    def _toggle_details(self) -> None:
        self._expanded = not self._expanded
        if self._expanded:
            self._ensure_log_view()
        self.details_box.setVisible(self._expanded)
        self.toggle_button.setText("收起详情 / 日志" if self._expanded else "展开详情 / 日志")

    def _open_url(self) -> None:
        if self._job is not None and self._job.product_url:
            QDesktopServices.openUrl(QUrl(self._job.product_url))

    def _open_dir(self) -> None:
        if self._job is not None and self._job.run_dir:
            _open_path(Path(self._job.run_dir).parent)


class BatchWorkspace(QWidget):
    """Multi-product control tower with one persistent surface per supplier URL."""

    def __init__(self, project_root: Path, *, busy_guard: Callable[[], bool] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project_root = project_root.resolve()
        self.busy_guard = busy_guard or (lambda: False)
        self.controller = BatchController(self.project_root, self)
        self._jobs: list[BatchJob] = []
        self._job_cards: dict[str, BatchJobCard] = {}
        self._pending_logs: dict[str, deque[str]] = {}
        self._batch_id = ""

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
        self.controller.log.connect(self._append_controller_log)
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
        hint = QLabel(
            f"每行一个 1688 / supplier URL。Source 串行预取；每个商品独立 owned Makro Tab，"
            f"Makro 准备和填写最高支持 {BATCH_WORKER_MAX} Workers 并行。"
        )
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
        self.worker_count.setRange(BATCH_WORKER_MIN, BATCH_WORKER_MAX)
        self.worker_count.setValue(BATCH_WORKER_DEFAULT)
        self.worker_count.setPrefix("Makro Workers  ")
        self.worker_count.setMaximumWidth(190)
        self.worker_count.setToolTip(
            f"Makro 准备/填写并行数：{BATCH_WORKER_MIN}-{BATCH_WORKER_MAX}。"
            "默认 6；并行越高，对浏览器、内存和接口并发要求越高。Source 采集始终串行。"
        )
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
        for key, title in (("total", "TOTAL"), ("processing", "PROCESSING"), ("ready", "READY"), ("done", "DONE"), ("review", "REVIEW"), ("failed", "FAILED")):
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
        layout.setSpacing(9)
        header_row = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        eyebrow = QLabel("JOB CONTROL · OWNED TAB ISOLATION · LIVE TELEMETRY")
        eyebrow.setObjectName("sectionEyebrow")
        title = QLabel("商品任务")
        title.setObjectName("cardTitle")
        title_box.addWidget(eyebrow)
        title_box.addWidget(title)
        self.job_count_label = QLabel("0 JOBS")
        self.job_count_label.setObjectName("sectionEyebrow")
        self.state_label = QLabel("Idle · 等待批量链接")
        self.state_label.setObjectName("cardHint")
        header_row.addLayout(title_box)
        header_row.addStretch(1)
        header_row.addWidget(self.job_count_label, 0, Qt.AlignTop)
        header_row.addSpacing(12)
        header_row.addWidget(self.state_label, 0, Qt.AlignTop)
        layout.addLayout(header_row)
        self.job_scroll = QScrollArea()
        self.job_scroll.setObjectName("batchJobScroll")
        self.job_scroll.setWidgetResizable(True)
        self.job_scroll.setFrameShape(QFrame.NoFrame)
        self.job_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.job_scroll.setStyleSheet("QScrollArea#batchJobScroll { background: transparent; border: none; } QScrollArea#batchJobScroll > QWidget > QWidget { background: transparent; }")
        self.jobs_host = QWidget()
        self.jobs_host.setObjectName("batchJobsHost")
        self.jobs_host.setStyleSheet("QWidget#batchJobsHost { background: transparent; }")
        self.jobs_layout = QVBoxLayout(self.jobs_host)
        self.jobs_layout.setContentsMargins(2, 2, 2, 2)
        self.jobs_layout.setSpacing(10)
        self.empty_state = QLabel("尚未创建商品任务\n批量准备后，每个链接会生成独立任务卡、owned tab 状态、实时进度和独立日志。")
        self.empty_state.setObjectName("cardHint")
        self.empty_state.setAlignment(Qt.AlignCenter)
        self.empty_state.setWordWrap(True)
        self.empty_state.setMinimumHeight(220)
        self.jobs_layout.addWidget(self.empty_state)
        self.jobs_layout.addStretch(1)
        self.job_scroll.setWidget(self.jobs_host)
        layout.addWidget(self.job_scroll, 1)
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
            self.controller.start_prepare(urls, config, prepare_concurrency=int(self.worker_count.value()))
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
            QMessageBox.warning(self, "需要 Save 授权", "批量 Full Step 3 会持久化草稿。请显式勾选“允许 Save + reopen”。")
            return
        answer = QMessageBox.question(
            self,
            "确认批量真实填写",
            f"即将执行 {len(ready)} 个 READY 商品。\n\n"
            f"Makro workers: {self.worker_count.value()}\n"
            "Save + reopen: ON\n"
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
        batch_id = self.controller.batch.batch_id if self.controller.batch is not None else ""
        if batch_id != self._batch_id:
            self._batch_id = batch_id
            self._clear_job_cards()
        seen: set[str] = set()
        for job in jobs:
            seen.add(job.job_id)
            card = self._job_cards.get(job.job_id)
            if card is None:
                card = BatchJobCard(job.job_id, details_callback=self._open_job_detail, parent=self.jobs_host)
                self._job_cards[job.job_id] = card
                self.jobs_layout.insertWidget(self.jobs_layout.count() - 1, card)
                pending = self._pending_logs.pop(job.job_id, deque())
                for line in pending:
                    card.append_log(line)
            card.update_job(job)
        for job_id in list(self._job_cards):
            if job_id in seen:
                continue
            card = self._job_cards.pop(job_id)
            card.setParent(None)
            card.deleteLater()
        self.empty_state.setVisible(not jobs)
        self.job_count_label.setText(f"{len(jobs)} JOBS")
        self.execute_button.setEnabled(not self.controller.is_running and any(job.status == "READY" for job in jobs))

    def _append_controller_log(self, text: str) -> None:
        match = _JOB_LOG_LINE.match(str(text or "").strip())
        if not match:
            return
        job_id, line = match.groups()
        card = self._job_cards.get(job_id)
        if card is not None:
            card.append_log(line)
            return
        pending = self._pending_logs.setdefault(job_id, deque(maxlen=_MAX_JOB_LOG_LINES))
        pending.append(line)

    def _clear_job_cards(self) -> None:
        for card in self._job_cards.values():
            card.setParent(None)
            card.deleteLater()
        self._job_cards.clear()
        self._pending_logs.clear()
        self.empty_state.setVisible(True)
        self.job_count_label.setText("0 JOBS")

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

    @staticmethod
    def _job_detail_text(job: BatchJob) -> str:
        return "\n".join(
            (
                f"Supplier URL\n{job.product_url}",
                f"\nStatus\n{_STAGE_LABELS.get(job.status, job.status)}",
                f"\nProgress\n{max(0, min(100, job.progress))}%",
                f"\nVertical\n{job.vertical or '—'}",
                f"\nBrand\n{job.brand or '—'}",
                f"\nREADY / BLOCKED\n{job.ready} / {job.blocked}",
                f"\nRequired blocked\n{job.required_blocked}",
                f"\nProduct images\n{job.image_count}",
                f"\nMakro targetId\n{job.makro_target_id or '—'}",
                f"\nRun directory\n{job.run_dir or '—'}",
                f"\nExecution report\n{job.execution_report or '—'}",
                f"\nCreated / Updated\n{job.created_at or '—'} / {job.updated_at or '—'}",
                f"\nLast detail\n{job.stage_detail or '—'}",
                f"\nError / review reason\n{job.error or '—'}",
            )
        )

    def _open_job_detail(self, job_id: str) -> None:
        job = next((item for item in self._jobs if item.job_id == job_id), None)
        if job is None:
            return
        if self._open_job_in_shared_modal(job):
            return
        QMessageBox.information(self, f"{job.job_id} · Batch Job", self._job_detail_text(job))

    def _open_job_in_shared_modal(self, job: BatchJob) -> bool:
        details = getattr(self.window(), "_card_details", None)
        open_custom = getattr(details, "open_custom", None)
        body_layout = getattr(details, "body_layout", None)
        if not callable(open_custom) or not isinstance(body_layout, QVBoxLayout):
            return False
        card = self._job_cards.get(job.job_id)
        live_log = card.log_text() if card is not None else ""

        def populate() -> None:
            summary = QLabel(
                f"{job.job_id}   ·   {_STAGE_LABELS.get(job.status, job.status)}   ·   "
                f"{max(0, min(100, job.progress))}%   ·   "
                f"READY {job.ready}   ·   BLOCKED {job.blocked}"
            )
            summary.setObjectName("modalMetaLabel")
            body_layout.addWidget(summary)
            detail = QPlainTextEdit()
            detail.setObjectName("cardDetailTextView")
            detail.setReadOnly(True)
            text = self._job_detail_text(job)
            if live_log:
                text += "\n\nLIVE JOB LOG\n" + live_log
            detail.setPlainText(text)
            detail.setMinimumHeight(420)
            body_layout.addWidget(detail, 1)
            row = QHBoxLayout()
            open_url = QPushButton("打开商品链接")
            open_url.setObjectName("quietButton")
            open_url.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(job.product_url)))
            row.addWidget(open_url)
            row.addStretch(1)
            open_dir = QPushButton("打开 Job 目录")
            open_dir.setObjectName("modalPrimaryButton")
            open_dir.clicked.connect(lambda: _open_path(Path(job.run_dir).parent))
            row.addWidget(open_dir)
            body_layout.addLayout(row)

        open_custom(
            title=job.product_name or _product_label(job.product_url),
            eyebrow=f"BATCH JOB · {job.job_id} · OWNED TAB",
            populate=populate,
            ratio=_BATCH_DETAIL_RATIO,
        )
        return True

    def _open_batch_dir(self) -> None:
        batch = self.controller.batch
        if batch is not None:
            _open_path(Path(batch.root_dir))


def _short_timestamp(value: str) -> str:
    text = str(value or "")
    if "T" not in text:
        return text
    tail = text.split("T", 1)[1]
    return tail.split("+", 1)[0]


def _product_label(url: str) -> str:
    parsed = urlparse(url)
    tail = Path(parsed.path).name or parsed.netloc
    if len(tail) > 42:
        tail = tail[:39] + "..."
    return tail


def _open_path(path: Path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))


__all__ = ["BatchJobCard", "BatchWorkspace"]
