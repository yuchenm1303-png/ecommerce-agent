from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from .console_window import MainWindow as ConsoleMainWindow
from .readonly_runner import RunnerConfig
from .result_loader import RunResult


_STAGE_LABELS = {
    "scan": "Source Capture",
    "cold": "Step 1 · Vertical",
    "hot": "Step 2 · Brand",
    "plan": "Step 3 · Resolve / Fill Plan",
}


class WorkflowMainWindow(ConsoleMainWindow):
    """Business-wiring upgrade layered over the preserved QWidget visual shell."""

    def __init__(self, project_root: Path) -> None:
        super().__init__(project_root)
        self.setWindowTitle("ecommerce-agent · Current Workflow Acceptance")
        self._relabel_acceptance_console()

    def _build_input_card(self) -> QFrame:
        card = super()._build_input_card()
        layout = card.layout()
        assert isinstance(layout, QVBoxLayout)

        self.start_button.setText("④ 完整流程准备")
        self.start_button.setToolTip(
            "从供应商 URL 自动完成 Step 1 → Step 2 → 当前 Resolver cold/hot → read-only Fill Plan；Step 3 不写入。"
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
                    "只输入一个 1688 / supplier 商品 URL。Step 1/2 使用当前 one-link 自动选择；"
                    "Step 3 准备阶段保持 0 Write / 0 Save / 0 QC。"
                )
            elif "只有完成 read-only 四阶段后才解锁" in text:
                label.setText(
                    "完成 Step 3 当前 Resolver + Fill Plan 后才解锁真实填写；"
                    "Save / 图片分别授权，Send to QC 永久锁定。"
                )

        stage_row = QHBoxLayout()
        stage_row.setSpacing(8)
        self.step1_button = QPushButton("① Step 1 · Vertical")
        self.step2_button = QPushButton("② Step 2 · Brand")
        self.step3_button = QPushButton("③ Step 3 · Resolve")
        for button in (self.step1_button, self.step2_button, self.step3_button):
            button.setObjectName("quietButton")
            stage_row.addWidget(button)
        stage_row.addStretch(1)

        self.step1_button.clicked.connect(lambda: self._start_mode("step1"))
        self.step2_button.clicked.connect(lambda: self._start_mode("step2"))
        self.step3_button.clicked.connect(lambda: self._start_mode("step3"))
        layout.insertLayout(2, stage_row)
        return card

    def _relabel_acceptance_console(self) -> None:
        for key, title in _STAGE_LABELS.items():
            unit = self.console.phase_units.get(key)
            if unit is not None:
                unit.title.setText(title)
            row = self.console.phase_rows.get(key)
            if row is not None:
                number = {"scan": "01", "cold": "02", "hot": "03", "plan": "04"}[key]
                self.console.timeline.setItem(row, 0, self._table_item(f"{number} · {title}"))

        self.console.progress_detail.setText("idle · choose Step 1 / Step 2 / Step 3 / full")
        self.console.diagnostics_view.setPlainText(
            "Current workflow: supplier evidence → Step 1 Vertical → Step 2 Brand → "
            "current Resolver cold/hot → read-only Fill Plan."
        )

    @staticmethod
    def _table_item(text: str):
        from PySide6.QtWidgets import QTableWidgetItem

        return QTableWidgetItem(text)

    def _start_run(self) -> None:
        self._start_mode("full")

    def _start_mode(self, mode: str) -> None:
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
        if not result.plan_summary or result.ready <= 0:
            self.real_start_button.setEnabled(False)
            self.real_policy_hint.setText(
                f"{result.workflow_mode or 'workflow'} 已完成，但尚无可执行的 Step 3 READY Fill Plan；真实填写保持锁定。"
            )
            return
        self.real_start_button.setEnabled(True)
        self.real_policy_hint.setText(
            f"当前 Step 3 Fill Plan 已通过：READY={result.ready}，真实填写已解锁。"
            "Save / 图片仍需显式授权，Send to QC 继续锁定。"
        )

    def _start_real_execution(self) -> None:
        if self.current_result is not None and self.current_result.vertical:
            self.vertical_input.setText(self.current_result.vertical)
        super()._start_real_execution()
