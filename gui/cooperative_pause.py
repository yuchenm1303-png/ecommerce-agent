from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLayout, QMessageBox, QPushButton, QWidget

from app.task_control import (
    PAUSED,
    PAUSE_REQUESTED,
    RESUMING,
    RUNNING,
    initialize_task_control,
    request_pause,
    request_resume,
    task_control_state,
)
from .batch_job_controls import BatchJobControlManager, _PauseState


_TASK_STATE_RE = re.compile(r"GUI_TASK_STATE\s+(PAUSED|RUNNING)\s+checkpoint=(.*)$")


def _layout_containing(root: QLayout | None, widget: QWidget) -> QLayout | None:
    if root is None:
        return None
    for index in range(root.count()):
        item = root.itemAt(index)
        if item.widget() is widget:
            return root
        child = item.layout()
        found = _layout_containing(child, widget) if child is not None else None
        if found is not None:
            return found
    return None


class SingleCooperativePauseController(QObject):
    """One pause/resume surface shared by Single preparation and real execution."""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self.window = window
        self.runner = window.runner
        self.execution_runner = getattr(window, "execution_runner", None)
        self.state = RUNNING
        self.checkpoint = ""
        self._active_root: Path | None = None
        self._install_ui()
        self.runner.running_changed.connect(lambda _running: self._sync())
        self.runner.log.connect(self._observe_log)
        if self.execution_runner is not None:
            self.execution_runner.running_changed.connect(lambda _running: self._sync())
            self.execution_runner.log.connect(self._observe_log)
        self._sync()

    def _install_ui(self) -> None:
        root = self.window.centralWidget().layout() if self.window.centralWidget() is not None else None
        row = _layout_containing(root, self.window.start_button)
        if not isinstance(row, QHBoxLayout):
            raise RuntimeError("Single source action row is unavailable for pause controls")
        self.pause_button = QPushButton("暂停")
        self.pause_button.setObjectName("quietButton")
        self.pause_button.setToolTip(
            "安全点暂停：不会冻结浏览器或中断 Save/reopen。当前原子步骤完成后进入已暂停，"
            "你可以在 Makro 页面修正状态，再点击继续。"
        )
        self.pause_button.clicked.connect(self.toggle)
        stop_index = row.indexOf(self.window.stop_button)
        insert_at = stop_index if stop_index >= 0 else row.count()
        row.insertWidget(insert_at, self.pause_button)

        self.pause_hint = QLabel("安全点暂停")
        self.pause_hint.setObjectName("cardHint")
        self.pause_hint.setToolTip("恢复时后端重新读取真实页面状态，不直接相信暂停前进度。")
        row.insertWidget(insert_at + 1, self.pause_hint)

    def _active(self) -> tuple[Any | None, Path | None, str]:
        execution = self.execution_runner
        if execution is not None and execution.is_running and execution.output_root is not None:
            return execution, execution.output_root.resolve(), "real_execution"
        if self.runner.is_running and self.runner.run_dir is not None:
            return self.runner, self.runner.run_dir.resolve(), "prepare"
        return None, None, ""

    def toggle(self) -> None:
        _runner, root, workflow = self._active()
        if root is None:
            return
        payload = task_control_state(root)
        state = str(payload.get("state") or self.state or RUNNING).upper()
        if state == PAUSED:
            request_resume(root)
            self.state = RESUMING
            self.pause_button.setText("恢复中…")
            self.pause_button.setEnabled(False)
            self.pause_hint.setText("恢复中 · 正在重新核对当前页面")
            self._set_badge("SINGLE · 恢复中")
            return
        if state in {PAUSE_REQUESTED, RESUMING}:
            return
        initialize_task_control(root, workflow=workflow)
        request_pause(root, reason="single_gui_user")
        self.state = PAUSE_REQUESTED
        self.pause_button.setText("正在暂停…")
        self.pause_button.setEnabled(False)
        self.pause_hint.setText("正在暂停 · 等待当前安全步骤完成")
        self._set_badge("SINGLE · 正在暂停…")

    def _observe_log(self, line: str) -> None:
        match = _TASK_STATE_RE.search(str(line or "").strip())
        if match is None:
            return
        state, checkpoint = match.groups()
        self.state = state
        self.checkpoint = checkpoint.strip()
        if state == PAUSED:
            self.pause_button.setText("继续")
            self.pause_button.setEnabled(True)
            self.pause_hint.setText(f"已暂停 · {self.checkpoint}")
            self._set_badge("SINGLE · 已暂停")
        else:
            self.pause_button.setText("暂停")
            self.pause_button.setEnabled(True)
            self.pause_hint.setText("运行中 · 已按真实页面状态继续")
            self._set_badge("SINGLE · running")

    def _set_badge(self, text: str) -> None:
        badge = getattr(self.window, "phase_badge", None)
        if badge is not None:
            badge.setText(text)

    def _sync(self) -> None:
        _runner, root, _workflow = self._active()
        if root is None:
            self._active_root = None
            self.state = RUNNING
            self.checkpoint = ""
            self.pause_button.setText("暂停")
            self.pause_button.setEnabled(False)
            self.pause_hint.setText("安全点暂停")
            return
        if self._active_root != root:
            self._active_root = root
            initialize_task_control(root, reset=True)
        else:
            initialize_task_control(root)
        payload = task_control_state(root)
        state = str(payload.get("state") or RUNNING).upper()
        self.state = state
        self.checkpoint = str(payload.get("checkpoint") or "")
        if state == PAUSED:
            self.pause_button.setText("继续")
            self.pause_button.setEnabled(True)
            self.pause_hint.setText(f"已暂停 · {self.checkpoint or 'safe checkpoint'}")
        elif state == PAUSE_REQUESTED:
            self.pause_button.setText("正在暂停…")
            self.pause_button.setEnabled(False)
            self.pause_hint.setText("正在暂停 · 等待当前安全步骤完成")
        elif state == RESUMING:
            self.pause_button.setText("恢复中…")
            self.pause_button.setEnabled(False)
            self.pause_hint.setText("恢复中 · 正在重新核对当前页面")
        else:
            self.pause_button.setText("暂停")
            self.pause_button.setEnabled(True)
            self.pause_hint.setText("运行中 · 可请求安全暂停")


