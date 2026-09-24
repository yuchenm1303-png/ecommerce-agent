from __future__ import annotations

import re
from typing import Any

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
    "运行控制台 · 真实阶段 / 命令 / 产物 / 日志": "指示器",
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
    "LIVE": "实时",
}


def _set_text(widget: Any, text: str) -> None:
    if widget is None or not hasattr(widget, "text") or not hasattr(widget, "setText"):
        return
    try:
        if widget.text() != text:
            widget.setText(text)
    except RuntimeError:
        pass


def _humanize(text: str) -> str:
    value = str(text or "")
    for old, new in (
        ("STEP 3 CURRENT READ-ONLY FILL PLAN", "正在生成填写方案"),
        ("STEP 3 CURRENT RESOLVER · HOT/CACHE", "正在补充字段"),
        ("STEP 3 CURRENT RESOLVER · COLD", "正在识别字段"),
        ("Step 3 Resolver + Fill Plan", "填写方案"),
        ("Step 3 · Resolve / Fill Plan", "生成填写方案"),
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
        ("candidates", "参考资料"),
        ("candidate", "参考资料"),
        ("BATCH_SOURCE START", "正在采集商品"),
        ("BATCH_SOURCE", "商品采集"),
        ("Gate", "检查"),
    ):
        value = value.replace(old, new)
    value = re.sub(r"\bAI\b", "识别", value)
    value = re.sub(r"\bworkflow\b", "流程", value, flags=re.IGNORECASE)
    value = re.sub(r"\bworkers?\b", "并行任务", value, flags=re.IGNORECASE)
    value = re.sub(r"\bblocked\b", "暂不可填", value, flags=re.IGNORECASE)
    return value


