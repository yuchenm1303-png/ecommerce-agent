from __future__ import annotations

import re
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import (
    QAbstractButton,
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QWidget,
)

from .real_execution import FULL_STEP3, PRODUCT_PHOTOS


_PHASE_TITLES = {
    "scan": "采集商品",
    "cold": "识别类目",
    "hot": "识别品牌",
    "plan": "生成填写方案",
}

_JOB_STATUS = {
    "QUEUED": "等待处理",
    "CAPTURING": "采集商品",
    "UNDERSTANDING": "识别商品",
    "SELECTING_VERTICAL": "识别类目",
    "SELECTING_BRAND": "识别品牌",
    "RESOLVING": "生成填写方案",
    "READY": "可填写",
    "FILLING": "填写中",
    "UPLOADING_IMAGES": "上传图片",
    "SAVING": "保存中",
    "VERIFYING": "复核中",
    "DONE": "已完成",
    "REVIEW": "待处理",
    "FAILED": "失败",
    "STOPPED": "已停止",
    "PAUSED": "已暂停",
}

_PHASE_STATE = {
    "WAITING": "等待",
    "RUNNING": "进行中",
    "COMPLETED": "完成",
    "FAILED": "失败",
    "CANCELLED": "已取消",
    "SKIPPED": "已跳过",
}

_ACTIVITY_MODE = {
    "STANDBY": "待命",
    "PREPARING": "准备中",
    "READY": "可填写",
    "FILLING": "填写中",
    "COMPLETE": "已完成",
    "FAILED": "失败",
}

_RUNTIME_STATE = {
    "IDLE": "● 待命",
    "RUNNING": "● 进行中",
    "READY": "● 已准备",
    "AI_ANALYZING": "◉ 正在分析",
    "RECOVERING": "◉ 正在恢复",
    "WAITING_FOR_USER": "⚠ 需要处理",
    "RECOVERED": "✓ 已恢复",
    "WARNING": "⚠ 请注意",
    "FAILED": "× 已安全停止",
    "COMPLETE": "✓ 已完成",
}

_BROWSER_STATE = {
    "CHECKING": "正在检查",
    "READY": "已连接",
    "STARTING": "正在启动",
    "LOGIN": "需要登录",
    "OFFLINE": "连接已中断",
    "ERROR": "启动失败",
}

_SECTION_NAMES = {
    "Price, Stock and Shipping Information": "价格、库存与配送",
    "Product Description": "商品描述",
    "Additional Description": "补充信息",
    PRODUCT_PHOTOS: "商品图片",
}

_EXACT_COPY = {
    "LOCAL DEVELOPMENT  ·  ZERO-WRITE ACCEPTANCE": "MAKRO 商品上架",
    "ecommerce-agent": "商品上架助手",
    "PRODUCT SOURCE": "商品信息",
    "商品来源": "商品链接",
    "FIELD RESOLUTION": "字段检查",
    "FIELD RESOLUTION · FULL TRACE": "字段检查",
    "字段决策与最终 Gate": "字段检查",
    "RUN DIAGNOSTICS": "运行详情",
    "RUN DIAGNOSTICS · MODEL / CACHE": "运行详情",
    "Resolver Telemetry": "处理状态",
    "Local / Cache": "处理状态",
    "ENTITY MATCH": "参考资料",
    "Web candidates": "资料核对",
    "ZERO-WRITE CONTRACT": "操作权限",
    "Makro write safety": "安全状态",
    "LIVE CONSOLE": "任务记录",
    "实时运行日志": "运行记录",
    "ACCEPTANCE CONTROL CONSOLE": "任务进度",
    "运行控制台 · 真实阶段 / 命令 / 产物 / 日志": "查看准备、填写和验证过程",
    "REAL BROWSER ACCEPTANCE · EXPLICIT PERMISSIONS": "填写设置",
    "真实网页填写验收": "填写设置",
    "Live Console": "运行记录",
    "Timeline": "步骤",
    "Commands / Artifacts": "文件",
    "Diagnostics": "详情",
    "Real Execution": "填写记录",
    "Telemetry": "运行详情",
    "Web": "参考资料",
    "Safety": "安全",
    "Console": "运行记录",
    "Artifacts": "文件",
    "Real Run": "填写记录",
    "JOB CONTROL": "任务操作",
    "Job 目录": "任务目录",
    "详情窗口": "查看详情",
    "展开详情 / 日志": "查看详情",
    "收起详情 / 日志": "收起详情",
    "单独真实填写": "填写此商品",
    "批量填写 READY": "填写全部可用商品",
    "打开 Batch 目录": "打开批次目录",
    "打开结果目录": "打开任务目录",
    "REAL": "填写",
    "LIVE": "实时",
    "0 files": "0 张图片",
}


