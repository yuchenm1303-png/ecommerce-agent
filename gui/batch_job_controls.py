from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


_PAUSED_STATUS = "PAUSED"
_TERMINAL_STATUSES = {"DONE", "REVIEW", "FAILED", "STOPPED"}
_PREPARE_ACTIVE = {
    "CAPTURING",
    "UNDERSTANDING",
    "SELECTING_VERTICAL",
    "SELECTING_BRAND",
    "RESOLVING",
}
_EXECUTE_ACTIVE = {"FILLING", "UPLOADING_IMAGES", "SAVING", "VERIFYING"}


@dataclass(slots=True)
class _PauseState:
    resume_kind: str
    detail: str


@dataclass(slots=True)
class _JobControls:
    host: QFrame
    run_button: QPushButton
    pause_button: QPushButton
    hint: QLabel


class BatchJobControlManager(QObject):
    """Per-job execution and safe scheduler pause controls for Batch cards.

    Preparation and execution are independent lanes. A READY job may enter the
    canonical real executor immediately while other owned jobs continue Source /
    prepare work. The controller remains the only subprocess owner; this layer
    only schedules job ids into its existing queues and start methods.
    """

    def __init__(self, workspace: QWidget) -> None:
        super().__init__(workspace)
        self.workspace = workspace
        self.controller = workspace.controller
        self._controls: dict[str, _JobControls] = {}
        self._paused: dict[str, _PauseState] = {}
        self._pause_requested: dict[str, str] = {}
        self._syncing = False
        self._repump_scheduled = False

        self.controller.jobs_changed.connect(self._on_jobs_changed)
        self.controller.running_changed.connect(lambda _running: self._refresh_controls())
        self._on_jobs_changed(list(getattr(workspace, "_jobs", [])))

    def _on_jobs_changed(self, jobs: list[Any]) -> None:
        if self._syncing:
            return
        self._syncing = True
        changed = False
        try:
            by_id = {str(job.job_id): job for job in jobs}
            for job_id, job in by_id.items():
                card = getattr(self.workspace, "_job_cards", {}).get(job_id)
                if card is not None:
                    self._ensure_controls(card, job_id)
                if self._apply_pending_pause(job):
                    changed = True
            for job_id in list(self._controls):
                if job_id not in by_id:
                    self._controls.pop(job_id, None)
                    self._paused.pop(job_id, None)
                    self._pause_requested.pop(job_id, None)
            self._refresh_controls(jobs)
        finally:
            self._syncing = False
        if changed:
            self.controller._persist_emit()
        self._schedule_repump()

    def _schedule_repump(self) -> None:
        if self._repump_scheduled:
            return
        self._repump_scheduled = True
        QTimer.singleShot(0, self._run_scheduled_repump)

    def _run_scheduled_repump(self) -> None:
        self._repump_scheduled = False
        self._repump()

    def _ensure_controls(self, card: QWidget, job_id: str) -> None:
        if job_id in self._controls:
            return
        root = card.layout()
        details_box = getattr(card, "details_box", None)
        if not isinstance(root, QVBoxLayout) or details_box is None:
            return

        host = QFrame(card)
        host.setObjectName("batchJobControlStrip")
        host.setStyleSheet(
            "QFrame#batchJobControlStrip {"
            "background: rgba(5, 18, 34, 62);"
            "border: 1px solid rgba(255,255,255,24);"
            "border-radius: 9px;"
            "}"
        )
        row = QHBoxLayout(host)
        row.setContentsMargins(10, 7, 10, 7)
        row.setSpacing(8)

        label = QLabel("JOB CONTROL")
        label.setObjectName("sectionEyebrow")
        hint = QLabel("独立任务控制")
        hint.setObjectName("cardHint")
        hint.setWordWrap(True)
        pause_button = QPushButton("暂停")
        pause_button.setObjectName("quietButton")
        run_button = QPushButton("单独真实填写")
        run_button.setObjectName("primaryButton")

        pause_button.clicked.connect(lambda _checked=False, jid=job_id: self.toggle_pause(jid))
        run_button.clicked.connect(lambda _checked=False, jid=job_id: self.start_job_execution(jid))

        row.addWidget(label)
        row.addWidget(hint, 1)
        row.addWidget(pause_button)
        row.addWidget(run_button)

        index = root.indexOf(details_box)
        root.insertWidget(index if index >= 0 else root.count(), host)
        self._controls[job_id] = _JobControls(host, run_button, pause_button, hint)

    def start_job_execution(self, job_id: str) -> None:
        job = self._job(job_id)
        if job is None:
            return
        if job_id in self._paused or job_id in self._pause_requested or job.status == _PAUSED_STATUS:
            QMessageBox.information(self.workspace, "任务已暂停", "请先点击“继续”，再启动真实填写。")
            return
        if job.status != "READY":
            QMessageBox.information(
                self.workspace,
                "当前任务不可真实填写",
                f"{job_id} 当前状态为 {job.status}；只有 READY 任务可以单独执行 Full Step 3。",
            )
            return
        if self.controller.config is None:
            QMessageBox.warning(self.workspace, "缺少 Batch 配置", "请先完成该商品的准备阶段。")
            return
        if self._is_active(job_id) or job_id in self.controller._execute_queue:
            return

        answer = QMessageBox.question(
            self.workspace,
            f"确认单独真实填写 · {job_id}",
            "将只执行这一件商品的 Full Step 3。\n\n"
            "其他商品会继续准备或填写。\n"
            "Save + reopen: ON\n"
            "Product Photos: ON\n"
            "Send to QC: LOCKED / FALSE\n\n"
            "确认开始？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        batch = self.controller.batch
        if batch is None:
            return
        batch.save_authorized = True
        batch.images_authorized = True
        batch.send_to_qc = False
        self.controller._execution_images = True
        self.controller._stopping = False

        # Keep an active prepare owner-mode intact. The execution lane is pumped
        # independently below, so other products continue their own preparation.
        if self.controller._mode == "idle":
            self.controller._mode = "execute"
            batch.status = "EXECUTING"
            self.controller.running_changed.emit(True)

        self._append_unique(self.controller._execute_queue, job_id)
        self.controller.state_changed.emit(f"{job_id} · 单独真实填写 · 其他任务继续")
        self.controller._persist_emit()
        try:
            self._repump()
        except Exception as exc:
            self._remove_job(self.controller._execute_queue, job_id)
            job.status = "FAILED"
            job.stage_detail = "真实填写无法启动"
            job.error = str(exc)
            job.touch()
            self.controller._persist_emit()
            QMessageBox.critical(self.workspace, "真实填写无法启动", str(exc))

    def toggle_pause(self, job_id: str) -> None:
        job = self._job(job_id)
        if job is None:
            return
        if job_id in self._paused or job.status == _PAUSED_STATUS:
            self._resume(job)
            return
        if job_id in self._pause_requested:
            return
        if job.status in _TERMINAL_STATUSES:
            return

        active_stage = self._active_stage(job_id)
        if active_stage is not None:
            self._pause_requested[job_id] = active_stage
            job.stage_detail = "已请求安全暂停 · 当前阶段完成后暂停"
            job.touch()
            self.controller._persist_emit()
            return

        resume_kind = self._remove_from_queue(job_id)
        if resume_kind is None:
            if job.status == "READY":
                resume_kind = "ready"
            elif job.status in _PREPARE_ACTIVE or job.progress < 18:
                resume_kind = "source" if job.progress < 18 else "prepare"
            elif job.status in _EXECUTE_ACTIVE:
                resume_kind = "execute"
            else:
                resume_kind = "ready" if job.ready > 0 else "prepare"
        self._mark_paused(job, resume_kind, "已暂停 · 点击继续")
        self.controller._persist_emit()
        self._repump()

    def _apply_pending_pause(self, job: Any) -> bool:
        job_id = str(job.job_id)
        stage = self._pause_requested.get(job_id)
        if stage is None or self._is_active(job_id):
            return False
        self._pause_requested.pop(job_id, None)

        if stage == "source":
            if job.status == "FAILED":
                return False
            self._remove_job(self.controller._prepare_queue, job_id)
            self._mark_paused(job, "prepare", "Source 已完成 · 后续准备已暂停")
            return True
        if stage == "prepare":
            if job.status == "READY":
                self._mark_paused(job, "ready", "准备已完成 · 真实填写前暂停")
                return True
            return False
        if stage == "execute":
            return False
        return False

    def _resume(self, job: Any) -> None:
        state = self._paused.pop(str(job.job_id), None)
        if state is None:
            return

        if state.resume_kind == "source":
            job.status = "QUEUED"
            job.stage_detail = "已继续 · 等待 Source Capture"
            self._append_unique(self.controller._source_queue, str(job.job_id))
            self._ensure_prepare_mode()
        elif state.resume_kind == "prepare":
            job.status = "QUEUED"
            job.stage_detail = "已继续 · 等待商品准备"
            self._append_unique(self.controller._prepare_queue, str(job.job_id))
            self._ensure_prepare_mode()
        elif state.resume_kind == "execute":
            job.status = "READY"
            job.stage_detail = "已继续 · 等待真实填写"
            self._ensure_execute_mode()
            self._append_unique(self.controller._execute_queue, str(job.job_id))
        else:
            job.status = "READY"
            job.stage_detail = "已继续 · 准备完成"
        job.touch()
        self.controller._persist_emit()
        self._repump()

    def _ensure_prepare_mode(self) -> None:
        batch = self.controller.batch
        if batch is None:
            return
        if self.controller._mode == "idle":
            self.controller._mode = "prepare"
            batch.status = "PREPARING"
            self.controller.running_changed.emit(True)
            self.controller.state_changed.emit("Batch · 继续准备")

    def _ensure_execute_mode(self) -> None:
        batch = self.controller.batch
        if batch is None:
            return
        batch.save_authorized = True
        batch.images_authorized = True
        batch.send_to_qc = False
        self.controller._execution_images = True
        if self.controller._mode == "idle":
            self.controller._mode = "execute"
            batch.status = "EXECUTING"
            self.controller.running_changed.emit(True)
            self.controller.state_changed.emit("Batch · 继续真实填写")

    def _repump(self) -> None:
        self._pump_prepare_lane()
        self._pump_execute_lane()

        # Let the original owner-mode perform only lifecycle settlement. Queue
        # starts above are already bounded independently by their own limits.
        if self.controller._mode == "prepare":
            self.controller._pump_prepare()
        elif self.controller._mode == "execute":
            self.controller._pump_execute()

    def _pump_prepare_lane(self) -> None:
        batch = self.controller.batch
        if batch is None or self.controller.config is None:
            return
        source_active = any(stage == "source" for _, stage in self.controller._processes.values())
        if self.controller._source_queue and not source_active:
            self.controller._start_source(self.controller._source_queue.pop(0))

        active_prepare = sum(stage == "prepare" for _, stage in self.controller._processes.values())
        while self.controller._prepare_queue and active_prepare < batch.prepare_concurrency:
            self.controller._start_prepare_job(self.controller._prepare_queue.pop(0))
            active_prepare += 1

    def _pump_execute_lane(self) -> None:
        batch = self.controller.batch
        if batch is None or self.controller.config is None:
            return
        active_execute = sum(stage == "execute" for _, stage in self.controller._processes.values())
        while self.controller._execute_queue and active_execute < batch.execute_concurrency:
            self.controller._start_execute_job(self.controller._execute_queue.pop(0))
            active_execute += 1

    def _mark_paused(self, job: Any, resume_kind: str, detail: str) -> None:
        self._paused[str(job.job_id)] = _PauseState(resume_kind, detail)
        job.status = _PAUSED_STATUS
        job.stage_detail = detail
        job.touch()

    def _refresh_controls(self, jobs: list[Any] | None = None) -> None:
        if jobs is None:
            jobs = list(getattr(self.workspace, "_jobs", []))
        by_id = {str(job.job_id): job for job in jobs}
        for job_id, controls in self._controls.items():
            job = by_id.get(job_id)
            if job is None:
                continue
            paused = job_id in self._paused or job.status == _PAUSED_STATUS
            pending = job_id in self._pause_requested
            active = self._is_active(job_id)
            queued_execute = job_id in self.controller._execute_queue

            controls.pause_button.setText("继续" if paused else ("等待暂停" if pending else "暂停"))
            controls.pause_button.setEnabled(
                not pending and (paused or job.status not in _TERMINAL_STATUSES)
            )
            controls.run_button.setEnabled(
                job.status == "READY"
                and not paused
                and not pending
                and not active
                and not queued_execute
            )
            if paused:
                controls.hint.setText("已暂停 · 不参与批量调度")
                card = getattr(self.workspace, "_job_cards", {}).get(job_id)
                chip = getattr(card, "status_chip", None)
                if chip is not None:
                    chip.setText("已暂停")
                    chip.setStyleSheet(
                        "color:#ffe0a0; background:rgba(190,132,37,0.24);"
                        "border:1px solid rgba(255,255,255,30);"
                        "border-radius:9px; padding:4px 9px; font-weight:720;"
                    )
            elif pending:
                controls.hint.setText("安全暂停已请求 · 当前阶段完成后停住")
            elif job.status == "READY":
                controls.hint.setText("READY · 可单独填写 · 其他任务继续")
            elif active:
                controls.hint.setText("任务运行中")
            else:
                controls.hint.setText("独立任务控制")

    def _job(self, job_id: str) -> Any | None:
        batch = self.controller.batch
        if batch is None:
            return None
        return next((job for job in batch.jobs if job.job_id == job_id), None)

    def _active_stage(self, job_id: str) -> str | None:
        for _process, (owned_job_id, stage) in self.controller._processes.items():
            if owned_job_id == job_id:
                return stage
        return None

    def _is_active(self, job_id: str) -> bool:
        return self._active_stage(job_id) is not None

    def _remove_from_queue(self, job_id: str) -> str | None:
        if self._remove_job(self.controller._source_queue, job_id):
            return "source"
        if self._remove_job(self.controller._prepare_queue, job_id):
            return "prepare"
        if self._remove_job(self.controller._execute_queue, job_id):
            return "execute"
        return None

    @staticmethod
    def _remove_job(queue: list[str], job_id: str) -> bool:
        removed = False
        while job_id in queue:
            queue.remove(job_id)
            removed = True
        return removed

    @staticmethod
    def _append_unique(queue: list[str], job_id: str) -> None:
        if job_id not in queue:
            queue.append(job_id)


def install_batch_job_controls(workspace: QWidget) -> BatchJobControlManager:
    existing = getattr(workspace, "_batch_job_controls", None)
    if isinstance(existing, BatchJobControlManager):
        return existing
    manager = BatchJobControlManager(workspace)
    setattr(workspace, "_batch_job_controls", manager)
    return manager


__all__ = ["BatchJobControlManager", "_PauseState", "install_batch_job_controls"]
