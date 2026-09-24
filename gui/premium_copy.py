from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QLabel, QMainWindow, QTabWidget, QTableWidget, QWidget


_PHASE_TITLES = {
    "scan": "Source Capture",
    "cold": "Category Match",
    "hot": "Brand Match",
    "plan": "Listing Plan",
}

_PHASE_STATES = {
    "等待": "WAITING",
    "进行中": "RUNNING",
    "完成": "COMPLETED",
    "失败": "FAILED",
    "已取消": "CANCELLED",
    "已跳过": "SKIPPED",
    "WAITING": "WAITING",
    "RUNNING": "RUNNING",
    "COMPLETED": "COMPLETED",
    "FAILED": "FAILED",
    "CANCELLED": "CANCELLED",
    "SKIPPED": "SKIPPED",
}

_JOB_STATES = {
    "QUEUED": "QUEUED",
    "CAPTURING": "CAPTURING",
    "UNDERSTANDING": "ANALYZING",
    "SELECTING_VERTICAL": "CATEGORY",
    "SELECTING_BRAND": "BRAND",
    "RESOLVING": "RESOLVING",
    "READY": "READY",
    "FILLING": "FILLING",
    "UPLOADING_IMAGES": "UPLOADING",
    "SAVING": "SAVING",
    "VERIFYING": "VERIFYING",
    "DONE": "DONE",
    "REVIEW": "REVIEW",
    "FAILED": "FAILED",
    "STOPPED": "STOPPED",
    "PAUSED": "PAUSED",
}

_BROWSER_STATES = {
    "CHECKING": "CHECKING",
    "READY": "CONNECTED",
    "STARTING": "STARTING",
    "LOGIN": "LOGIN REQUIRED",
    "OFFLINE": "OFFLINE",
    "ERROR": "ERROR",
}

_RUNTIME_STATES = {
    "IDLE": "● IDLE",
    "RUNNING": "● RUNNING",
    "READY": "● READY",
    "AI_ANALYZING": "◉ ANALYZING",
    "RECOVERING": "◉ RECOVERING",
    "WAITING_FOR_USER": "⚠ ACTION REQUIRED",
    "RECOVERED": "✓ RECOVERED",
    "WARNING": "⚠ WARNING",
    "FAILED": "× SAFE STOP",
    "COMPLETE": "✓ COMPLETE",
}

_EYEBROWS = {
    "商品信息": "PRODUCT SOURCE",
    "PRODUCT SOURCE": "PRODUCT SOURCE",
    "字段检查": "FIELD REVIEW",
    "FIELD RESOLUTION": "FIELD REVIEW",
    "FIELD RESOLUTION · FULL TRACE": "FIELD REVIEW",
    "运行详情": "RUNTIME",
    "RUN DIAGNOSTICS": "RUNTIME",
    "RUN DIAGNOSTICS · MODEL / CACHE": "RUNTIME",
    "参考资料": "REFERENCE",
    "ENTITY MATCH": "REFERENCE",
    "操作权限": "SAFETY",
    "ZERO-WRITE CONTRACT": "SAFETY",
    "任务记录": "ACTIVITY",
    "LIVE CONSOLE": "ACTIVITY",
    "填写设置": "LISTING CONTROL",
    "REAL BROWSER ACCEPTANCE · EXPLICIT PERMISSIONS": "LISTING CONTROL",
    "批量商品": "BATCH QUEUE",
    "BATCH LISTING · MULTI PRODUCT QUEUE": "BATCH QUEUE",
    "商品任务": "LISTING TASK",
    "JOB CONTROL": "LISTING TASK",
    "JOB CONTROL · OWNED TAB ISOLATION · LIVE TELEMETRY": "LISTING TASK",
}


def _set_text(widget: Any, text: str) -> None:
    if widget is None or not hasattr(widget, "text") or not hasattr(widget, "setText"):
        return
    try:
        if widget.text() != text:
            widget.setText(text)
    except RuntimeError:
        pass


