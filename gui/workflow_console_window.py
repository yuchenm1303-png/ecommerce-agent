from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.listing_images import listing_images_from_resolver_outputs

from .batch_workspace import BatchWorkspace
from .console_window import MainWindow as ConsoleMainWindow
from .readonly_runner import RunnerConfig
from .real_execution import FULL_STEP3
from .result_loader import RunResult, latest_resolver_manifest


_STAGE_LABELS = {
    "scan": "Source Capture",
    "cold": "Step 1 · Vertical",
    "hot": "Step 2 · Brand",
    "plan": "Step 3 · Resolve / Fill Plan",
}

_AUTO_PRODUCT_PHOTO_LIMIT = 5


class WorkflowMainWindow(ConsoleMainWindow):
    """Single-product lab plus production-oriented Batch control tower."""

    def __init__(self, project_root: Path) -> None:
        super().__init__(project_root)
        self.setWindowTitle("ecommerce-agent · Listing Automation")
        self._relabel_acceptance_console()

    def _build_input_card(self) -> QFrame:
        card = super()._build_input_card()
        layout = card.layout()
        assert isinstance(layout, QVBoxLayout)

        self.start_button.setText("新建任务 · 从 0 完整准备")
        self.start_button.setToolTip(
            "把当前供应商 URL 作为一个全新的 Listing Job：始终新建专属 Makro 标签页，"
            "从首页 → Step 1 → Step 2 → Resolver cold/hot → read-only Fill Plan；"
            "绝不自动接管或恢复旧商品草稿，Step 3 准备阶段不写入。"
        )

        # Vertical is output from the current Step 1 selection, not a normal GUI
        # input. Diagnostic backend overrides still exist in CLI only.
        self.vertical_input.clear()
        self.vertical_input.setReadOnly(True)
        self.vertical_input.setPlaceholderText("Vertical · 自动识别后显示")
        self.vertical_input.setToolTip("由当前 Step 1 live candidates + AI 自动确定；GUI 不做硬编码覆盖。")

        for label in card.findChildren(QLabel):
            text = label.text()
            if "只输入一个 1688 / supplier 商品 URL" in text:
                label.setText(
                    "只输入一个 1688 / supplier 商品 URL。每次“新建任务”都会创建独立 Makro tab，"
                    "从 0 完成 Step 1/2；Step 3 准备阶段保持 0 Write / 0 Save / 0 QC。"
                )
            elif "只有完成 read-only 四阶段后才解锁" in text:
                label.setText(
                    "完成本任务 Step 3 Resolver + Fill Plan 后才解锁真实填写；"
                    "正式入口默认 Full Step 3，Save / 图片分别授权，Send to QC 永久锁定。"
                )

        # The formal execution path is Full Step 3. Single-section choices stay
        # available as diagnostics, but they are no longer the default action.
        full_index = self.real_scope_combo.findData(FULL_STEP3)
        if full_index >= 0:
            self.real_scope_combo.setCurrentIndex(full_index)
        self.real_scope_combo.currentIndexChanged.connect(self._sync_execution_mode_copy)

        self.real_upload_check.setText("上传本次商品图")
        self.real_upload_check.setToolTip(
            "勾选后默认使用当前 Resolver 通过 Listing Image 质量门的商品图片；不会把 source-page 截图当 listing 图片。"
        )
        self.real_pick_images_button.setText("手动覆盖图片…")
        self.real_pick_images_button.setToolTip(
            "可选。只有需要替换自动商品图时才手动选择；不选择时使用当前 Resolver 的合格 Listing Images。"
        )
        self.real_image_count.setText("AUTO · waiting")
        self.real_start_button.setText("一键填写全部 READY")
        self.real_policy_hint.setText(
            "正式入口默认 Full Step 3：填写全部 READY。Save 与图片仍需每件商品显式授权；"
            "勾选图片后自动复用当前 Resolver 的合格商品图，无需重新选文件；Send to QC 永久锁定。"
        )
        self._sync_execution_mode_copy()

        stage_row = QHBoxLayout()
        stage_row.setSpacing(8)
        self.step1_button = QPushButton("诊断① · Step 1")
        self.step2_button = QPushButton("诊断② · Step 2")
        self.step3_button = QPushButton("诊断③ · Step 3")
        self.step1_button.setToolTip("阶段诊断：测试 Step 1 / Vertical，不代表创建完整的新商品任务。")
        self.step2_button.setToolTip("阶段诊断：只对当前唯一 Step 2 页面测试 Brand 流程。")
        self.step3_button.setToolTip("阶段诊断：只对当前唯一 Step 3 页面测试 Resolver / Fill Plan。")
        for button in (self.step1_button, self.step2_button, self.step3_button):
            button.setObjectName("quietButton")
            stage_row.addWidget(button)
        stage_row.addStretch(1)

        self.step1_button.clicked.connect(lambda: self._start_mode("step1"))
        self.step2_button.clicked.connect(lambda: self._start_mode("step2"))
        self.step3_button.clicked.connect(lambda: self._start_mode("step3"))
        layout.insertLayout(2, stage_row)
        return card

    def install_mode_workspace(self) -> None:
        """Wrap the fully-polished Single UI and Batch UI in one instant stack.

        This is deliberately called by ``run_local_gui.py`` *after* the existing
        Single UI polish has created its splitters. That preserves the stable
        Single layout/performance work while still giving Batch a full-screen
        sibling workspace. Business widgets are moved, never reconstructed.
        """

        if hasattr(self, "mode_stack"):
            return
        root = self.centralWidget()
        outer = root.layout() if root is not None else None
        if root is None or not isinstance(outer, QVBoxLayout):
            raise RuntimeError("WorkflowMainWindow expected the preserved QVBoxLayout root")

        single_page = QWidget()
        single_page.setObjectName("singleWorkspace")
        single_layout = QVBoxLayout(single_page)
        single_layout.setContentsMargins(0, 0, 0, 0)
        single_layout.setSpacing(10)

        # Item 0 remains the common application header. ui_polish has already
        # converted the Single body into its stable splitters at this point.
        while outer.count() > 1:
            item = outer.takeAt(1)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                stretch = 1 if widget.objectName() in {"workspaceHost", "bodySplitter"} else 0
                single_layout.addWidget(widget, stretch)
            elif child_layout is not None:
                child_layout.setParent(None)
                single_layout.addLayout(child_layout)

        switch_card = QFrame()
        switch_card.setObjectName("microCard")
        switch_layout = QHBoxLayout(switch_card)
        switch_layout.setContentsMargins(7, 5, 7, 5)
        switch_layout.setSpacing(3)
        switch_layout.addStretch(1)
        self.single_mode_button = QPushButton("SINGLE")
        self.batch_mode_button = QPushButton("BATCH")
        for button in (self.single_mode_button, self.batch_mode_button):
            button.setCheckable(True)
            button.setObjectName("quietButton")
            button.setMinimumWidth(112)
            button.setStyleSheet(
                "QPushButton:checked {"
                "background-color: rgba(190, 113, 157, 190);"
                "border-color: rgba(255, 220, 239, 105);"
                "font-weight: 720;"
                "}"
            )
            switch_layout.addWidget(button)
        switch_layout.addStretch(1)

        self.mode_stack = QStackedWidget()
        self.mode_stack.setObjectName("modeStack")
        self.mode_stack.addWidget(single_page)
        self.batch_workspace = BatchWorkspace(
            self.project_root,
            busy_guard=self._single_is_busy,
            parent=self.mode_stack,
        )
        self.mode_stack.addWidget(self.batch_workspace)

        self.single_mode_button.clicked.connect(lambda: self._set_workspace_mode(0))
        self.batch_mode_button.clicked.connect(lambda: self._set_workspace_mode(1))
        self.batch_workspace.controller.state_changed.connect(self._batch_state_changed)

        outer.addWidget(switch_card)
        outer.addWidget(self.mode_stack, 1)
        self._set_workspace_mode(0)

    def _set_workspace_mode(self, index: int) -> None:
        index = 0 if int(index) <= 0 else 1
        self.mode_stack.setCurrentIndex(index)
        self.single_mode_button.setChecked(index == 0)
        self.batch_mode_button.setChecked(index == 1)
        self.open_run_button.setVisible(index == 0)
        if index == 0:
            if self.runner.is_running:
                self.phase_badge.setText("SINGLE · workflow running")
            elif getattr(self, "execution_runner", None) is not None and self.execution_runner.is_running:
                self.phase_badge.setText("SINGLE · real execution running")
            else:
                self.phase_badge.setText("SINGLE · ready")
        else:
            batch = self.batch_workspace.controller.batch
            if self.batch_workspace.is_running:
                self.phase_badge.setText("BATCH · running")
            elif batch is not None:
                summary = batch.summary()
                self.phase_badge.setText(
                    f"BATCH · {summary['ready']} ready · {summary['done']} done · {summary['review']} review"
                )
            else:
                self.phase_badge.setText("BATCH · ready for URLs")

    def _batch_state_changed(self, text: str) -> None:
        if hasattr(self, "mode_stack") and self.mode_stack.currentIndex() == 1:
            self.phase_badge.setText(f"BATCH · {text}")

    def _single_is_busy(self) -> bool:
        return bool(
            self.runner.is_running
            or (
                getattr(self, "execution_runner", None) is not None
                and self.execution_runner.is_running
            )
        )

    def _batch_is_busy(self) -> bool:
        return bool(
            hasattr(self, "batch_workspace")
            and self.batch_workspace.is_running
        )

    def _sync_execution_mode_copy(self, *_args: object) -> None:
        if not hasattr(self, "real_scope_combo") or not hasattr(self, "real_start_button"):
            return
        if self.real_scope_combo.currentData() == FULL_STEP3:
            self.real_start_button.setText("一键填写全部 READY")
            self.real_start_button.setToolTip(
                "执行全部 Step 3 READY 字段；按授权 Save + reopen，并可自动上传本次 Resolver 商品图。"
            )
        else:
            self.real_start_button.setText("运行单项诊断")
            self.real_start_button.setToolTip("仅执行当前选中的单 section / Product Photos，用于诊断。")

    def _relabel_acceptance_console(self) -> None:
        for key, title in _STAGE_LABELS.items():
            unit = self.console.phase_units.get(key)
            if unit is not None:
                unit.title.setText(title)
            row = self.console.phase_rows.get(key)
            if row is not None:
                number = {"scan": "01", "cold": "02", "hot": "03", "plan": "04"}[key]
                self.console.timeline.setItem(row, 0, self._table_item(f"{number} · {title}"))

        self.console.progress_detail.setText("idle · 新建完整任务 / 阶段诊断")
        self.console.diagnostics_view.setPlainText(
            "New task contract: supplier URL → fresh dedicated Makro tab → Step 1 Vertical → "
            "Step 2 Brand → Resolver cold/hot → read-only Fill Plan. "
            "The three numbered buttons are stage diagnostics and may inspect an existing stage page."
        )

    @staticmethod
    def _table_item(text: str):
        from PySide6.QtWidgets import QTableWidgetItem

        return QTableWidgetItem(text)

    def _current_resolver_product_images(self) -> tuple[Path, ...]:
        result = getattr(self, "current_result", None)
        if result is None:
            return ()
        manifest_path = latest_resolver_manifest(result.run_dir, "03-hot-resolver")
        if manifest_path is None or not manifest_path.is_file():
            return ()
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return ()
        outputs = manifest.get("outputs") or {}
        return listing_images_from_resolver_outputs(outputs)

    def _start_run(self) -> None:
        self._start_mode("full")

    def _start_mode(self, mode: str) -> None:
        if self._batch_is_busy():
            QMessageBox.warning(self, "无法开始 Single", "Batch worker 仍在运行。")
            return
        if getattr(self, "execution_runner", None) is not None and self.execution_runner.is_running:
            QMessageBox.warning(self, "无法开始", "真实 Step 3 执行仍在运行。")
            return

        config = RunnerConfig(
            product_url=self.url_input.text().strip(),
            makro_cdp_port=int(self.makro_port.value()),
            source_cdp_port=int(self.source_port.value()),
            source_use_current_page=self.current_page_check.isChecked(),
        )
        try:
            self._reset_result_views()
            self.runner.start(config, mode=mode)
            self.open_run_button.setEnabled(True)
        except Exception as exc:
            QMessageBox.critical(self, "无法开始测试", str(exc))

    def _set_running(self, running: bool) -> None:
        super()._set_running(running)
        for button in (self.step1_button, self.step2_button, self.step3_button):
            button.setEnabled(not running)
        if running:
            self.real_start_button.setEnabled(False)

    def _on_real_running(self, running: bool) -> None:
        super()._on_real_running(running)
        workflow_running = self.runner.is_running
        for button in (self.step1_button, self.step2_button, self.step3_button):
            button.setEnabled(not running and not workflow_running)

    def _reset_result_views(self) -> None:
        super()._reset_result_views()
        # Permissions are per product/run. Never carry Save/image authorization
        # or manually selected photos across products.
        self._selected_upload_images = []
        if hasattr(self, "real_save_check"):
            self.real_save_check.setChecked(False)
        if hasattr(self, "real_upload_check"):
            self.real_upload_check.setChecked(False)
        if hasattr(self, "real_scope_combo"):
            full_index = self.real_scope_combo.findData(FULL_STEP3)
            if full_index >= 0:
                self.real_scope_combo.setCurrentIndex(full_index)
        if hasattr(self, "real_image_count"):
            self.real_image_count.setText("AUTO · waiting")
            self.real_image_count.setToolTip("")
        self._sync_execution_mode_copy()

    def _apply_result(self, result: RunResult) -> None:
        super()._apply_result(result)
        if result.vertical:
            self.vertical_input.setText(result.vertical)
        if result.workflow_mode in {"step1", "step2"}:
            detail = f"vertical={result.vertical or '—'}"
            if result.brand:
                detail += f" · brand={result.brand}"
            self.fields_hint.setText(f"{result.workflow_mode} complete · {detail}")
        elif result.workflow_mode in {"step3", "full"} and result.plan_summary:
            self.fields_hint.setText(
                f"{result.workflow_mode} · live={result.live_field_count} · "
                f"READY={result.ready} · BLOCKED={result.blocked}"
            )

    def _unlock_real_execution(self, result: RunResult) -> None:
        images = self._current_resolver_product_images()
        if images:
            auto_count = min(len(images), _AUTO_PRODUCT_PHOTO_LIMIT)
            self.real_image_count.setText(f"AUTO {auto_count}/{len(images)}")
            self.real_image_count.setToolTip(
                "自动候选来自本次 Resolver primary_source_listing_images：\n"
                + "\n".join(str(path) for path in images[:_AUTO_PRODUCT_PHOTO_LIMIT])
            )
        else:
            self.real_image_count.setText("AUTO 0")
            self.real_image_count.setToolTip("本次 Resolver 没有通过 Listing Image 质量门的商品图片。")

        if not result.plan_summary or result.ready <= 0:
            self.real_start_button.setEnabled(False)
            self.real_policy_hint.setText(
                f"{result.workflow_mode or 'workflow'} 已完成，但尚无可执行的 Step 3 READY Fill Plan；真实填写保持锁定。"
            )
            return
        self.real_start_button.setEnabled(True)
        self.real_policy_hint.setText(
            f"当前 Step 3 Fill Plan 已通过：READY={result.ready}。默认 Full Step 3 会填写全部 READY；"
            f"本次可自动使用 {min(len(images), _AUTO_PRODUCT_PHOTO_LIMIT)} 张合格商品图。"
            "Save / 图片仍需显式授权，Send to QC 继续锁定。"
        )

    def _start_real_execution(self) -> None:
        if self._batch_is_busy():
            QMessageBox.warning(self, "无法开始 Single 真实填写", "Batch worker 仍在运行。")
            return
        if self.current_result is not None and self.current_result.vertical:
            self.vertical_input.setText(self.current_result.vertical)

        # Automatic Product Photos are always derived from the canonical listing
        # image gate. Raw resolver evidence remains available only for strict rebind.
        if self.real_upload_check.isChecked() and not self._selected_upload_images:
            images = self._current_resolver_product_images()
            if not images:
                QMessageBox.warning(
                    self,
                    "没有合格商品图片",
                    "本次 Resolver 没有通过 Listing Image 质量门的图片；不会拿原始证据图或网页截图冒充 Listing Photos。",
                )
                return
            selected = list(images[:_AUTO_PRODUCT_PHOTO_LIMIT])
            self._selected_upload_images = selected
            self.real_image_count.setText(f"AUTO {len(selected)}/{len(images)}")
            self.real_image_count.setToolTip("\n".join(str(path) for path in selected))

        super()._start_real_execution()
