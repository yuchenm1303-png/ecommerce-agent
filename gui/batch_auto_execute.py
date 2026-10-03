from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QWidget

from .batch_model import normalize_batch_concurrency


class BatchAutoExecute(QObject):
    """Queue READY Batch jobs for Full Step 3 when the user enables auto fill."""

    def __init__(self, workspace: QWidget) -> None:
        super().__init__(workspace)
        self.workspace = workspace
        self.controller = workspace.controller
        self.individual = getattr(workspace, "_batch_individual_controls", None)
        if self.individual is None:
            raise RuntimeError("Batch auto execute requires BatchIndividualControls")

        self._drain_scheduled = False
        self.toggle = self._install_toggle()
        self.toggle.toggled.connect(self._on_toggled)
        self.controller.jobs_changed.connect(self._on_jobs_changed)

    def _install_toggle(self) -> QCheckBox:
        existing = getattr(self.workspace, "auto_execute_check", None)
        if isinstance(existing, QCheckBox):
            return existing

        execute_button = getattr(self.workspace, "execute_button", None)
        action_card = execute_button.parentWidget() if execute_button is not None else None
        layout = action_card.layout() if action_card is not None else None
        if not isinstance(layout, QHBoxLayout):
            raise RuntimeError("Batch action bar is unavailable")

        toggle = QCheckBox("自动正式填写", action_card)
        toggle.setObjectName("batchAutoExecuteCheck")
        toggle.setChecked(False)
        toggle.setToolTip(
            "开启后，每个商品字段准备完成并进入 READY 时，会自动执行正式填写；"
            "等同于手动点击该商品的“单独填写”。关闭后只停止后续自动触发，"
            "已经开始或已进入执行队列的商品不会被打断。"
        )

        anchor = getattr(self.workspace, "qc_check", None)
        index = layout.indexOf(anchor) if anchor is not None else -1
        layout.insertWidget(index + 1 if index >= 0 else max(0, layout.count() - 4), toggle)
        self.workspace.auto_execute_check = toggle
        return toggle

    def _on_toggled(self, enabled: bool) -> None:
        if enabled:
            self._schedule_drain()

    def _on_jobs_changed(self, _jobs: object = None) -> None:
        if self.toggle.isChecked():
            self._schedule_drain()

    def _schedule_drain(self) -> None:
        if self._drain_scheduled or not self.toggle.isChecked():
            return
        self._drain_scheduled = True
        QTimer.singleShot(0, self._drain_ready_jobs)

    def _drain_ready_jobs(self) -> None:
        try:
            if not self.toggle.isChecked():
                return
            if bool(getattr(self.controller, "_stopping", False)):
                return

            batch = self.controller.batch
            if batch is None or self.controller.config is None:
                return
            if str(getattr(batch, "status", "")) == "STOPPED":
                return

            for job in list(batch.jobs):
                if not self.toggle.isChecked() or bool(getattr(self.controller, "_stopping", False)):
                    break
                self._queue_ready_job(job)
        finally:
            self._drain_scheduled = False

    def _queue_ready_job(self, job: Any) -> None:
        job_id = str(getattr(job, "job_id", "") or "")
        if not job_id or str(getattr(job, "status", "")) != "READY":
            return
        if self.individual._job_is_scheduled(job_id):
            return

        row = self.individual._row_for_job(job_id)
        if row is not None:
            self.individual._bind_job_context(row, job)

        batch = self.controller.batch
        if batch is None or self.controller.config is None:
            return

        was_running = self.controller.is_running
        batch.save_authorized = True
        batch.images_authorized = True
        batch.send_to_qc = False
        batch.execute_concurrency = normalize_batch_concurrency(
            int(self.workspace.worker_count.value())
        )
        batch.status = "EXECUTING"
        self.controller._execution_images = True
        if self.controller._mode == "idle":
            self.controller._mode = "execute"
        if job_id not in self.controller._execute_queue:
            self.controller._execute_queue.append(job_id)

        if not was_running:
            self.controller._emit_running_changed(True)
        self.controller._emit_state_changed(
            f"{job_id} · 自动正式填写 · 准备完成后已自动进入 Full Step 3"
        )
        self.controller._persist_emit(immediate=True)
        self.individual._pump_lanes()


def install_batch_auto_execute(workspace: QWidget) -> BatchAutoExecute:
    existing = getattr(workspace, "_batch_auto_execute", None)
    if isinstance(existing, BatchAutoExecute):
        return existing
    controller = BatchAutoExecute(workspace)
    workspace._batch_auto_execute = controller
    return controller


__all__ = ["BatchAutoExecute", "install_batch_auto_execute"]
