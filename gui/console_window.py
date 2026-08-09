from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from app.makro.listing_preflight import CORE_FORM_SECTIONS

from .acceptance_console import AcceptanceConsole
from .main_window import MainWindow as BaseMainWindow
from .main_window import STATUS_COLORS
from .real_execution import (
    FULL_STEP3,
    PRODUCT_PHOTOS,
    RealExecutionConfig,
    RealExecutionConsole,
    RealExecutionRunner,
)
from .result_loader import PhaseStats, RunResult


_AI_STATUS_COLORS = {
    **STATUS_COLORS,
    "BUSINESS_LOCKED": QColor("#d9a2c5"),
    "REVIEW": QColor("#b8b6ef"),
}


class MainWindow(BaseMainWindow):
    """Primary acceptance UI with read-only telemetry and gated real execution."""

    def __init__(self, project_root: Path) -> None:
        self._selected_upload_images: list[Path] = []
        super().__init__(project_root)

        self.execution_runner = RealExecutionRunner(project_root, self)
        self.execution_console = RealExecutionConsole(self.execution_runner)
        self.console.tabs.addTab(self.execution_console, "Real Execution")

        self.execution_runner.running_changed.connect(self._on_real_running)
        self.execution_runner.progress_changed.connect(self._on_real_progress)
        self.execution_runner.completed.connect(self._on_real_completed)
        self.execution_runner.failed.connect(self._on_real_failed)
        self.runner.completed.connect(self._unlock_real_execution)

        self.real_start_button.setEnabled(False)
        self._sync_real_controls()

        self.setWindowTitle("ecommerce-agent · Acceptance Control Console")
        self.resize(1600, 1080)
        self.setMinimumSize(1240, 860)

    def _build_input_card(self) -> QFrame:
        card = super()._build_input_card()
        layout = card.layout()
        assert isinstance(layout, QVBoxLayout)

        heading = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        eyebrow = QLabel("REAL BROWSER ACCEPTANCE · EXPLICIT PERMISSIONS")
        eyebrow.setObjectName("sectionEyebrow")
        title = QLabel("真实网页填写验收")
        title.setObjectName("cardTitle")
        title_box.addWidget(eyebrow)
        title_box.addWidget(title)
        heading.addLayout(title_box)
        heading.addSpacing(12)
        hint = QLabel(
            "只有完成 read-only 四阶段后才解锁。真实填写 / Save / 图片分别授权；Send to QC 受仓库策略锁定。"
        )
        hint.setObjectName("cardHint")
        hint.setWordWrap(True)
        heading.addWidget(hint, 1, Qt.AlignBottom)
        layout.addLayout(heading)

        controls = QHBoxLayout()
        controls.setSpacing(9)

        self.real_scope_combo = QComboBox()
        self.real_scope_combo.setMinimumWidth(270)
        for section in CORE_FORM_SECTIONS:
            self.real_scope_combo.addItem(f"Single · {section}", section)
        self.real_scope_combo.addItem(f"Single · {PRODUCT_PHOTOS}", PRODUCT_PHOTOS)
        self.real_scope_combo.addItem("Full Step 3 · persisted acceptance", FULL_STEP3)
        if len(CORE_FORM_SECTIONS) > 1:
            self.real_scope_combo.setCurrentIndex(1)
        self.real_scope_combo.currentIndexChanged.connect(self._sync_real_controls)
        controls.addWidget(self.real_scope_combo)

        self.real_save_check = QCheckBox("允许 Save + reopen")
        self.real_save_check.setChecked(False)
        controls.addWidget(self.real_save_check)

        self.real_upload_check = QCheckBox("上传图片")
        self.real_upload_check.setChecked(False)
        self.real_upload_check.toggled.connect(self._sync_real_controls)
        controls.addWidget(self.real_upload_check)

        self.real_pick_images_button = QPushButton("选择图片…")
        self.real_pick_images_button.setObjectName("quietButton")
        self.real_pick_images_button.clicked.connect(self._pick_upload_images)
        controls.addWidget(self.real_pick_images_button)

        self.real_image_count = QLabel("0 files")
        self.real_image_count.setObjectName("cardHint")
        controls.addWidget(self.real_image_count)

        self.real_qc_check = QCheckBox("Send to QC · LOCKED")
        self.real_qc_check.setChecked(False)
        self.real_qc_check.setEnabled(False)
        self.real_qc_check.setToolTip("AGENTS.md 明确禁止自动点击 Send to QC。")
        controls.addWidget(self.real_qc_check)

        controls.addStretch(1)

        self.real_start_button = QPushButton("真实填写测试")
        self.real_start_button.setObjectName("primaryButton")
        self.real_start_button.clicked.connect(self._start_real_execution)
        controls.addWidget(self.real_start_button)

        self.real_stop_button = QPushButton("停止真实测试")
        self.real_stop_button.setObjectName("dangerButton")
        self.real_stop_button.setEnabled(False)
        self.real_stop_button.clicked.connect(self._stop_real_execution)
        controls.addWidget(self.real_stop_button)

        layout.addLayout(controls)

        self.real_policy_hint = QLabel(
            "默认：Single section / no Save / no image upload / QC locked。"
            "Full Step 3 必须显式开启 Save；图片只有勾选并选择文件后才会传入 executor。"
        )
        self.real_policy_hint.setObjectName("cardHint")
        self.real_policy_hint.setWordWrap(True)
        layout.addWidget(self.real_policy_hint)
        return card

    def _build_log_card(self) -> QFrame:
        self.console = AcceptanceConsole(self.runner)
        # Keep the existing buffered log presenter contract: it receives the
        # console's Live Console view as the one canonical read-only log widget.
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

    def _sync_real_controls(self, *_args: object) -> None:
        scope = self.real_scope_combo.currentData()
        full = scope == FULL_STEP3
        photos_supported = full or scope == PRODUCT_PHOTOS
        real_running = bool(getattr(self, "execution_runner", None) and self.execution_runner.is_running)

        self.real_save_check.setEnabled(full and not real_running)
        if not full:
            self.real_save_check.setChecked(False)

        self.real_upload_check.setEnabled(photos_supported and not real_running)
        if not photos_supported:
            self.real_upload_check.setChecked(False)

        self.real_pick_images_button.setEnabled(
            photos_supported and self.real_upload_check.isChecked() and not real_running
        )
        self.real_scope_combo.setEnabled(not real_running)

    def _pick_upload_images(self) -> None:
        files, _selected_filter = QFileDialog.getOpenFileNames(
            self,
            "选择要上传到 Product Photos 的图片",
            "",
            "Images (*.jpg *.jpeg *.png *.webp);;All files (*.*)",
        )
        if not files:
            return
        self._selected_upload_images = [Path(value).resolve() for value in files]
        self.real_image_count.setText(f"{len(self._selected_upload_images)} files")
        self.real_image_count.setToolTip("\n".join(str(path) for path in self._selected_upload_images))

    def _unlock_real_execution(self, result: RunResult) -> None:
        if result.ready <= 0:
            self.real_start_button.setEnabled(False)
            self.real_policy_hint.setText("read-only 已完成，但没有 READY 字段；真实填写保持锁定。")
            return
        self.real_start_button.setEnabled(True)
        self.real_policy_hint.setText(
            f"read-only acceptance 已通过：READY={result.ready}，真实执行已解锁。"
            "默认仍是 no-save；所有写入结果会进入 Real Execution 控制台。"
        )

    def _start_real_execution(self) -> None:
        if self.runner.is_running:
            QMessageBox.warning(self, "无法开始真实测试", "read-only acceptance 仍在运行。")
            return
        if self.current_result is None or not self.current_result.plan_summary:
            QMessageBox.warning(self, "无法开始真实测试", "请先完整跑通 read-only 四阶段验收。")
            return

        scope = str(self.real_scope_combo.currentData() or "")
        allow_save = self.real_save_check.isChecked()
        if scope == FULL_STEP3 and not allow_save:
            QMessageBox.warning(
                self,
                "Full Step 3 需要 Save 授权",
                "Full Step 3 是持久化验收。请显式勾选“允许 Save + reopen”，"
                "或者改选 Single section 做 no-save 真实预览。",
            )
            return

        upload_images: tuple[Path, ...] = ()
        if self.real_upload_check.isChecked():
            if not self._selected_upload_images:
                QMessageBox.warning(self, "未选择图片", "已开启“上传图片”，请先选择实际 listing 图片。")
                return
            missing = [path for path in self._selected_upload_images if not path.is_file()]
            if missing:
                QMessageBox.warning(self, "图片不存在", "\n".join(str(path) for path in missing))
                return
            upload_images = tuple(self._selected_upload_images)

        scope_text = self.real_scope_combo.currentText()
        action_text = ["真实填写当前 Makro 页面"]
        action_text.append("Save + reopen verification" if allow_save else "NO SAVE")
        action_text.append(f"上传 {len(upload_images)} 张图片" if upload_images else "NO IMAGE UPLOAD")
        action_text.append("Send to QC = LOCKED / FALSE")
        answer = QMessageBox.question(
            self,
            "确认真实网页操作",
            f"Scope: {scope_text}\n\n" + "\n".join(action_text) + "\n\n确认开始？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        config = RealExecutionConfig(
            read_only_run_dir=self.current_result.run_dir,
            scope=scope,
            expected_vertical=self.vertical_input.text().strip(),
            makro_cdp_port=self.makro_port.value(),
            allow_save=allow_save,
            upload_images=upload_images,
        )
        try:
            self.console.tabs.setCurrentWidget(self.execution_console)
            self.execution_runner.start(config)
        except Exception as exc:
            QMessageBox.critical(self, "真实测试无法启动", str(exc))

    def _stop_real_execution(self) -> None:
        if getattr(self, "execution_runner", None) is not None:
            self.execution_runner.stop()

    def _on_real_running(self, running: bool) -> None:
        self.real_start_button.setEnabled(
            not running and self.current_result is not None and bool(self.current_result.plan_summary)
        )
        self.real_stop_button.setEnabled(running)
        self.start_button.setEnabled(not running and not self.runner.is_running)
        self.url_input.setEnabled(not running and not self.runner.is_running)
        self.makro_port.setEnabled(not running and not self.runner.is_running)
        self.source_port.setEnabled(not running and not self.runner.is_running)
        self.vertical_input.setEnabled(not running and not self.runner.is_running)
        self.current_page_check.setEnabled(not running and not self.runner.is_running)
        self._sync_real_controls()
        if running:
            self.phase_badge.setText("REAL · browser execution running")

    def _on_real_progress(self, percent: int, text: str) -> None:
        self.phase_badge.setText(f"REAL {max(0, min(100, percent))}% · {text}")

    def _on_real_completed(self, report: dict[str, object]) -> None:
        totals = report.get("field_totals") or {}
        if not isinstance(totals, dict):
            totals = {}
        self.phase_badge.setText(
            "REAL complete · attempted={} · validated={} · persisted={}".format(
                totals.get("writes_attempted", 0),
                totals.get("validated", 0),
                totals.get("persisted_verified", 0),
            )
        )
        self.console.tabs.setCurrentWidget(self.execution_console)

    def _on_real_failed(self, message: str) -> None:
        self.phase_badge.setText("REAL failed")
        self.console.tabs.setCurrentWidget(self.execution_console)
        QMessageBox.warning(self, "真实网页验收未完成", message)

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
        if hasattr(self, "real_start_button"):
            self.real_start_button.setEnabled(False)

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
