from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QHBoxLayout, QMessageBox, QPushButton, QVBoxLayout, QWidget


_REMOVABLE_STATUSES = {"DONE", "REVIEW", "FAILED", "STOPPED"}
_BULK_CLEAN_STATUSES = {"DONE", "FAILED", "STOPPED"}


class BatchLifecycleManager(QObject):
    """Make one Batch workspace reusable across consecutive batches.

    The scheduler/executor remains authoritative. This layer only manages the
    terminal UI lifecycle once there are no active subprocesses: unlock source
    inputs, remove terminal job cards from the current Batch view, and reset the
    current Batch session without deleting its on-disk artifacts or touching the
    Makro browser/tabs.
    """

    def __init__(self, workspace: QWidget) -> None:
        super().__init__(workspace)
        self.workspace = workspace
        self.controller = workspace.controller
        self._remove_buttons: dict[str, QPushButton] = {}

        self._install_global_actions()
        self.controller.running_changed.connect(self._on_running_changed)
        self.controller.jobs_changed.connect(self._sync_jobs)
        self._sync_jobs(list(getattr(workspace, "_jobs", [])))
        self._restore_idle_editing()

    # ------------------------------------------------------------ Global actions
    def _install_global_actions(self) -> None:
        host = getattr(self.workspace, "execute_button", None)
        card = host.parentWidget() if isinstance(host, QPushButton) else None
        layout = card.layout() if card is not None else None
        if not isinstance(layout, QHBoxLayout):
            return

        self.clear_finished_button = QPushButton("清理已结束")
        self.clear_finished_button.setObjectName("quietButton")
        self.clear_finished_button.setToolTip("移除 DONE / FAILED / STOPPED 任务卡；运行目录仍保留。")
        self.clear_finished_button.clicked.connect(self.clear_finished_jobs)

        self.new_batch_button = QPushButton("新批次")
        self.new_batch_button.setObjectName("quietButton")
        self.new_batch_button.setToolTip("结束当前工作区会话并保留链接输入，直接开始下一批。历史运行目录不会删除。")
        self.new_batch_button.clicked.connect(self.start_new_batch)

        anchor = getattr(self.workspace, "open_batch_button", None)
        index = layout.indexOf(anchor) if anchor is not None else -1
        if index < 0:
            index = max(0, layout.count() - 3)
        layout.insertWidget(index, self.clear_finished_button)
        layout.insertWidget(index + 1, self.new_batch_button)

    # --------------------------------------------------------------- Idle unlock
    def _on_running_changed(self, running: bool) -> None:
        if not running:
            self._restore_idle_editing()
        self._refresh_actions()

    def _restore_idle_editing(self) -> None:
        if self.controller.is_running:
            return

        editor = getattr(self.workspace, "url_input", None)
        set_read_only = getattr(editor, "setReadOnly", None)
        if callable(set_read_only):
            set_read_only(False)

        for name in ("prepare_button", "clear_button", "makro_port", "source_port", "worker_count"):
            widget = getattr(self.workspace, name, None)
            if widget is not None:
                widget.setEnabled(True)

        stop = getattr(self.workspace, "stop_button", None)
        if stop is not None:
            stop.setEnabled(False)
        save = getattr(self.workspace, "save_check", None)
        images = getattr(self.workspace, "images_check", None)
        if save is not None:
            save.setEnabled(True)
        if images is not None:
            images.setEnabled(True)

        execute = getattr(self.workspace, "execute_button", None)
        if execute is not None:
            execute.setEnabled(any(job.status == "READY" for job in getattr(self.workspace, "_jobs", [])))

        self._refresh_actions()

    # ---------------------------------------------------------- Per-job removal
    def _sync_jobs(self, jobs: list[Any]) -> None:
        by_id = {str(job.job_id): job for job in jobs}

        for job_id, job in by_id.items():
            card = getattr(self.workspace, "_job_cards", {}).get(job_id)
            if card is None:
                continue
            button = self._remove_buttons.get(job_id)
            if button is None:
                button = self._add_remove_button(card, job_id)
                if button is not None:
                    self._remove_buttons[job_id] = button
            if button is not None:
                removable = str(job.status) in _REMOVABLE_STATUSES
                button.setVisible(removable)
                button.setEnabled(removable and not self.controller.is_running)

        for job_id in list(self._remove_buttons):
            if job_id in by_id:
                continue
            self._remove_buttons.pop(job_id, None)

        if not self.controller.is_running:
            self._restore_idle_editing()
        self._refresh_actions(jobs)

    def _add_remove_button(self, card: QWidget, job_id: str) -> QPushButton | None:
        root = card.layout()
        details = getattr(card, "details_box", None)
        if not isinstance(root, QVBoxLayout):
            return None

        button = QPushButton("移除任务", card)
        button.setObjectName("dangerButton")
        button.setToolTip("只从当前 Batch 工作区移除；Job 日志、报告和 Makro 页面都保留。")
        button.clicked.connect(lambda _checked=False, jid=job_id: self.remove_job(jid))
        index = root.indexOf(details) if details is not None else root.count()
        root.insertWidget(index if index >= 0 else root.count(), button, 0, Qt.AlignRight)
        return button

    def remove_job(self, job_id: str) -> None:
        if self.controller.is_running:
            QMessageBox.information(self.workspace, "任务仍在运行", "请等待当前 Batch 进入空闲状态后再移除任务。")
            return
        batch = self.controller.batch
        if batch is None:
            return
        job = next((item for item in batch.jobs if str(item.job_id) == str(job_id)), None)
        if job is None or str(job.status) not in _REMOVABLE_STATUSES:
            return

        answer = QMessageBox.question(
            self.workspace,
            f"移除 {job_id}",
            "只会从当前工作区移除此任务。\n\n"
            "Job 目录、日志、report.json 和已经存在的 Makro 页面都会保留。\n"
            "确认移除？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return
        self._remove_job_ids({str(job_id)})

    def clear_finished_jobs(self) -> None:
        if self.controller.is_running:
            return
        batch = self.controller.batch
        if batch is None:
            return
        targets = {str(job.job_id) for job in batch.jobs if str(job.status) in _BULK_CLEAN_STATUSES}
        if not targets:
            QMessageBox.information(self.workspace, "没有已结束任务", "当前没有 DONE / FAILED / STOPPED 任务需要清理。")
            return
        answer = QMessageBox.question(
            self.workspace,
            "清理已结束任务",
            f"将从当前工作区移除 {len(targets)} 个已结束任务。\n"
            "磁盘日志、报告和 Makro 页面不会删除。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer == QMessageBox.Yes:
            self._remove_job_ids(targets)

    def _remove_job_ids(self, job_ids: set[str]) -> None:
        batch = self.controller.batch
        if batch is None:
            return

        for queue_name in ("_source_queue", "_prepare_queue", "_execute_queue"):
            queue = getattr(self.controller, queue_name, None)
            if isinstance(queue, list):
                queue[:] = [item for item in queue if str(item) not in job_ids]

        batch.jobs[:] = [job for job in batch.jobs if str(job.job_id) not in job_ids]

        owned = getattr(self.controller, "_listing_offer_intent_by_job_id", None)
        if isinstance(owned, dict):
            for job_id in job_ids:
                owned.pop(job_id, None)

        pending_logs = getattr(self.workspace, "_pending_logs", None)
        if isinstance(pending_logs, dict):
            for job_id in job_ids:
                pending_logs.pop(job_id, None)

        support = getattr(self.workspace.window(), "_listing_offer_support", None)
        panels = getattr(support, "_batch_required_panels", None)
        if isinstance(panels, dict):
            for job_id in job_ids:
                panels.pop(job_id, None)

        self.controller._persist_emit()
        if not batch.jobs:
            self.controller.state_changed.emit("当前批次为空 · 可编辑链接并开始下一批")
        self._restore_idle_editing()

    # -------------------------------------------------------------- New session
    def start_new_batch(self) -> None:
        if self.controller.is_running:
            QMessageBox.information(self.workspace, "Batch 仍在运行", "请先等待任务完成或停止当前 Batch。")
            return

        batch = self.controller.batch
        if batch is not None and batch.jobs:
            answer = QMessageBox.question(
                self.workspace,
                "开始新批次",
                "当前任务卡将从工作区清空，但历史 Job 目录、日志、报告和 Makro 页面都会保留。\n\n"
                "顶部链接和 SKU 规格不会清空，你可以直接修改后重新开始。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer != QMessageBox.Yes:
                return

        # Emit an empty view while the old Batch object still exists so all
        # previously installed card/control layers synchronously release job UI.
        self.controller.jobs_changed.emit([])
        self.controller.summary_changed.emit(
            {"total": 0, "processing": 0, "ready": 0, "done": 0, "review": 0, "failed": 0}
        )

        self.controller.batch = None
        self.controller.config = None
        self.controller._mode = "idle"
        self.controller._stopping = False
        self.controller._execution_images = False
        self.controller._source_queue.clear()
        self.controller._prepare_queue.clear()
        self.controller._execute_queue.clear()
        self.controller._buffers.clear()

        for name in (
            "_listing_offer_intent_by_job_id",
            "_listing_offer_intent_by_url",
            "_listing_offer_pending_intents",
        ):
            value = getattr(self.controller, name, None)
            if isinstance(value, dict):
                value.clear()
            elif isinstance(value, list):
                value.clear()

        self.workspace._jobs = []
        self.workspace._batch_id = ""
        self._remove_buttons.clear()

        open_batch = getattr(self.workspace, "open_batch_button", None)
        execute = getattr(self.workspace, "execute_button", None)
        save = getattr(self.workspace, "save_check", None)
        images = getattr(self.workspace, "images_check", None)
        if open_batch is not None:
            open_batch.setEnabled(False)
        if execute is not None:
            execute.setEnabled(False)
        if save is not None:
            save.setChecked(False)
        if images is not None:
            images.setChecked(False)

        self.controller.state_changed.emit("新批次 · 修改链接 / SKU 规格后开始准备")
        self._restore_idle_editing()

    def _refresh_actions(self, jobs: list[Any] | None = None) -> None:
        if jobs is None:
            jobs = list(getattr(self.workspace, "_jobs", []))
        idle = not self.controller.is_running
        clear_button = getattr(self, "clear_finished_button", None)
        new_button = getattr(self, "new_batch_button", None)
        if clear_button is not None:
            clear_button.setEnabled(idle and any(str(job.status) in _BULK_CLEAN_STATUSES for job in jobs))
        if new_button is not None:
            new_button.setEnabled(idle)


def install_batch_lifecycle(workspace: QWidget) -> BatchLifecycleManager:
    existing = getattr(workspace, "_batch_lifecycle", None)
    if isinstance(existing, BatchLifecycleManager):
        return existing
    manager = BatchLifecycleManager(workspace)
    workspace._batch_lifecycle = manager
    return manager


__all__ = ["BatchLifecycleManager", "install_batch_lifecycle"]