def _set_text(widget: Any, text: str) -> None:
    if widget is not None and hasattr(widget, "text") and hasattr(widget, "setText"):
        try:
            if widget.text() != text:
                widget.setText(text)
        except RuntimeError:
            pass


def _humanize(text: str) -> str:
    value = str(text or "")
    replacements = (
        ("STEP 3 CURRENT READ-ONLY FILL PLAN", "正在生成填写方案"),
        ("STEP 3 CURRENT RESOLVER · HOT/CACHE", "正在补充字段"),
        ("STEP 3 CURRENT RESOLVER · COLD", "正在识别字段"),
        ("Step 3 · Resolve / Fill Plan", "生成填写方案"),
        ("Step 3 Resolver + Fill Plan", "填写方案"),
        ("Step 1 · Vertical", "识别类目"),
        ("Step 2 · Brand", "识别品牌"),
        ("Source Capture / Product Identity", "采集并识别商品"),
        ("Source Capture", "采集商品"),
        ("Full Step 3", "完整填写"),
        ("Product Photos", "商品图片"),
        ("Save + reopen verification", "保存并复核"),
        ("Save + reopen", "保存并复核"),
        ("Send to QC", "送审"),
        ("read-only acceptance", "商品准备"),
        ("read-only", "准备"),
        ("Resolver Hot/Cache", "补充字段"),
        ("Resolver Cold", "识别字段"),
        ("Resolver", "字段匹配"),
        ("Fill Plan", "填写方案"),
        ("Live Schema", "页面字段"),
        ("live schema", "页面字段"),
        ("live candidates", "页面候选项"),
        ("primary_source_product_images", "已采集商品图片"),
        ("owned tabs", "独立标签页"),
        ("owned tab", "独立标签页"),
        ("owned-tab", "独立标签页"),
        ("targetId", "标签页"),
        ("Shadow Mode", "仅提示，不自动操作"),
        ("Runtime Supervisor", "任务助手"),
        ("Recovery", "恢复"),
        ("Real Execution", "实际填写"),
        ("browser execution", "网页填写"),
        ("AI still running", "正在识别"),
        ("AI处理中", "正在识别"),
        ("AI 请求已发送", "正在识别"),
        ("AI 已建立连接", "正在识别"),
        ("AI 已返回首段结果", "正在生成结果"),
        ("AI 响应完成", "识别完成"),
        ("Model calls", "识别调用"),
        ("Source cache", "商品缓存"),
        ("Web cache", "资料缓存"),
        ("Fields / Plan", "字段 / 方案"),
        ("Cold Local", "首次识别"),
        ("Cold Web", "首次资料核对"),
        ("Hot Local", "快速复用"),
        ("Hot Web", "快速资料核对"),
        ("Hot/Cache", "快速复用"),
        ("Gate", "检查"),
        ("blocked", "暂不可填"),
        ("candidate", "参考资料"),
        ("candidates", "参考资料"),
    )
    for old, new in replacements:
        value = value.replace(old, new)
    value = re.sub(r"\bAI\b", "识别", value)
    value = re.sub(r"\bworkflow\b", "流程", value, flags=re.IGNORECASE)
    value = re.sub(r"\bworker(s)?\b", "并行任务", value, flags=re.IGNORECASE)
    return value