class ProductCopyController(QObject):
    """Keep visible copy product-facing without changing business state."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self._assistant: QWidget | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(24)
        self._timer.timeout.connect(self.refresh)
        self._apply_static_copy()
        self._connect_updates()
        self.refresh()

    def _connect(self, signal: Any) -> None:
        if signal is None or not hasattr(signal, "connect"):
            return
        try:
            signal.connect(lambda *_args: self.schedule())
        except (RuntimeError, TypeError):
            pass

    def _connect_updates(self) -> None:
        runner = getattr(self.window, "runner", None)
        real = getattr(self.window, "execution_runner", None)
        batch = getattr(getattr(self.window, "batch_workspace", None), "controller", None)
        browser = getattr(self.window, "_managed_makro_browser", None)
        stack = getattr(self.window, "mode_stack", None)
        combo = getattr(self.window, "real_scope_combo", None)
        for owner, names in (
            (runner, ("running_changed", "progress_changed", "phase_event", "result_updated", "completed", "failed")),
            (real, ("running_changed", "progress_changed", "completed", "failed")),
            (batch, ("jobs_changed", "summary_changed", "running_changed", "state_changed")),
            (browser, ("status_changed",)),
            (stack, ("currentChanged",)),
            (combo, ("currentIndexChanged",)),
        ):
            if owner is None:
                continue
            for name in names:
                self._connect(getattr(owner, name, None))

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
        for widget in (*self.window.findChildren(QLabel), *self.window.findChildren(QAbstractButton)):
            replacement = _EXACT_COPY.get(widget.text())
            if replacement is not None:
                widget.setText(replacement)

        _set_text(self.window.findChild(QLabel, "brandMark"), "MAKRO 商品上架")
        _set_text(self.window.findChild(QLabel, "appTitle"), "商品上架助手")
        _set_text(self.window.findChild(QLabel, "subtle"), "从商品链接到 Makro 草稿，一站式准备与填写")

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
        vertical = getattr(self.window, "vertical_input", None)
        if isinstance(vertical, QLineEdit):
            vertical.setPlaceholderText("类目将在识别后显示")
            vertical.setToolTip("系统根据商品信息自动识别，无需手动填写。")

        self._apply_controls()
        self._apply_console()
        self._apply_batch_static()

    def _apply_controls(self) -> None:
        _set_text(getattr(self.window, "start_button", None), "启动单链接任务")
        start = getattr(self.window, "start_button", None)
        if start is not None:
            start.setToolTip("自动采集商品信息并生成可填写方案。")
        for name, text in (
            ("step1_button", "识别类目"),
            ("step2_button", "识别品牌"),
            ("step3_button", "生成填写方案"),
            ("real_save_check", "填写后保存并复核"),
            ("real_upload_check", "上传商品图片"),
            ("real_pick_images_button", "选择图片…"),
            ("real_qc_check", "送审需手动完成"),
            ("real_stop_button", "停止填写"),
            ("real_settings_toggle", "填写设置"),
        ):
            _set_text(getattr(self.window, name, None), text)

        combo = getattr(self.window, "real_scope_combo", None)
        if isinstance(combo, QComboBox):
            for index in range(combo.count()):
                data = combo.itemData(index)
                if data == FULL_STEP3:
                    combo.setItemText(index, "完整填写 · 全部可用字段")
                elif data == PRODUCT_PHOTOS:
                    combo.setItemText(index, "商品图片")
                else:
                    raw = combo.itemText(index)
                    section = raw.split("·", 1)[-1].strip()
                    combo.setItemText(index, f"单项填写 · {_SECTION_NAMES.get(section, section)}")
            _set_text(
                getattr(self.window, "real_start_button", None),
                "开始填写" if combo.currentData() == FULL_STEP3 else "填写当前项目",
            )

        table = getattr(self.window, "field_table", None)
        if isinstance(table, QTableWidget):
            if table.columnCount() >= 7:
                table.setHorizontalHeaderLabels(["字段", "识别状态", "建议值", "填写状态", "说明", "依据", "字段编号"])
            elif table.columnCount() >= 5:
                table.setHorizontalHeaderLabels(["字段", "建议值", "填写状态", "说明", "依据"])
        web = getattr(self.window, "web_table", None)
        if isinstance(web, QTableWidget) and web.columnCount() >= 3:
            web.setHorizontalHeaderLabels(["判定", "资料来源", "说明"])
        tabs = getattr(self.window, "side_detail_tabs", None)
        if isinstance(tabs, QTabWidget):
            for index, text in enumerate(("运行详情", "参考资料", "安全")):
                if index < tabs.count():
                    tabs.setTabText(index, text)

        for name, title, caption in (
            ("ready_card", "可填写", "已生成填写方案"),
            ("missing_card", "待补充", "缺少商品信息"),
            ("conflict_card", "需确认", "存在冲突信息"),
            ("blocked_card", "暂不可填", "需要补充或确认"),
        ):
            card = getattr(self.window, name, None)
            layout = card.layout() if isinstance(card, QWidget) else None
            if layout is not None and layout.count() >= 3:
                _set_text(layout.itemAt(1).widget(), title)
                _set_text(layout.itemAt(2).widget(), caption)

    def _apply_console(self) -> None:
        console = getattr(self.window, "console", None)
        if not isinstance(console, QWidget):
            return
        for label in console.findChildren(QLabel):
            replacement = _EXACT_COPY.get(label.text())
            if replacement is not None:
                label.setText(replacement)
        tabs = getattr(console, "tabs", None)
        if isinstance(tabs, QTabWidget):
            for index, text in enumerate(("运行记录", "步骤", "文件", "详情", "填写记录")):
                if index < tabs.count():
                    tabs.setTabText(index, text)
        phases = getattr(console, "phase_units", {})
        if isinstance(phases, dict):
            for key, unit in phases.items():
                _set_text(getattr(unit, "title", None), _PHASE_TITLES.get(key, key))
                state = getattr(unit, "state", None)
                if isinstance(state, QLabel):
                    parts = state.text().split(" · ", 1)
                    mapped = _PHASE_STATE.get(parts[0].upper())
                    if mapped:
                        state.setText(mapped + (f" · {parts[1]}" if len(parts) > 1 else ""))
        timeline = getattr(console, "timeline", None)
        if isinstance(timeline, QTableWidget) and timeline.columnCount() >= 6:
            timeline.setHorizontalHeaderLabels(["步骤", "状态", "耗时", "结果", "开始时间", "输出"])
        artifact = getattr(console, "artifact_table", None)
        if isinstance(artifact, QTableWidget) and artifact.columnCount() >= 3:
            artifact.setHorizontalHeaderLabels(["类型", "文件", "大小"])
        command = getattr(console, "command_view", None)
        if isinstance(command, QPlainTextEdit):
            command.setPlaceholderText("任务执行信息会显示在这里")
        detail = getattr(console, "progress_detail", None)
        if isinstance(detail, QLabel):
            detail.setText(_humanize(detail.text()))
        total = getattr(console, "total_time_label", None)
        if isinstance(total, QLabel):
            total.setText(re.sub(r"^Total\s+", "用时 ", total.text()))
        count = getattr(console, "log_count_label", None)
        if isinstance(count, QLabel):
            count.setText(re.sub(r"^(\d+)\s+log lines?$", r"\1 条记录", count.text()))

    def _apply_batch_static(self) -> None:
        workspace = getattr(self.window, "batch_workspace", None)
        if not isinstance(workspace, QWidget):
            return
        source_port = getattr(workspace, "source_port", None)
        if isinstance(source_port, QSpinBox):
            source_port.setPrefix("采集浏览器  ")
        workers = getattr(workspace, "worker_count", None)
        if isinstance(workers, QSpinBox):
            workers.setPrefix("并行任务  ")
        for name, text in (
            ("clear_button", "清空链接"),
            ("prepare_button", "开始批量准备"),
            ("save_check", "填写后保存并复核"),
            ("images_check", "上传商品图片"),
            ("qc_check", "送审需手动完成"),
            ("open_batch_button", "打开批次目录"),
            ("execute_button", "填写全部可用商品"),
        ):
            _set_text(getattr(workspace, name, None), text)
        for label in workspace.findChildren(QLabel):
            replacement = _EXACT_COPY.get(label.text())
            if replacement is not None:
                label.setText(replacement)
            elif "批量准备后，每个链接会生成独立任务卡" in label.text():
                label.setText("添加商品链接并开始准备后，每个商品会显示独立进度和操作。")
            elif label.text() == "每个链接独立任务 · 第 5 条起滚动":
                label.setText("每个链接单独处理")

    def _apply_batch_dynamic(self) -> None:
        workspace = getattr(self.window, "batch_workspace", None)
        if not isinstance(workspace, QWidget):
            return
        jobs = list(getattr(workspace, "_jobs", []) or [])
        _set_text(getattr(workspace, "job_count_label", None), f"{len(jobs)} 个任务")
        controller = getattr(workspace, "controller", None)
        batch = getattr(controller, "batch", None)
        state = getattr(workspace, "state_label", None)
        if isinstance(state, QLabel):
            state.setText("任务进行中" if workspace.is_running else ("批次已创建" if batch is not None else "等待添加商品链接"))

        editor = getattr(workspace, "_batch_url_editor", None)
        if editor is not None:
            rows = list(getattr(editor, "rows", []) or [])
            nonempty = [row for row in rows if row.url()]
            enabled = [row for row in nonempty if row.is_enabled()]
            _set_text(getattr(editor, "summary", None), f"{len(nonempty)} 个链接 · {len(enabled)} 个启用")
            for row in rows:
                if isinstance(getattr(row, "input", None), QLineEdit):
                    row.input.setPlaceholderText("粘贴商品链接")

        root = workspace.layout()
        summary = root.itemAt(1).layout() if root is not None and root.count() > 1 else None
        copy = (
            ("商品总数", "本次批量任务"),
            ("处理中", "正在准备或填写"),
            ("可填写", "已经准备完成"),
            ("已完成", "已经保存并复核"),
            ("待处理", "需要补充或确认"),
            ("失败", "查看错误详情"),
        )
        if summary is not None:
            for index, (caption, detail) in enumerate(copy):
                if index >= summary.count():
                    break
                card = summary.itemAt(index).widget()
                layout = card.layout() if isinstance(card, QWidget) else None
                if layout is not None and layout.count() > 1:
                    _set_text(layout.itemAt(1).widget(), caption)
                if layout is not None and layout.count() > 2:
                    _set_text(layout.itemAt(2).widget(), detail)

        cards = getattr(workspace, "_job_cards", {})
        for job_id, card in (list(cards.items()) if isinstance(cards, dict) else []):
            job = getattr(card, "_job", None)
            if job is None:
                continue
            _set_text(getattr(card, "job_label", None), f"{job_id} · 商品任务")
            _set_text(getattr(card, "status_chip", None), _JOB_STATUS.get(str(job.status), str(job.status)))
            meta = getattr(card, "meta_label", None)
            if isinstance(meta, QLabel):
                meta.setText(
                    f"类目  {job.vertical or '—'}    ·    品牌  {job.brand or '—'}    ·    "
                    f"可填写  {job.ready}    ·    暂不可填  {job.blocked}    ·    "
                    f"必填待处理  {job.required_blocked}    ·    图片  {job.image_count}"
                )
            detail = getattr(card, "detail_label", None)
            if isinstance(detail, QLabel):
                detail.setText(_humanize(detail.text()))
            phase = getattr(card, "phase_label", None)
            if isinstance(phase, QLabel):
                text = phase.text()
                for old, new in (("SOURCE", "采集"), ("PRODUCT", "商品"), ("VERTICAL", "类目"), ("BRAND", "品牌"), ("RESOLVE", "匹配"), ("EXECUTE", "填写"), ("VERIFY", "复核")):
                    text = text.replace(old, new)
                phase.setText(text)
            _set_text(getattr(card, "open_dir_button", None), "任务目录")
            _set_text(getattr(card, "modal_button", None), "查看详情")
            _set_text(getattr(card, "toggle_button", None), "收起详情" if getattr(card, "_expanded", False) else "查看详情")
            for label in card.findChildren(QLabel):
                if label.text() == "LIVE":
                    label.setText("实时")
                elif label.text() == "JOB CONTROL":
                    label.setText("任务操作")

        manager = getattr(workspace, "_batch_job_controls", None)
        controls = getattr(manager, "_controls", {})
        paused = getattr(manager, "_paused", {})
        pending = getattr(manager, "_pause_requested", {})
        by_id = {str(job.job_id): job for job in jobs}
        if isinstance(controls, dict):
            for job_id, control in controls.items():
                _set_text(getattr(control, "run_button", None), "填写此商品")
                hint = getattr(control, "hint", None)
                job = by_id.get(str(job_id))
                if not isinstance(hint, QLabel) or job is None:
                    continue
                if job_id in paused or str(job.status) == "PAUSED":
                    hint.setText("已暂停")
                elif job_id in pending:
                    hint.setText("将在当前步骤完成后暂停")
                elif str(job.status) == "READY":
                    hint.setText("已准备好")
                else:
                    hint.setText("当前商品")

    def _apply_safety(self) -> None:
        for name, yes_text, no_text in (
            ("write_value", "已填写", "未填写"),
            ("save_value", "已保存", "未保存"),
            ("qc_value", "已送审", "未送审"),
        ):
            label = getattr(self.window, name, None)
            if not isinstance(label, QLabel):
                continue
            raw = label.text()
            if raw.startswith((yes_text, no_text)):
                continue
            if name == "write_value":
                match = re.search(r"(\d+)$", raw)
                count = int(match.group(1)) if match else 0
                label.setText(f"已填写 · {count} 项" if count else "未填写")
            elif raw.startswith("YES"):
                match = re.search(r"(\d+)$", raw)
                label.setText(f"{yes_text} · {match.group(1)} 次" if match and name == "save_value" else yes_text)
            else:
                label.setText(no_text)

    def _apply_browser(self) -> None:
        manager = getattr(self.window, "_managed_makro_browser", None)
        if manager is None:
            return
        state = str(getattr(manager, "_state", "CHECKING") or "CHECKING").upper()
        text = f"Makro 浏览器 · {_BROWSER_STATE.get(state, '正在检查')}"
        single = getattr(manager, "_single_label", None)
        batch = getattr(manager, "_batch_label", None)
        if isinstance(single, QLabel):
            single.setText(text)
            single.setToolTip("程序会自动连接 Makro 浏览器，并复用已有登录状态。")
        if isinstance(batch, QLabel):
            batch.setText(text + " · 多商品共用登录")
            batch.setToolTip("批量任务共用同一登录会话，每个商品使用独立标签页。")

    def _apply_activity(self) -> None:
        widget = getattr(getattr(self.window, "_activity_presence_controller", None), "widget", None)
        if widget is None:
            return
        internal = str(getattr(widget, "mode", "STANDBY") or "STANDBY").upper()
        display = {
            "STANDBY": "待命", "PREPARING": "准备中", "READY": "可填写",
            "FILLING": "填写中", "COMPLETE": "已完成", "FAILED": "失败",
        }.get(internal, getattr(widget, "mode", internal))
        try:
            if display != widget.mode:
                from . import activity_presence as activity_module
                color = activity_module._MODE_COLORS.get(internal)  # noqa: SLF001
                if color is not None:
                    activity_module._MODE_COLORS[display] = color  # noqa: SLF001
                widget.mode = display
            widget.detail = _humanize(str(getattr(widget, "detail", "")))
            widget.meta = _humanize(str(getattr(widget, "meta", "")))
            widget.update()
        except RuntimeError:
            pass

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
                badge.setText("批量任务进行中")
            elif batch is not None:
                summary = batch.summary()
                badge.setText(f"批量 · {summary.get('ready', 0)} 可填写 · {summary.get('done', 0)} 已完成 · {summary.get('review', 0)} 待处理")
            else:
                badge.setText("批量 · 等待商品链接")
            return
        runner = getattr(self.window, "runner", None)
        real = getattr(self.window, "execution_runner", None)
        result = getattr(self.window, "current_result", None)
        if real is not None and real.is_running:
            badge.setText("正在填写商品")
        elif runner is not None and runner.is_running:
            badge.setText("正在准备商品")
        elif result is not None and result.plan_summary:
            badge.setText("商品已准备")
        else:
            badge.setText("就绪")

    def _apply_runtime_assistant(self) -> None:
        assistant = self._assistant
        if assistant is None:
            return
        assistant.setWindowTitle("任务助手")
        assistant.setToolTip("拖动窗口可移动任务助手")
        _set_text(getattr(assistant, "suggestion_title", None), "处理建议")
        event = getattr(assistant, "_last_event", None)
        if event is None:
            _set_text(getattr(assistant, "state_label", None), "● 待命")
            _set_text(getattr(assistant, "detail_label", None), "等待商品任务")
            return
        state = getattr(getattr(event, "state", None), "name", "") or str(getattr(getattr(event, "state", None), "value", "")).upper()
        _set_text(getattr(assistant, "state_label", None), _RUNTIME_STATE.get(state, "● 进行中"))
        title = _humanize(str(getattr(event, "title", "") or ""))
        phase = _humanize(str(getattr(event, "phase", "") or ""))
        _set_text(getattr(assistant, "detail_label", None), f"{phase} · {title}" if phase and phase not in title else (title or phase or "任务进行中"))
        _set_text(getattr(assistant, "alert_label", None), _humanize(str(getattr(event, "detail", "") or "")))
        _set_text(getattr(assistant, "suggestion_label", None), _humanize(str(getattr(event, "suggestion", "") or "")) or "当前无需额外操作。")
        confidence = float(getattr(event, "confidence", 0.0) or 0.0)
        _set_text(getattr(assistant, "confidence_label", None), f"建议可信度 {confidence * 100:.0f}%" if confidence > 0 else "仅提示，不自动操作")

    def refresh(self) -> None:
        self._apply_controls()
        self._apply_console()
        self._apply_batch_static()
        self._apply_batch_dynamic()
        self._apply_safety()
        self._apply_browser()
        self._apply_activity()
        self._apply_phase_badge()
        self._apply_runtime_assistant()


def install_product_copy(window: QMainWindow) -> ProductCopyController:
    existing = getattr(window, "_product_copy", None)
    if isinstance(existing, ProductCopyController):
        return existing
    controller = ProductCopyController(window)
    window._product_copy = controller  # type: ignore[attr-defined]
    return controller


__all__ = ["ProductCopyController", "install_product_copy"]