class CooperativeBatchJobControlManager(BatchJobControlManager):
    """Upgrade Batch card pause to the same worker-level cooperative protocol."""

    def __init__(self, workspace: QWidget) -> None:
        super().__init__(workspace)
        self.controller.log.connect(self._observe_controller_log)

    def _on_jobs_changed(self, jobs: list[Any]) -> None:
        super()._on_jobs_changed(jobs)
        if self.controller._mode == "prepare":
            QTimer.singleShot(0, lambda: self._backfill("prepare"))
        elif self.controller._mode == "execute":
            QTimer.singleShot(0, lambda: self._backfill("execute"))

    def _control_root(self, job: Any, stage: str) -> Path:
        if stage == "execute":
            return self.controller._job_root(job) / "real-execution"
        return Path(job.run_dir).resolve()

    def toggle_pause(self, job_id: str) -> None:
        job = self._job(job_id)
        if job is None:
            return
        active_stage = self._active_stage(job_id)
        if active_stage in {"prepare", "execute"}:
            if job_id in self._paused or job.status == "PAUSED":
                self._resume_active(job, active_stage)
                return
            if job_id in self._pause_requested:
                return
            root = self._control_root(job, active_stage)
            initialize_task_control(
                root,
                task_id=job_id,
                workflow=f"batch_{active_stage}",
                product_url=job.product_url,
            )
            request_pause(root, reason="batch_gui_user", resume_kind=active_stage)
            self._pause_requested[job_id] = active_stage
            job.stage_detail = "正在暂停 · 当前原子步骤完成后停住"
            job.touch()
            self.controller._persist_emit()
            self._refresh_controls()
            return
        super().toggle_pause(job_id)

    def _observe_controller_log(self, text: str) -> None:
        raw = str(text or "")
        if "] " not in raw:
            return
        prefix, line = raw.split("] ", 1)
        job_id = prefix.lstrip("[").strip()
        match = _TASK_STATE_RE.search(line.strip())
        if match is None:
            return
        state, checkpoint = match.groups()
        job = self._job(job_id)
        if job is None:
            return
        stage = self._active_stage(job_id) or self._pause_requested.get(job_id) or "prepare"
        if state == PAUSED:
            self._pause_requested.pop(job_id, None)
            self._paused[job_id] = _PauseState(stage, checkpoint.strip())
            job.status = "PAUSED"
            job.stage_detail = f"已暂停 · {checkpoint.strip()} · 可人工修正后继续"
            job.touch()
            self.controller._persist_emit()
            self._refresh_controls()
            self._backfill(stage)
        elif state == RUNNING:
            self._paused.pop(job_id, None)
            self._pause_requested.pop(job_id, None)
            if stage == "execute":
                job.status = "FILLING"
                job.stage_detail = "已继续 · 重新核对页面后继续真实填写"
            else:
                job.status = "RESOLVING"
                job.stage_detail = "已继续 · 按当前页面状态继续准备"
            job.touch()
            self.controller._persist_emit()
            self._refresh_controls()

    def _resume_active(self, job: Any, stage: str) -> None:
        root = self._control_root(job, stage)
        payload = task_control_state(root)
        if str(payload.get("state") or "").upper() != PAUSED:
            super()._resume(job)
            return
        if self._active_nonpaused(stage) >= self._limit(stage):
            QMessageBox.information(
                self.workspace,
                "等待运行槽",
                "其他任务正在占用当前并发槽。该任务保持暂停；稍后再点击“继续”即可。",
            )
            return
        request_resume(root)
        job.stage_detail = "恢复中 · 正在重新核对当前页面"
        job.touch()
        self.controller._persist_emit()
        controls = self._controls.get(str(job.job_id))
        if controls is not None:
            controls.pause_button.setText("恢复中…")
            controls.pause_button.setEnabled(False)
            controls.hint.setText("恢复中 · live-state reconcile")

    def _active_nonpaused(self, stage: str) -> int:
        count = 0
        for _process, (job_id, owned_stage) in self.controller._processes.items():
            if owned_stage != stage:
                continue
            job = self._job(job_id)
            if job is not None and job.status != "PAUSED":
                count += 1
        return count

    def _limit(self, stage: str) -> int:
        batch = self.controller.batch
        if batch is None:
            return 1
        return batch.execute_concurrency if stage == "execute" else batch.prepare_concurrency

    def _backfill(self, stage: str) -> None:
        if stage == "prepare":
            while self.controller._prepare_queue and self._active_nonpaused("prepare") < self._limit("prepare"):
                self.controller._start_prepare_job(self.controller._prepare_queue.pop(0))
        elif stage == "execute":
            while self.controller._execute_queue and self._active_nonpaused("execute") < self._limit("execute"):
                self.controller._start_execute_job(self.controller._execute_queue.pop(0))

    def _resume(self, job: Any) -> None:
        stage = self._active_stage(str(job.job_id))
        if stage in {"prepare", "execute"}:
            self._resume_active(job, stage)
            return
        state = self._paused.get(str(job.job_id))
        if state is None and job.status == "PAUSED":
            root = Path(job.run_dir).resolve()
            payload = task_control_state(root)
            resume_kind = str(payload.get("resume_kind") or "")
            if resume_kind:
                self._paused[str(job.job_id)] = _PauseState(
                    resume_kind,
                    str(payload.get("checkpoint") or ""),
                )
        super()._resume(job)


def install_cooperative_pause(window: QWidget) -> SingleCooperativePauseController:
    existing = getattr(window, "_cooperative_pause", None)
    if isinstance(existing, SingleCooperativePauseController):
        return existing
    controller = SingleCooperativePauseController(window)
    setattr(window, "_cooperative_pause", controller)
    return controller


def install_cooperative_batch_job_controls(workspace: QWidget) -> CooperativeBatchJobControlManager:
    existing = getattr(workspace, "_batch_job_controls", None)
    if isinstance(existing, CooperativeBatchJobControlManager):
        return existing
    if existing is not None:
        existing.deleteLater()
    manager = CooperativeBatchJobControlManager(workspace)
    setattr(workspace, "_batch_job_controls", manager)
    return manager


__all__ = [
    "CooperativeBatchJobControlManager",
    "SingleCooperativePauseController",
    "install_cooperative_batch_job_controls",
    "install_cooperative_pause",
]