class ProductCopyController(QObject):
    """Product-facing copy layer; business state and stored data remain untouched."""

    _COALESCE_MS = 24

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self._assistant: QWidget | None = None
        self._activity_wrapped = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self._COALESCE_MS)
        self._timer.timeout.connect(self.refresh)

        self._apply_static_copy()
        self._wrap_activity_presence()
        self._connect_updates()
        self.refresh()

    def _connect(self, signal: Any, callback: Callable[..., None] | None = None) -> None:
        if signal is None or not hasattr(signal, "connect"):
            return
        try:
            signal.connect(callback or (lambda *_args: self.schedule()))
        except (RuntimeError, TypeError):
            pass

    def _connect_updates(self) -> None:
        runner = getattr(self.window, "runner", None)
        if runner is not None:
            for name in (
                "running_changed",
                "progress_changed",
                "phase_event",
                "result_updated",
                "completed",
                "failed",
            ):
                self._connect(getattr(runner, name, None))
            self._connect(getattr(runner, "log", None), self._on_runner_log)

        real = getattr(self.window, "execution_runner", None)
        if real is not None:
            for name in ("running_changed", "progress_changed", "completed", "failed"):
                self._connect(getattr(real, name, None))

        workspace = getattr(self.window, "batch_workspace", None)
        controller = getattr(workspace, "controller", None)
        if controller is not None:
            for name in ("jobs_changed", "summary_changed", "running_changed", "state_changed"):
                self._connect(getattr(controller, name, None))

        browser = getattr(self.window, "_managed_makro_browser", None)
        self._connect(getattr(browser, "status_changed", None))

        mode_stack = getattr(self.window, "mode_stack", None)
        self._connect(getattr(mode_stack, "currentChanged", None))

    def _on_runner_log(self, line: str) -> None:
        text = str(line or "")
        if any(marker in text for marker in ("STEP 3", "AI ", "GUI WORKFLOW", "vertical 安全校验")):
            self.schedule()

    def schedule(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def attach_runtime_assistant(self, assistant: QWidget) -> None:
        self._assistant = assistant
        bridge = getattr(self.window, "_runtime_event_bridge", None)
        self._connect(getattr(bridge, "event_emitted", None))
        self._apply_runtime_assistant()

    def _apply_static_copy(self) -> None:
        self.window.setWindowTitle("ecommerce-agent · 商品上架助手")
        for widget in self.window.findChildren(QLabel):
            replacement = _EXACT_COPY.get(widget.text())
            if replacement is not None:
                widget.setText(replacement)
        for widget in self.window.findChildren(QAbstractButton):
            replacement = _EXACT_COPY.get(widget.text())
            if replacement is not None:
                widget.setText(replacement)

        brand = self.window.findChild(QLabel, "brandMark")
        title = self.window.findChild(QLabel, "appTitle")
        subtle = self.window.findChild(QLabel, "subtle")
        _set_text(brand, "MAKRO 商品上架")
        _set_text(title, "商品上架助手")
        _set_text(subtle, "从商品链接到 Makro 草稿，一站式准备与填写")

        for label in self.window.findChildren(QLabel):
            text = label.text()
            if "只输入一个 1688 / supplier 商品 URL" in text:
                label.setText("粘贴供应商商品链接，系统会自动识别类目、品牌并生成填写方案。")
            elif "只有完成 read-only 四阶段后才解锁" in text:
                label.setText("商品准备完成后即可填写。保存和图片由你确认，送审需手动完成。")
            elif text == "完整 subprocess stdout/stderr；批量刷新，避免高频日志拖慢 UI":
                label.setText("任务运行信息")
            elif text == "每个链接独立任务 · 第 5 条起滚动":
                label.setText("每个链接单独处理")

        source_port = getattr(self.window, "source_port", None)
        if isinstance(source_port, QSpinBox):
            source_port.setPrefix("采集浏览器  ")
        current_page = getattr(self.window, "current_page_check", None)
        if isinstance(current_page, QCheckBox):
            current_page.setText("使用当前采集页面")
            current_page.setToolTip("仅在当前采集页面已经打开正确商品时使用。")

        vertical_input = getattr(self.window, "vertical_input", None)
        if isinstance(vertical_input, QLineEdit):
            vertical_input.setPlaceholderText("类目将在识别后显示")
            vertical_input.setToolTip("系统根据商品信息自动识别，无需手动填写。")

        self._apply_real_controls_static()
        self._apply_console_static()
        self._apply_batch_static()
        self._apply_mode_switch()

    def _apply_real_controls_static(self) -> None:
        start = getattr(self.window, "start_button", None)
        stop = getattr(self.window, "stop_button", None)
        _set_text(start, "一键准备商品")
        if start is not None:
            start.setToolTip("自动采集商品信息并生成可填写方案。")
        _set_text(stop, "停止")

        for name, text in (
            ("step1_button", "识别类目"),
            ("step2_button", "识别品牌"),
            ("step3_button", "生成填写方案"),
        ):
            button = getattr(self.window, name, None)
            _set_text(button, text)

        _set_text(getattr(self.window, "real_save_check", None), "填写后保存并复核")
        _set_text(getattr(self.window, "real_upload_check", None), "上传商品图片")
        _set_text(getattr(self.window, "real_pick_images_button", None), "选择图片…")
        _set_text(getattr(self.window, "real_qc_check", None), "送审需手动完成")
        _set_text(getattr(self.window, "real_start_button", None), "开始填写")
        _set_text(getattr(self.window, "real_stop_button", None), "停止填写")
        settings = getattr(self.window, "real_settings_toggle", None)
        _set_text(settings, "填写设置")

        combo = getattr(self.window, "real_scope_combo", None)
        if isinstance(combo, QComboBox):
            for index in range(combo.count()):
                data = combo.itemData(index)
                if data == FULL_STEP3:
                    combo.setItemText(index, "完整填写 · 全部可用字段")
                    continue
                if data == PRODUCT_PHOTOS:
                    combo.setItemText(index, "商品图片")
                    continue
                raw = combo.itemText(index)
                section = raw.split("·", 1)[-1].strip()
                section = _SECTION_NAMES.get(section, section)
                combo.setItemText(index, f"单项填写 · {section}")

        field_table = getattr(self.window, "field_table", None)
        if isinstance(field_table, QTableWidget):
            if field_table.columnCount() >= 7:
                field_table.setHorizontalHeaderLabels(
                    ["字段", "识别状态", "建议值", "填写状态", "说明", "依据", "字段编号"]
                )
            elif field_table.columnCount() >= 5:
                field_table.setHorizontalHeaderLabels(["字段", "建议值", "填写状态", "说明", "依据"])

        web_table = getattr(self.window, "web_table", None)
        if isinstance(web_table, QTableWidget) and web_table.columnCount() >= 3:
            web_table.setHorizontalHeaderLabels(["判定", "资料来源", "说明"])

        tabs = getattr(self.window, "side_detail_tabs", None)
        if isinstance(tabs, QTabWidget):
            for index, text in enumerate(("运行详情", "参考资料", "安全")):
                if index < tabs.count():
                    tabs.setTabText(index, text)

        self._apply_status_cards()

    def _apply_status_cards(self) -> None:
        items = (
            ("ready_card", "可填写", "已生成填写方案"),
            ("missing_card", "待补充", "缺少商品信息"),
            ("conflict_card", "需确认", "存在冲突信息"),
            ("blocked_card", "暂不可填", "需要补充或确认"),
        )
        for name, title, caption in items:
            card = getattr(self.window, name, None)
            layout = card.layout() if isinstance(card, QWidget) else None
            if layout is None or layout.count() < 3:
                continue
            title_label = layout.itemAt(1).widget()
            caption_label = layout.itemAt(2).widget()
            _set_text(title_label, title)
            _set_text(caption_label, caption)

    def _apply_console_static(self) -> None:
        console = getattr(self.window, "console", None)
        if not isinstance(console, QWidget):
            return
        for label in console.findChildren(QLabel):
            replacement = _EXACT_COPY.get(label.text())
            if replacement is not None:
                label.setText(replacement)
        tabs = getattr(console, "tabs", None)
        if isinstance(tabs, QTabWidget):
            names = ("运行记录", "步骤", "文件", "详情", "填写记录")
            for index, text in enumerate(names):
                if index < tabs.count():
                    tabs.setTabText(index, text)

        phase_units = getattr(console, "phase_units", {})
        for key, title in _PHASE_TITLES.items():
            unit = phase_units.get(key) if isinstance(phase_units, dict) else None
            _set_text(getattr(unit, "title", None), title)

        timeline = getattr(console, "timeline", None)
        if isinstance(timeline, QTableWidget) and timeline.columnCount() >= 6:
            timeline.setHorizontalHeaderLabels(["步骤", "状态", "耗时", "结果", "开始时间", "输出"])

        artifact = getattr(console, "artifact_table", None)
        if isinstance(artifact, QTableWidget) and artifact.columnCount() >= 3:
            artifact.setHorizontalHeaderLabels(["类型", "文件", "大小"])

        command = getattr(console, "command_view", None)
        if isinstance(command, QPlainTextEdit):
            command.setPlaceholderText("任务执行信息会显示在这里")

        diagnostics = getattr(console, "diagnostics_view", None)
        if isinstance(diagnostics, QPlainTextEdit):
            text = diagnostics.toPlainText()
            if text.startswith("Current workflow:") or text == "等待 acceptance 数据…":
                diagnostics.setPlainText("当前流程：采集商品 → 识别类目 → 识别品牌 → 生成填写方案。")

    def _apply_batch_static(self) -> None:
        workspace = getattr(self.window, "batch_workspace", None)
        if not isinstance(workspace, QWidget):
            return
        source_port = getattr(workspace, "source_port", None)
        if isinstance(source_port, QSpinBox):
            source_port.setPrefix("采集浏览器  ")
        worker_count = getattr(workspace, "worker_count", None)
        if isinstance(worker_count, QSpinBox):
            worker_count.setPrefix("并行任务  ")

        _set_text(getattr(workspace, "clear_button", None), "清空链接")
        _set_text(getattr(workspace, "prepare_button", None), "开始批量准备")
        _set_text(getattr(workspace, "save_check", None), "填写后保存并复核")
        _set_text(getattr(workspace, "images_check", None), "上传商品图片")
        _set_text(getattr(workspace, "qc_check", None), "送审需手动完成")
        _set_text(getattr(workspace, "open_batch_button", None), "打开批次目录")
        _set_text(getattr(workspace, "execute_button", None), "填写全部可用商品")

        for label in workspace.findChildren(QLabel):
            text = label.text()
            replacement = _EXACT_COPY.get(text)
            if replacement is not None:
                label.setText(replacement)
            elif text == "尚未创建商品任务\n批量准备后，每个链接会生成独立任务卡、owned tab 状态、实时进度和独立日志。":
                label.setText("添加商品链接并开始准备后，每个商品会显示独立进度和操作。")
            elif text == "每个链接独立任务 · 第 5 条起滚动":
                label.setText("每个链接单独处理")

        editor = getattr(workspace, "_batch_url_editor", None)
        if editor is not None:
            for row in getattr(editor, "rows", []):
                line = getattr(row, "input", None)
                if isinstance(line, QLineEdit):
                    line.setPlaceholderText("粘贴商品链接")

    def _apply_mode_switch(self) -> None:
        toggle = getattr(self.window, "_workspace_mode_switch", None)
        if toggle is None:
            return
        try:
            toggle.setAccessibleName("单品 / 批量")
            toggle.setToolTip("当前：批量任务 · 点击切换到单品" if toggle.isChecked() else "当前：单品任务 · 点击切换到批量")
        except RuntimeError:
            pass

    def _wrap_activity_presence(self) -> None:
        controller = getattr(self.window, "_activity_presence_controller", None)
        widget = getattr(controller, "widget", None)
        if widget is None or self._activity_wrapped:
            return
        original = getattr(widget, "set_activity", None)
        if not callable(original):
            return
        try:
            from . import activity_presence as activity_module

            for internal, display in _ACTIVITY_MODE.items():
                color = activity_module._MODE_COLORS.get(internal)  # noqa: SLF001
                if color is not None:
                    activity_module._MODE_COLORS[display] = color  # noqa: SLF001
        except Exception:
            pass

        def product_set_activity(
            mode: str,
            detail: str,
            percent: int,
            *,
            active: bool,
            meta: str = "",
        ) -> None:
            original(mode, _humanize(detail), percent, active=active, meta=_humanize(meta))
            display = _ACTIVITY_MODE.get(str(mode or "").upper(), str(mode or ""))
            try:
                widget.mode = display
                widget.update()
            except RuntimeError:
                pass

        widget.set_activity = product_set_activity
        # Productize the state that existed before the wrapper was installed.
        current_mode = str(getattr(widget, "mode", "STANDBY"))
        widget.mode = _ACTIVITY_MODE.get(current_mode.upper(), current_mode)
        widget.detail = _humanize(str(getattr(widget, "detail", "等待任务")))
        widget.meta = _humanize(str(getattr(widget, "meta", "总进度 · 0%")))
        self._activity_wrapped = True

    def refresh(self) -> None:
        self._apply_phase_badge()
        self._apply_single_dynamic()
        self._apply_console_dynamic()
        self._apply_batch_dynamic()
        self._apply_browser_status()
        self._apply_mode_switch()
        self._apply_runtime_assistant()

    def _apply_phase_badge(self) -> None:
        badge = getattr(self.window, "phase_badge", None)
        if not isinstance(badge, QLabel):
            return
        stack = getattr(self.window, "mode_stack", None)
        batch_mode = bool(stack is not None and int(stack.currentIndex()) == 1)
        if batch_mode:
            workspace = getattr(self.window, "batch_workspace", None)
            controller = getattr(workspace, "controller", None)
            batch = getattr(controller, "batch", None)
            if bool(getattr(workspace, "is_running", False)):
                text = "批量任务进行中"
            elif batch is not None:
                summary = batch.summary()
                text = (
                    f"批量 · {summary.get('ready', 0)} 可填写 · "
                    f"{summary.get('done', 0)} 已完成 · {summary.get('review', 0)} 待处理"
                )
            else:
                text = "批量 · 等待商品链接"
            _set_text(badge, text)
            return

        real = getattr(self.window, "execution_runner", None)
        runner = getattr(self.window, "runner", None)
        result = getattr(self.window, "current_result", None)
        if real is not None and bool(getattr(real, "is_running", False)):
            text = "正在填写商品"
        elif runner is not None and bool(getattr(runner, "is_running", False)):
            text = "正在准备商品"
        elif result is not None and bool(getattr(result, "plan_summary", None)):
            text = "商品已准备"
        else:
            text = "就绪"
        _set_text(badge, text)

    def _apply_single_dynamic(self) -> None:
        runner = getattr(self.window, "runner", None)
        real = getattr(self.window, "execution_runner", None)
        result = getattr(self.window, "current_result", None)
        fields_hint = getattr(self.window, "fields_hint", None)
        policy = getattr(self.window, "real_policy_hint", None)
        required = getattr(self.window, "_required_input_support", None)
        required_count = len(getattr(required, "inputs", {}) or {})

        if isinstance(fields_hint, QLabel):
            if runner is not None and bool(getattr(runner, "is_running", False)):
                fields_hint.setText("正在准备商品信息…")
            elif result is not None:
                mode = str(getattr(result, "workflow_mode", "") or "")
                if mode in {"step1", "step2"}:
                    parts = []
                    vertical = str(getattr(result, "vertical", "") or "").strip()
                    brand = str(getattr(result, "brand", "") or "").strip()
                    if vertical:
                        parts.append(f"类目：{vertical}")
                    if brand:
                        parts.append(f"品牌：{brand}")
                    fields_hint.setText(" · ".join(parts) or "识别完成")
                elif getattr(result, "plan_summary", None):
                    ready = int(getattr(result, "ready", 0) or 0)
                    blocked = int(getattr(result, "blocked", 0) or 0)
                    suffix = f" · {required_count} 个必填项待确认" if required_count else ""
                    fields_hint.setText(f"可填写 {ready} · 待处理 {blocked}{suffix}")
            else:
                fields_hint.setText("等待商品准备")

        if isinstance(policy, QLabel):
            if real is not None and bool(getattr(real, "is_running", False)):
                policy.setText("正在填写，请保持 Makro 页面打开。")
            elif runner is not None and bool(getattr(runner, "is_running", False)):
                policy.setText("正在准备商品，完成后即可开始填写。")
            elif result is not None and getattr(result, "plan_summary", None):
                ready = int(getattr(result, "ready", 0) or 0)
                if required_count:
                    policy.setText(
                        f"已准备 {ready} 个可填写字段，另有 {required_count} 个必填项可自动补默认值。"
                        "确认保存和图片选项后即可开始；送审需手动完成。"
                    )
                else:
                    policy.setText(
                        f"已准备 {ready} 个可填写字段。确认保存和图片选项后即可开始；送审需手动完成。"
                    )
            else:
                policy.setText("商品准备完成后即可填写。保存和图片由你确认，送审需手动完成。")

        image_count = getattr(self.window, "real_image_count", None)
        if isinstance(image_count, QLabel):
            text = image_count.text()
            match = re.fullmatch(r"AUTO\s+(\d+)/(\d+)", text)
            if match:
                image_count.setText(f"已找到 {match.group(1)}/{match.group(2)} 张")
            elif text == "AUTO · waiting":
                image_count.setText("等待商品图片")
            elif text == "AUTO 0":
                image_count.setText("暂无商品图片")
            elif text.endswith(" files"):
                image_count.setText(text[:-6] + " 张图片")

        web_hint = getattr(self.window, "web_hint", None)
        if isinstance(web_hint, QLabel):
            text = web_hint.text()
            match = re.fullmatch(r"(\d+) candidates", text)
            if match:
                web_hint.setText(f"{match.group(1)} 条参考资料")
            elif text == "same / different / uncertain":
                web_hint.setText("用于核对商品信息")

        for name in (
            "cold_label",
            "cold_web_label",
            "hot_label",
            "hot_web_label",
            "source_cache_label",
            "web_cache_label",
            "model_total_label",
            "pipeline_detail_label",
        ):
            label = getattr(self.window, name, None)
            if isinstance(label, QLabel):
                label.setText(_humanize(label.text()))

        write = getattr(self.window, "write_value", None)
        save = getattr(self.window, "save_value", None)
        qc = getattr(self.window, "qc_value", None)
        if isinstance(write, QLabel):
            raw = write.text()
            match = re.search(r"(\d+)$", raw)
            count = int(match.group(1)) if match else 0
            write.setText(f"已填写 · {count} 项" if count else "未填写")
        if isinstance(save, QLabel):
            raw = save.text()
            match = re.search(r"(\d+)$", raw)
            if raw.startswith("YES"):
                save.setText(f"已保存 · {match.group(1)} 次" if match else "已保存")
            elif raw.startswith("NO"):
                save.setText("未保存")
        if isinstance(qc, QLabel):
            qc.setText("已送审" if qc.text().startswith("YES") else "未送审")

        self._apply_real_controls_static()

    def _apply_console_dynamic(self) -> None:
        console = getattr(self.window, "console", None)
        if not isinstance(console, QWidget):
            return
        progress_detail = getattr(console, "progress_detail", None)
        if isinstance(progress_detail, QLabel):
            progress_detail.setText(_humanize(progress_detail.text()))

        total = getattr(console, "total_time_label", None)
        if isinstance(total, QLabel):
            total.setText(re.sub(r"^Total\s+", "用时 ", total.text()))
        count = getattr(console, "log_count_label", None)
        if isinstance(count, QLabel):
            count.setText(re.sub(r"^(\d+)\s+log lines?$", r"\1 条记录", count.text()))

        phase_units = getattr(console, "phase_units", {})
        if isinstance(phase_units, dict):
            for key, unit in phase_units.items():
                _set_text(getattr(unit, "title", None), _PHASE_TITLES.get(key, key))
                state = getattr(unit, "state", None)
                if isinstance(state, QLabel):
                    raw = state.text()
                    parts = raw.split(" · ", 1)
                    mapped = _PHASE_STATE.get(parts[0].upper())
                    if mapped:
                        state.setText(mapped + (f" · {parts[1]}" if len(parts) > 1 else ""))

        self._apply_console_static()

    def _apply_batch_dynamic(self) -> None:
        workspace = getattr(self.window, "batch_workspace", None)
        if not isinstance(workspace, QWidget):
            return
        jobs = list(getattr(workspace, "_jobs", []) or [])
        _set_text(getattr(workspace, "job_count_label", None), f"{len(jobs)} 个任务")

        state_label = getattr(workspace, "state_label", None)
        controller = getattr(workspace, "controller", None)
        batch = getattr(controller, "batch", None)
        if isinstance(state_label, QLabel):
            if bool(getattr(workspace, "is_running", False)):
                state_label.setText("任务进行中")
            elif batch is not None:
                state_label.setText("批次已创建")
            else:
                state_label.setText("等待添加商品链接")

        editor = getattr(workspace, "_batch_url_editor", None)
        if editor is not None:
            rows = list(getattr(editor, "rows", []) or [])
            nonempty = [row for row in rows if getattr(row, "url", lambda: "")()]
            enabled = [row for row in nonempty if getattr(row, "is_enabled", lambda: False)()]
            _set_text(getattr(editor, "summary", None), f"{len(nonempty)} 个链接 · {len(enabled)} 个启用")
            for row in rows:
                line = getattr(row, "input", None)
                if isinstance(line, QLineEdit):
                    line.setPlaceholderText("粘贴商品链接")

        root = workspace.layout()
        summary_layout = root.itemAt(1).layout() if root is not None and root.count() > 1 else None
        summary_copy = (
            ("商品总数", "本次批量任务"),
            ("处理中", "正在准备或填写"),
            ("可填写", "已经准备完成"),
            ("已完成", "已经保存并复核"),
            ("待处理", "需要补充或确认"),
            ("失败", "查看错误详情"),
        )
        if summary_layout is not None:
            for index, (caption_text, detail_text) in enumerate(summary_copy):
                if index >= summary_layout.count():
                    break
                card = summary_layout.itemAt(index).widget()
                layout = card.layout() if isinstance(card, QWidget) else None
                if layout is None:
                    continue
                if layout.count() > 1:
                    _set_text(layout.itemAt(1).widget(), caption_text)
                if layout.count() > 2:
                    _set_text(layout.itemAt(2).widget(), detail_text)

        cards = getattr(workspace, "_job_cards", {})
        for job_id, card in list(cards.items()) if isinstance(cards, dict) else []:
            job = getattr(card, "_job", None)
            if job is None:
                continue
            _set_text(getattr(card, "job_label", None), f"{job_id} · 商品任务")
            status = str(getattr(job, "status", "") or "")
            _set_text(getattr(card, "status_chip", None), _JOB_STATUS.get(status, status))

            phase = getattr(card, "phase_label", None)
            if isinstance(phase, QLabel):
                text = phase.text()
                for old, new in (
                    ("SOURCE", "采集"),
                    ("PRODUCT", "商品"),
                    ("VERTICAL", "类目"),
                    ("BRAND", "品牌"),
                    ("RESOLVE", "匹配"),
                    ("EXECUTE", "填写"),
                    ("VERIFY", "复核"),
                ):
                    text = text.replace(old, new)
                phase.setText(text)

            meta = getattr(card, "meta_label", None)
            if isinstance(meta, QLabel):
                meta.setText(
                    f"类目  {getattr(job, 'vertical', '') or '—'}    ·    "
                    f"品牌  {getattr(job, 'brand', '') or '—'}    ·    "
                    f"可填写  {int(getattr(job, 'ready', 0) or 0)}    ·    "
                    f"暂不可填  {int(getattr(job, 'blocked', 0) or 0)}    ·    "
                    f"必填待处理  {int(getattr(job, 'required_blocked', 0) or 0)}    ·    "
                    f"图片  {int(getattr(job, 'image_count', 0) or 0)}"
                )

            detail = getattr(card, "detail_label", None)
            if isinstance(detail, QLabel):
                detail.setText(_humanize(detail.text()))

            _set_text(getattr(card, "open_dir_button", None), "任务目录")
            _set_text(getattr(card, "modal_button", None), "查看详情")
            toggle = getattr(card, "toggle_button", None)
            if toggle is not None:
                _set_text(toggle, "收起详情" if bool(getattr(card, "_expanded", False)) else "查看详情")
            for label in card.findChildren(QLabel):
                if label.text() == "LIVE":
                    label.setText("实时")
                elif label.text() == "JOB CONTROL":
                    label.setText("任务操作")

        manager = getattr(workspace, "_batch_job_controls", None)
        controls = getattr(manager, "_controls", {})
        paused = getattr(manager, "_paused", {})
        pending = getattr(manager, "_pause_requested", {})
        if isinstance(controls, dict):
            by_id = {str(getattr(job, "job_id", "")): job for job in jobs}
            for job_id, control in controls.items():
                _set_text(getattr(control, "run_button", None), "填写此商品")
                hint = getattr(control, "hint", None)
                job = by_id.get(str(job_id))
                if not isinstance(hint, QLabel) or job is None:
                    continue
                if job_id in paused or str(getattr(job, "status", "")) == "PAUSED":
                    hint.setText("已暂停")
                elif job_id in pending:
                    hint.setText("将在当前步骤完成后暂停")
                elif str(getattr(job, "status", "")) == "READY":
                    hint.setText("已准备好")
                else:
                    hint.setText("当前商品")

        self._apply_batch_static()

    def _apply_browser_status(self) -> None:
        manager = getattr(self.window, "_managed_makro_browser", None)
        if manager is None:
            return
        state = str(getattr(manager, "_state", "CHECKING") or "CHECKING").upper()
        label = f"Makro 浏览器 · {_BROWSER_STATE.get(state, '正在检查')}"
        single = getattr(manager, "_single_label", None)
        batch = getattr(manager, "_batch_label", None)
        if isinstance(single, QLabel):
            single.setText(label)
            single.setToolTip("程序会自动连接 Makro 浏览器，并复用已有登录状态。")
        if isinstance(batch, QLabel):
            batch.setText(label + " · 多商品共用登录")
            batch.setToolTip("批量任务共用同一登录会话，每个商品使用独立标签页。")

    def _apply_runtime_assistant(self) -> None:
        assistant = self._assistant
        if assistant is None:
            return
        try:
            assistant.setWindowTitle("任务助手")
            assistant.setToolTip("拖动窗口可移动任务助手")
        except RuntimeError:
            return

        _set_text(getattr(assistant, "suggestion_title", None), "处理建议")
        event = getattr(assistant, "_last_event", None)
        if event is None:
            _set_text(getattr(assistant, "state_label", None), "● 待命")
            _set_text(getattr(assistant, "detail_label", None), "等待商品任务")
            return
        state = getattr(getattr(event, "state", None), "name", "")
        if not state:
            state = str(getattr(getattr(event, "state", None), "value", "") or "").upper()
        _set_text(getattr(assistant, "state_label", None), _RUNTIME_STATE.get(state, "● 进行中"))

        detail = _humanize(str(getattr(event, "title", "") or ""))
        phase = _humanize(str(getattr(event, "phase", "") or ""))
        if phase and phase not in detail:
            detail = f"{phase} · {detail}" if detail else phase
        _set_text(getattr(assistant, "detail_label", None), detail or "任务进行中")
        _set_text(getattr(assistant, "alert_label", None), _humanize(str(getattr(event, "detail", "") or "")))
        _set_text(
            getattr(assistant, "suggestion_label", None),
            _humanize(str(getattr(event, "suggestion", "") or "")) or "当前无需额外操作。",
        )
        confidence = float(getattr(event, "confidence", 0.0) or 0.0)
        if confidence > 0:
            text = f"建议可信度 {confidence * 100:.0f}%"
        else:
            text = "仅提示，不自动操作"
        _set_text(getattr(assistant, "confidence_label", None), text)


def install_product_copy(window: QMainWindow) -> ProductCopyController:
    existing = getattr(window, "_product_copy", None)
    if isinstance(existing, ProductCopyController):
        return existing
    controller = ProductCopyController(window)
    window._product_copy = controller  # type: ignore[attr-defined]
    return controller


__all__ = ["ProductCopyController", "install_product_copy"]