class PremiumCopyController(QObject):
    """Layer premium bilingual presentation over the product-facing copy.

    English is reserved for hierarchy, compact states and professional labels.
    Actions, guidance and recoverable errors remain concise Chinese. Internal
    architecture terms such as owned-tab ids or shadow-mode mechanics stay out
    of the primary UI.
    """

    _COALESCE_MS = 36

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self._assistant: QWidget | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self._COALESCE_MS)
        self._timer.timeout.connect(self.refresh)
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
        workspace = getattr(self.window, "batch_workspace", None)
        batch = getattr(workspace, "controller", None)
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
        self._apply_assistant()

    def _apply_header(self) -> None:
        self.window.setWindowTitle("ecommerce-agent · Listing Studio")
        _set_text(self.window.findChild(QLabel, "brandMark"), "MAKRO LISTING AUTOMATION")
        _set_text(self.window.findChild(QLabel, "appTitle"), "Listing Studio")
        _set_text(
            self.window.findChild(QLabel, "subtle"),
            "商品采集 · 类目与品牌识别 · 草稿填写与批量任务",
        )

    def _apply_eyebrows(self) -> None:
        for label in self.window.findChildren(QLabel):
            if label.objectName() == "sectionEyebrow":
                replacement = _EYEBROWS.get(label.text())
                if replacement:
                    label.setText(replacement)
            elif label.objectName() == "consoleEyebrow" and label.text() in {"任务进度", "ACCEPTANCE CONTROL CONSOLE"}:
                label.setText("WORKFLOW")

    def _apply_status_cards(self) -> None:
        copy = (
            ("ready_card", "READY", "可直接填写"),
            ("missing_card", "MISSING", "缺少商品信息"),
            ("conflict_card", "CONFLICT", "需要确认信息"),
            ("blocked_card", "BLOCKED", "暂不可填写"),
        )
        for name, title, caption in copy:
            card = getattr(self.window, name, None)
            layout = card.layout() if isinstance(card, QWidget) else None
            if layout is None or layout.count() < 3:
                continue
            _set_text(layout.itemAt(1).widget(), title)
            _set_text(layout.itemAt(2).widget(), caption)

    def _apply_side_tabs(self) -> None:
        tabs = getattr(self.window, "side_detail_tabs", None)
        if not isinstance(tabs, QTabWidget):
            return
        for index, text in enumerate(("Runtime", "Reference", "Safety")):
            if index < tabs.count():
                tabs.setTabText(index, text)

    def _apply_console(self) -> None:
        console = getattr(self.window, "console", None)
        if not isinstance(console, QWidget):
            return
        tabs = getattr(console, "tabs", None)
        if isinstance(tabs, QTabWidget):
            for index, text in enumerate(("Console", "Timeline", "Artifacts", "Diagnostics", "Fill Log")):
                if index < tabs.count():
                    tabs.setTabText(index, text)

        phases = getattr(console, "phase_units", {})
        if isinstance(phases, dict):
            for key, unit in phases.items():
                _set_text(getattr(unit, "title", None), _PHASE_TITLES.get(key, key))
                state = getattr(unit, "state", None)
                if isinstance(state, QLabel):
                    parts = state.text().split(" · ", 1)
                    mapped = _PHASE_STATES.get(parts[0].strip())
                    if mapped:
                        state.setText(mapped + (f" · {parts[1]}" if len(parts) > 1 else ""))

        timeline = getattr(console, "timeline", None)
        if isinstance(timeline, QTableWidget) and timeline.columnCount() >= 6:
            timeline.setHorizontalHeaderLabels(["Stage", "Status", "Duration", "Result", "Started", "Output"])
        artifacts = getattr(console, "artifact_table", None)
        if isinstance(artifacts, QTableWidget) and artifacts.columnCount() >= 3:
            artifacts.setHorizontalHeaderLabels(["Type", "Artifact", "Size"])

    def _apply_batch(self) -> None:
        workspace = getattr(self.window, "batch_workspace", None)
        if not isinstance(workspace, QWidget):
            return
        jobs = list(getattr(workspace, "_jobs", []) or [])

        root = workspace.layout()
        summary = root.itemAt(1).layout() if root is not None and root.count() > 1 else None
        summary_copy = (
            ("TOTAL", "本次商品"),
            ("ACTIVE", "准备或填写中"),
            ("READY", "已准备完成"),
            ("DONE", "已完成"),
            ("REVIEW", "需要处理"),
            ("FAILED", "执行失败"),
        )
        if summary is not None:
            for index, (title, caption) in enumerate(summary_copy):
                if index >= summary.count():
                    break
                card = summary.itemAt(index).widget()
                layout = card.layout() if isinstance(card, QWidget) else None
                if layout is None:
                    continue
                if layout.count() > 1:
                    _set_text(layout.itemAt(1).widget(), title)
                if layout.count() > 2:
                    _set_text(layout.itemAt(2).widget(), caption)

        cards = getattr(workspace, "_job_cards", {})
        if isinstance(cards, dict):
            for job_id, card in cards.items():
                job = getattr(card, "_job", None)
                if job is None:
                    continue
                _set_text(getattr(card, "job_label", None), f"{job_id} · LISTING TASK")
                status = str(getattr(job, "status", "") or "")
                _set_text(getattr(card, "status_chip", None), _JOB_STATES.get(status, status))

                meta = getattr(card, "meta_label", None)
                if isinstance(meta, QLabel):
                    meta.setText(
                        f"Category  {getattr(job, 'vertical', '') or '—'}    ·    "
                        f"Brand  {getattr(job, 'brand', '') or '—'}    ·    "
                        f"Ready  {int(getattr(job, 'ready', 0) or 0)}    ·    "
                        f"Blocked  {int(getattr(job, 'blocked', 0) or 0)}    ·    "
                        f"Required  {int(getattr(job, 'required_blocked', 0) or 0)}    ·    "
                        f"Images  {int(getattr(job, 'image_count', 0) or 0)}"
                    )

                phase = getattr(card, "phase_label", None)
                if isinstance(phase, QLabel):
                    text = phase.text()
                    for old, new in (
                        ("采集", "SOURCE"),
                        ("商品", "PRODUCT"),
                        ("类目", "CATEGORY"),
                        ("品牌", "BRAND"),
                        ("匹配", "RESOLVE"),
                        ("填写", "FILL"),
                        ("复核", "VERIFY"),
                    ):
                        text = text.replace(old, new)
                    phase.setText(text)

                for label in card.findChildren(QLabel):
                    if label.text() == "实时":
                        label.setText("LIVE")
                    elif label.text() == "任务操作":
                        label.setText("LISTING CONTROL")

        editor = getattr(workspace, "_batch_url_editor", None)
        summary_chip = getattr(editor, "summary", None)
        if summary_chip is not None:
            nonempty = [row for row in getattr(editor, "rows", []) if row.url()]
            enabled = [row for row in nonempty if row.is_enabled()]
            _set_text(summary_chip, f"{len(nonempty)} LINKS · {len(enabled)} ACTIVE")

    def _apply_browser(self) -> None:
        manager = getattr(self.window, "_managed_makro_browser", None)
        if manager is None:
            return
        state = str(getattr(manager, "_state", "CHECKING") or "CHECKING").upper()
        text = f"Makro Browser · {_BROWSER_STATES.get(state, 'CHECKING')}"
        _set_text(getattr(manager, "_single_label", None), text)
        _set_text(getattr(manager, "_batch_label", None), text + " · Multi-task Session")

    def _apply_activity(self) -> None:
        widget = getattr(getattr(self.window, "_activity_presence_controller", None), "widget", None)
        if widget is None:
            return
        current = str(getattr(widget, "mode", "") or "")
        display = {
            "待命": "STANDBY",
            "准备中": "PREPARING",
            "可填写": "READY",
            "填写中": "FILLING",
            "已完成": "COMPLETE",
            "失败": "FAILED",
            "STANDBY": "STANDBY",
            "PREPARING": "PREPARING",
            "READY": "READY",
            "FILLING": "FILLING",
            "COMPLETE": "COMPLETE",
            "FAILED": "FAILED",
        }.get(current, current)
        try:
            if display and widget.mode != display:
                widget.mode = display
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
                badge.setText("BATCH · RUNNING")
            elif batch is not None:
                summary = batch.summary()
                badge.setText(
                    f"BATCH · {summary.get('ready', 0)} READY · "
                    f"{summary.get('done', 0)} DONE · {summary.get('review', 0)} REVIEW"
                )
            else:
                badge.setText("BATCH · READY")
            return

        runner = getattr(self.window, "runner", None)
        real = getattr(self.window, "execution_runner", None)
        result = getattr(self.window, "current_result", None)
        if real is not None and bool(getattr(real, "is_running", False)):
            badge.setText("SINGLE · FILLING")
        elif runner is not None and bool(getattr(runner, "is_running", False)):
            badge.setText("SINGLE · PREPARING")
        elif result is not None and bool(getattr(result, "plan_summary", None)):
            badge.setText("SINGLE · READY")
        else:
            badge.setText("SINGLE · READY")

    def _apply_mode_switch(self) -> None:
        toggle = getattr(self.window, "_workspace_mode_switch", None)
        if toggle is None:
            return
        try:
            toggle.setAccessibleName("Single / Batch")
            toggle.setToolTip(
                "Batch Mode · 点击切换 Single" if toggle.isChecked()
                else "Single Mode · 点击切换 Batch"
            )
        except RuntimeError:
            pass

    def _apply_assistant(self) -> None:
        assistant = self._assistant
        if assistant is None:
            return
        try:
            assistant.setWindowTitle("Task Assistant")
            assistant.setToolTip("拖动窗口可移动 Task Assistant")
        except RuntimeError:
            return
        _set_text(getattr(assistant, "suggestion_title", None), "SUGGESTED ACTION")
        event = getattr(assistant, "_last_event", None)
        if event is None:
            _set_text(getattr(assistant, "state_label", None), "● IDLE")
            return
        state = getattr(getattr(event, "state", None), "name", "")
        if not state:
            state = str(getattr(getattr(event, "state", None), "value", "") or "").upper()
        _set_text(getattr(assistant, "state_label", None), _RUNTIME_STATES.get(state, "● RUNNING"))
        confidence = float(getattr(event, "confidence", 0.0) or 0.0)
        _set_text(
            getattr(assistant, "confidence_label", None),
            f"Confidence {confidence * 100:.0f}%" if confidence > 0 else "Advisory only",
        )

    def refresh(self) -> None:
        self._apply_header()
        self._apply_eyebrows()
        self._apply_status_cards()
        self._apply_side_tabs()
        self._apply_console()
        self._apply_batch()
        self._apply_browser()
        self._apply_activity()
        self._apply_phase_badge()
        self._apply_mode_switch()
        self._apply_assistant()


def install_premium_copy(window: QMainWindow) -> PremiumCopyController:
    existing = getattr(window, "_premium_copy", None)
    if isinstance(existing, PremiumCopyController):
        return existing
    controller = PremiumCopyController(window)
    window._premium_copy = controller  # type: ignore[attr-defined]
    return controller


__all__ = ["PremiumCopyController", "install_premium_copy"]
