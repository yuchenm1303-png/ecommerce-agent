from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from types import MethodType
from typing import Any
from urllib.parse import urlparse

from PySide6.QtCore import QObject, QProcess, QTimer, Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .batch_model import BatchJob, normalize_batch_concurrency
from .listing_offer_support import _clean_intent, _write_intent_sidecar
from .readonly_runner import RunnerConfig


_TERMINAL = {"DONE", "REVIEW", "FAILED", "STOPPED"}
_JOB_ID_RE = re.compile(r"^JOB-(\d+)$")


@dataclass(slots=True)
class _RowControls:
    start: QPushButton
    stop: QPushButton
    delete: QPushButton


@dataclass(slots=True)
class _CardControls:
    host: QFrame
    fill: QPushButton
    stop: QPushButton
    delete: QPushButton
    hint: QLabel


class BatchIndividualControls(QObject):
    """Per-link start/stop/delete on top of the canonical BatchController.

    One input row owns at most one current Job. Starting a row schedules only that
    product; stopping terminates/removes only that Job's queues/process; deleting
    removes only the current workspace record. Disk artifacts and Makro tabs are
    deliberately preserved. The controller remains the sole subprocess owner.
    """

    def __init__(self, workspace: QWidget) -> None:
        super().__init__(workspace)
        self.workspace = workspace
        self.window = workspace.window()
        self.controller = workspace.controller
        self.editor = getattr(workspace, "_batch_url_editor", None)
        if self.editor is None:
            raise RuntimeError("Batch independent controls require BatchUrlEditor")

        self._rows: dict[int, _RowControls] = {}
        self._cards: dict[str, _CardControls] = {}
        self._stop_requested: set[str] = set()
        self._delete_requested: set[str] = set()
        self._batch_id = ""

        self._install_job_owned_product_files()
        self._install_row_hooks()
        self._install_controller_hooks()
        for row in list(self.editor.rows):
            self._decorate_row(row)

        self.controller.jobs_changed.connect(self._sync_jobs)
        self.controller.running_changed.connect(lambda _running: self._refresh_rows())
        self._sync_jobs(list(getattr(workspace, "_jobs", [])))

    # ------------------------------------------------------------ row controls
    def _install_row_hooks(self) -> None:
        original_add_row = self.editor.add_row

        def add_row(_editor: Any, *args: Any, **kwargs: Any):
            row = original_add_row(*args, **kwargs)
            self._decorate_row(row)
            return row

        self.editor.add_row = MethodType(add_row, self.editor)

        original_set_locked = self.editor.set_locked

        def set_locked(_editor: Any, locked: bool) -> None:
            original_set_locked(bool(locked))
            # Editing remains locked while Batch owns browser/process state, but
            # independent Start/Stop/Delete remain available where safe.
            self._refresh_rows()

        self.editor.set_locked = MethodType(set_locked, self.editor)

    def _decorate_row(self, row: Any) -> None:
        if id(row) in self._rows:
            return
        layout = row.layout()
        remove = getattr(row, "remove_button", None)
        if not isinstance(layout, QHBoxLayout) or not isinstance(remove, QPushButton):
            return

        start = QPushButton("启动", row)
        start.setObjectName("batchRowStartButton")
        start.setFixedSize(48, 28)
        start.setToolTip("只启动这一条：Source Capture → Step 1/2 → Resolver → Fill Plan。")
        stop = QPushButton("停止", row)
        stop.setObjectName("batchRowStopButton")
        stop.setFixedSize(48, 28)
        stop.setToolTip("只停止这一条商品，其他链接继续运行。")

        try:
            remove.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        remove.setText("删除")
        remove.setToolTip("删除这一条任务/输入；磁盘日志、报告和 Makro 页面保留。")

        index = layout.indexOf(remove)
        insert_at = index if index >= 0 else layout.count()
        layout.insertWidget(insert_at, start, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.insertWidget(insert_at + 1, stop, 0, Qt.AlignmentFlag.AlignVCenter)

        start.clicked.connect(lambda _checked=False, current=row: self.start_row(current))
        stop.clicked.connect(lambda _checked=False, current=row: self.stop_row(current))
        remove.clicked.connect(lambda _checked=False, current=row: self.delete_row(current))

        row.setStyleSheet(
            (row.styleSheet() or "")
            + "QPushButton#batchRowStartButton, QPushButton#batchRowStopButton {"
            "min-height:28px;max-height:28px;border-radius:8px;padding:0 7px;"
            "font-size:11px;font-weight:730;border:1px solid rgba(255,255,255,28);"
            "background:rgba(12,34,54,108);color:rgba(236,247,255,220);}"
            "QPushButton#batchRowStartButton:hover {"
            "border-color:rgba(143,231,194,105);background:rgba(44,126,94,90);}"
            "QPushButton#batchRowStopButton:hover {"
            "border-color:rgba(255,188,139,100);background:rgba(139,86,42,84);}"
            "QPushButton#batchRowStartButton:disabled,QPushButton#batchRowStopButton:disabled {"
            "color:rgba(235,245,255,62);background:rgba(24,37,49,40);"
            "border-color:rgba(255,255,255,12);}"
        )
        self._rows[id(row)] = _RowControls(start, stop, remove)
        self._refresh_row(row)

    # --------------------------------------------------------- controller hooks
    def _install_controller_hooks(self) -> None:
        self._original_start_prepare = self.controller.start_prepare

        def start_prepare(_controller: Any, urls: list[str], config: Any, **kwargs: Any):
            matched_rows = self._match_rows(urls)
            batch = self._original_start_prepare(urls, config, **kwargs)
            self._batch_id = str(batch.batch_id)
            for row, job in zip(matched_rows, batch.jobs):
                self._bind_row_job(row, job)
            return batch

        self.controller.start_prepare = MethodType(start_prepare, self.controller)

        self._original_finished = self.controller._finished

        def finished(_controller: Any, process: Any, exit_code: int) -> None:
            job_id, stage = _controller._processes.get(process, ("", ""))
            self._original_finished(process, exit_code)

            if job_id in self._stop_requested:
                self._stop_requested.discard(job_id)
                self._remove_from_queues(job_id)
                job = self._job(job_id)
                if job is not None:
                    job.status = "STOPPED"
                    job.stage_detail = "已停止 · 仅当前商品"
                    job.error = ""
                    job.touch()

            if job_id in self._delete_requested:
                self._delete_requested.discard(job_id)
                self._remove_job_record(job_id)
            elif job_id:
                self.controller._persist_emit()

            self._pump_lanes()
            self._settle_if_idle(stage)

        self.controller._finished = MethodType(finished, self.controller)

    # -------------------------------------------------------------- start row
    def start_row(self, row: Any) -> None:
        if row not in self.editor.rows or not bool(row.is_enabled()):
            return
        url = str(row.url() or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            QMessageBox.warning(self.workspace, "链接无效", "请先填写完整的 http(s) 商品链接。")
            return
        if bool(getattr(self.controller, "_stopping", False)) and self.controller.is_running:
            QMessageBox.information(self.workspace, "Batch 正在停止", "请等待全局停止完成后再启动新商品。")
            return

        current = self._row_job(row)
        if current is not None and self._job_busy_or_ready(current):
            QMessageBox.information(
                self.workspace,
                "这一条已经启动",
                f"{current.job_id} 当前状态为 {current.status}。",
            )
            return

        config = self._runtime_config(url)
        try:
            if self.controller.batch is None:
                # The first independent row still passes through the full installed
                # start_prepare chain (AI runtime, offer intent, product files,
                # photo ownership, browser ownership). Only one URL is supplied.
                batch = self._original_start_prepare(
                    [url],
                    config,
                    prepare_concurrency=int(self.workspace.worker_count.value()),
                )
                job = batch.jobs[0]
                self._batch_id = str(batch.batch_id)
            else:
                job = self._append_job(url, config)

            self._bind_row_job(row, job)
            self.workspace.open_batch_button.setEnabled(True)
            self.controller.state_changed.emit(f"{job.job_id} · 单独启动 · 其他商品不受影响")
            self.controller._persist_emit(immediate=True)
            self._pump_lanes()
        except Exception as exc:
            QMessageBox.critical(self.workspace, "单独启动失败", str(exc))

    def _append_job(self, url: str, config: RunnerConfig) -> BatchJob:
        batch = self.controller.batch
        if batch is None:
            raise RuntimeError("Batch session is unavailable")

        if self.controller.config is None:
            self.controller.config = config
        elif not self.controller.is_running:
            self.controller.config = config
        # While other jobs are active, the new job joins the exact same resolved
        # runtime config; ports/provider/key ownership must not mutate mid-flight.

        batch.prepare_concurrency = normalize_batch_concurrency(int(self.workspace.worker_count.value()))
        job_id = self._next_job_id()
        root = Path(batch.root_dir).resolve() / "jobs" / job_id
        root.mkdir(parents=True, exist_ok=False)
        job = BatchJob(
            job_id=job_id,
            product_url=url,
            run_dir=str((root / "workflow").resolve()),
        )
        batch.jobs.append(job)
        batch.status = "PREPARING"
        self.controller._stopping = False
        self.controller._source_queue.append(job_id)
        if self.controller._mode == "idle":
            self.controller._mode = "prepare"
            self.controller.running_changed.emit(True)
        return job

    def _next_job_id(self) -> str:
        batch = self.controller.batch
        if batch is None:
            return "JOB-001"
        numbers: set[int] = set()
        for job in batch.jobs:
            match = _JOB_ID_RE.match(str(job.job_id))
            if match:
                numbers.add(int(match.group(1)))
        jobs_root = Path(batch.root_dir).resolve() / "jobs"
        if jobs_root.is_dir():
            for path in jobs_root.iterdir():
                match = _JOB_ID_RE.match(path.name)
                if match:
                    numbers.add(int(match.group(1)))
        return f"JOB-{(max(numbers) + 1 if numbers else 1):03d}"

    def _runtime_config(self, url: str) -> RunnerConfig:
        return RunnerConfig(
            product_url=url,
            makro_cdp_port=int(self.workspace.makro_port.value()),
            source_cdp_port=int(self.workspace.source_port.value()),
            source_use_current_page=False,
        )

    # -------------------------------------------------------------- stop/delete
    def stop_row(self, row: Any) -> None:
        job = self._row_job(row)
        if job is not None:
            self.stop_job(str(job.job_id))

    def stop_job(self, job_id: str) -> None:
        job = self._job(job_id)
        if job is None or str(job.status) in _TERMINAL:
            return
        self._remove_from_queues(job_id)
        process = self._process_for_job(job_id)
        job.status = "STOPPED"
        job.stage_detail = "正在停止 · 仅当前商品" if process is not None else "已停止 · 仅当前商品"
        job.error = ""
        job.touch()

        if process is None:
            self.controller._persist_emit(immediate=True)
            self._pump_lanes()
            self._settle_if_idle("")
            return

        self._stop_requested.add(job_id)
        process.terminate()
        QTimer.singleShot(2500, lambda p=process: self._kill_if_running(p))
        self.controller._persist_emit(immediate=True)

    def delete_row(self, row: Any) -> None:
        job = self._row_job(row)
        if job is None:
            self._remove_row_force(row)
            return

        active = self._job_is_scheduled(str(job.job_id))
        answer = QMessageBox.question(
            self.workspace,
            f"删除 {job.job_id}",
            ("这一条仍在运行/排队，会先只停止该商品。\n\n" if active else "")
            + "将从当前 Batch 工作区移除该任务和输入行。\n"
            "Job 目录、日志、报告和 Makro 页面都会保留。\n\n确认删除？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        row._delete_after_job = True
        self.delete_job(str(job.job_id))
        if self._job(str(job.job_id)) is None:
            self._remove_row_force(row)

    def delete_job(self, job_id: str) -> None:
        job = self._job(job_id)
        if job is None:
            return
        process = self._process_for_job(job_id)
        if process is not None:
            self._delete_requested.add(job_id)
            self.stop_job(job_id)
            return
        self._remove_from_queues(job_id)
        self._remove_job_record(job_id)
        self._pump_lanes()
        self._settle_if_idle("")

    def _kill_if_running(self, process: Any) -> None:
        if process in self.controller._processes and process.state() != QProcess.NotRunning:
            process.kill()

    # ----------------------------------------------------------- per-job fill
    def start_job_execution(self, job_id: str) -> None:
        job = self._job(job_id)
        if job is None or str(job.status) != "READY":
            if job is not None:
                QMessageBox.information(
                    self.workspace,
                    "当前任务不可填写",
                    f"{job_id} 当前状态为 {job.status}；只有 READY 商品可以单独真实填写。",
                )
            return
        if self.controller.config is None or self._job_is_scheduled(job_id):
            return

        # Rebind row-owned intent/files/photos at the last responsible moment so
        # customer changes made after prepare are honored by this exact Job.
        row = self._row_for_job(job_id)
        if row is not None:
            self._bind_job_context(row, job)

        answer = QMessageBox.question(
            self.workspace,
            f"确认单独真实填写 · {job_id}",
            "只执行这一件商品的 Full Step 3。\n\n"
            "其他商品继续运行。\n"
            "Save + reopen: ON\n"
            "Product Photos: ON\n"
            "Send to QC: LOCKED / FALSE\n\n确认开始？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        batch = self.controller.batch
        if batch is None:
            return
        was_running = self.controller.is_running
        batch.save_authorized = True
        batch.images_authorized = True
        batch.send_to_qc = False
        batch.execute_concurrency = normalize_batch_concurrency(int(self.workspace.worker_count.value()))
        batch.status = "EXECUTING"
        self.controller._execution_images = True
        self.controller._stopping = False
        if self.controller._mode == "idle":
            self.controller._mode = "execute"
        self.controller._execute_queue.append(job_id)
        if not was_running:
            self.controller.running_changed.emit(True)
        self.controller.state_changed.emit(f"{job_id} · 单独真实填写 · 其他商品继续")
        self.controller._persist_emit(immediate=True)
        self._pump_lanes()

    # -------------------------------------------------------------- Job cards
    def _decorate_card(self, card: QWidget, job_id: str) -> None:
        if job_id in self._cards:
            return
        root = card.layout()
        details = getattr(card, "details_box", None)
        if not isinstance(root, QVBoxLayout):
            return

        host = QFrame(card)
        host.setObjectName("batchIndividualControlStrip")
        row = QHBoxLayout(host)
        row.setContentsMargins(10, 7, 10, 7)
        row.setSpacing(8)
        label = QLabel("JOB CONTROL")
        label.setObjectName("sectionEyebrow")
        hint = QLabel("独立任务控制")
        hint.setObjectName("cardHint")
        fill = QPushButton("单独填写")
        fill.setObjectName("primaryButton")
        stop = QPushButton("停止")
        stop.setObjectName("quietButton")
        delete = QPushButton("删除")
        delete.setObjectName("dangerButton")

        fill.clicked.connect(lambda _checked=False, jid=job_id: self.start_job_execution(jid))
        stop.clicked.connect(lambda _checked=False, jid=job_id: self.stop_job(jid))
        delete.clicked.connect(lambda _checked=False, jid=job_id: self._confirm_delete_job(jid))
        row.addWidget(label)
        row.addWidget(hint, 1)
        row.addWidget(fill)
        row.addWidget(stop)
        row.addWidget(delete)

        index = root.indexOf(details) if details is not None else root.count()
        root.insertWidget(index if index >= 0 else root.count(), host)
        self._cards[job_id] = _CardControls(host, fill, stop, delete, hint)
        self._hide_legacy_remove(job_id)

    def _confirm_delete_job(self, job_id: str) -> None:
        job = self._job(job_id)
        if job is None:
            return
        answer = QMessageBox.question(
            self.workspace,
            f"删除 {job_id}",
            ("任务仍在运行/排队，会先停止这一件商品。\n\n" if self._job_is_scheduled(job_id) else "")
            + "只从当前 Batch 工作区删除；磁盘产物和 Makro 页面保留。\n确认删除？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.delete_job(job_id)

    # ---------------------------------------------------------- context ownership
    def _bind_row_job(self, row: Any, job: Any) -> None:
        row._individual_job_id = str(job.job_id)
        row._delete_after_job = False
        self._bind_job_context(row, job)
        self._refresh_row(row)

    def _bind_job_context(self, row: Any, job: Any) -> None:
        job_id = str(job.job_id)
        offer = getattr(row, "offer_input", None)
        intent = _clean_intent(offer.text() if isinstance(offer, QLineEdit) else "")
        intents = getattr(self.controller, "_listing_offer_intent_by_job_id", None)
        if not isinstance(intents, dict):
            intents = {}
            self.controller._listing_offer_intent_by_job_id = intents
        intents[job_id] = intent
        _write_intent_sidecar(Path(job.run_dir).parent, intent)

        files_ui = getattr(self.window, "_batch_product_files_ui", None)
        files: tuple[Path, ...] = ()
        if files_ui is not None:
            row_files = getattr(files_ui, "_row_files", None)
            if callable(row_files):
                files = tuple(row_files(row))
            writer = getattr(files_ui, "_write_job_sidecar", None)
            if callable(writer):
                writer(job, files)

        file_map = getattr(self.controller, "_supplemental_product_files_by_job_id", None)
        if not isinstance(file_map, dict):
            file_map = {}
            self.controller._supplemental_product_files_by_job_id = file_map
        # Explicit empty tuple is meaningful for duplicate URLs: do not fall back
        # to another occurrence's URL-scoped supplemental file selection.
        file_map[job_id] = files

        photos = getattr(self.window, "_listing_photo_ownership", None)
        set_images = getattr(photos, "_set_job_images", None)
        if callable(set_images):
            set_images(job, files)

    def _install_job_owned_product_files(self) -> None:
        files_ui = getattr(self.window, "_batch_product_files_ui", None)
        if files_ui is None or bool(getattr(files_ui, "_job_owned_files_installed", False)):
            return
        original = files_ui._job_files

        def job_files(_files_ui: Any, job: Any) -> tuple[Path, ...]:
            mapping = getattr(self.controller, "_supplemental_product_files_by_job_id", None)
            job_id = str(getattr(job, "job_id", ""))
            if isinstance(mapping, dict) and job_id in mapping:
                return tuple(Path(value).resolve() for value in mapping[job_id])
            return tuple(original(job))

        files_ui._job_files = MethodType(job_files, files_ui)
        files_ui._job_owned_files_installed = True

    # --------------------------------------------------------------- sync state
    def _match_rows(self, urls: list[str]) -> list[Any]:
        rows = [
            row
            for row in list(self.editor.rows)
            if bool(row.is_enabled()) and str(row.url() or "").strip()
        ]
        used: set[int] = set()
        matched: list[Any] = []
        for url in urls:
            key = str(url).strip()
            for row in rows:
                if id(row) in used or str(row.url() or "").strip() != key:
                    continue
                used.add(id(row))
                matched.append(row)
                break
        return matched

    def _sync_jobs(self, jobs: list[Any]) -> None:
        batch = self.controller.batch
        batch_id = str(batch.batch_id) if batch is not None else ""
        if batch_id != self._batch_id:
            self._batch_id = batch_id
            if not batch_id:
                for row in list(self.editor.rows):
                    row._individual_job_id = ""

        by_id = {str(job.job_id): job for job in jobs}
        claimed = {
            str(getattr(row, "_individual_job_id", "") or "")
            for row in list(self.editor.rows)
            if str(getattr(row, "_individual_job_id", "") or "")
        }
        for job in jobs:
            job_id = str(job.job_id)
            if job_id in claimed:
                continue
            for row in list(self.editor.rows):
                if str(getattr(row, "_individual_job_id", "") or ""):
                    continue
                if str(row.url() or "").strip() == str(job.product_url).strip():
                    self._bind_row_job(row, job)
                    claimed.add(job_id)
                    break

        for job_id, job in by_id.items():
            card = getattr(self.workspace, "_job_cards", {}).get(job_id)
            if card is not None:
                self._decorate_card(card, job_id)
                self._refresh_card(job)

        for row in list(self.editor.rows):
            job_id = str(getattr(row, "_individual_job_id", "") or "")
            if job_id and job_id not in by_id:
                if bool(getattr(row, "_delete_after_job", False)):
                    self._remove_row_force(row)
                    continue
                row._individual_job_id = ""
            self._refresh_row(row)

        for job_id in list(self._cards):
            if job_id not in by_id:
                self._cards.pop(job_id, None)

    def _refresh_rows(self) -> None:
        for row in list(self.editor.rows):
            self._refresh_row(row)

    def _refresh_row(self, row: Any) -> None:
        controls = self._rows.get(id(row))
        if controls is None:
            return
        job = self._row_job(row)
        scheduled = bool(job is not None and self._job_is_scheduled(str(job.job_id)))
        terminal = bool(job is not None and str(job.status) in _TERMINAL)
        ready = bool(job is not None and str(job.status) == "READY")
        valid = bool(str(row.url() or "").strip()) and bool(row.is_enabled())

        controls.start.setEnabled(valid and (job is None or terminal))
        controls.stop.setEnabled(bool(job is not None and (scheduled or ready or not terminal)))
        controls.delete.setEnabled(bool(str(row.url() or "").strip()) or job is not None)
        controls.start.setText(
            "运行中" if scheduled else "重启" if terminal else "已准备" if ready else "启动"
        )

    def _refresh_card(self, job: Any) -> None:
        controls = self._cards.get(str(job.job_id))
        if controls is None:
            return
        scheduled = self._job_is_scheduled(str(job.job_id))
        terminal = str(job.status) in _TERMINAL
        controls.fill.setEnabled(str(job.status) == "READY" and not scheduled)
        controls.stop.setEnabled(scheduled or (str(job.status) == "READY" and not terminal))
        controls.delete.setEnabled(True)
        controls.hint.setText(
            "运行中 · 只控制这一件商品"
            if scheduled
            else "READY · 可单独真实填写"
            if str(job.status) == "READY"
            else f"{job.status} · 可删除，历史产物保留"
            if terminal
            else "独立任务控制"
        )
        self._hide_legacy_remove(str(job.job_id))

    def _hide_legacy_remove(self, job_id: str) -> None:
        lifecycle = getattr(self.workspace, "_batch_lifecycle", None)
        old = getattr(lifecycle, "_remove_buttons", {}).get(job_id) if lifecycle is not None else None
        if isinstance(old, QPushButton):
            old.hide()

    # ---------------------------------------------------------- queues/processes
    def _pump_lanes(self) -> None:
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

        active_execute = sum(stage == "execute" for _, stage in self.controller._processes.values())
        while self.controller._execute_queue and active_execute < batch.execute_concurrency:
            self.controller._start_execute_job(self.controller._execute_queue.pop(0))
            active_execute += 1

    def _settle_if_idle(self, _stage: str) -> None:
        if self.controller.is_running or self.controller._mode == "idle":
            return
        self.controller._mode = "idle"
        batch = self.controller.batch
        if batch is not None and str(batch.status) != "STOPPED":
            if any(str(job.status) == "READY" for job in batch.jobs):
                batch.status = "PREPARED"
            elif batch.jobs and all(str(job.status) in _TERMINAL for job in batch.jobs):
                batch.status = "COMPLETE"
            else:
                batch.status = "IDLE"
        self.controller._persist_emit(immediate=True)
        self.controller.running_changed.emit(False)
        self.controller.state_changed.emit("Batch 空闲 · 可继续单独启动商品")

    def _remove_from_queues(self, job_id: str) -> None:
        for name in ("_source_queue", "_prepare_queue", "_execute_queue"):
            queue = getattr(self.controller, name, None)
            if not isinstance(queue, list):
                continue
            while job_id in queue:
                queue.remove(job_id)

    def _process_for_job(self, job_id: str) -> Any | None:
        for process, (owned_job_id, _stage) in self.controller._processes.items():
            if str(owned_job_id) == str(job_id):
                return process
        return None

    def _job_is_scheduled(self, job_id: str) -> bool:
        return bool(
            self._process_for_job(job_id) is not None
            or job_id in self.controller._source_queue
            or job_id in self.controller._prepare_queue
            or job_id in self.controller._execute_queue
        )

    def _job_busy_or_ready(self, job: Any) -> bool:
        return self._job_is_scheduled(str(job.job_id)) or str(job.status) not in _TERMINAL

    def _job(self, job_id: str) -> Any | None:
        batch = self.controller.batch
        if batch is None:
            return None
        return next((job for job in batch.jobs if str(job.job_id) == str(job_id)), None)

    def _row_job(self, row: Any) -> Any | None:
        job_id = str(getattr(row, "_individual_job_id", "") or "")
        return self._job(job_id) if job_id else None

    def _row_for_job(self, job_id: str) -> Any | None:
        return next(
            (
                row
                for row in list(self.editor.rows)
                if str(getattr(row, "_individual_job_id", "") or "") == str(job_id)
            ),
            None,
        )

    def _remove_job_record(self, job_id: str) -> None:
        batch = self.controller.batch
        if batch is None:
            return
        self._remove_from_queues(job_id)
        batch.jobs[:] = [job for job in batch.jobs if str(job.job_id) != str(job_id)]
        for name in ("_listing_offer_intent_by_job_id", "_supplemental_product_files_by_job_id"):
            mapping = getattr(self.controller, name, None)
            if isinstance(mapping, dict):
                mapping.pop(job_id, None)
        pending = getattr(self.workspace, "_pending_logs", None)
        if isinstance(pending, dict):
            pending.pop(job_id, None)
        support = getattr(self.window, "_listing_offer_support", None)
        panels = getattr(support, "_batch_required_panels", None)
        if isinstance(panels, dict):
            panels.pop(job_id, None)
        self.controller._persist_emit(immediate=True)

    def _remove_row_force(self, row: Any) -> None:
        if row not in self.editor.rows:
            return
        self.editor.rows.remove(row)
        self.editor.rows_layout.removeWidget(row)
        self._rows.pop(id(row), None)
        row.deleteLater()
        self.editor._ensure_min_rows()
        self.editor._renumber()
        self.editor._refresh_summary()


def install_batch_individual_controls(workspace: QWidget) -> BatchIndividualControls:
    existing = getattr(workspace, "_batch_individual_controls", None)
    if isinstance(existing, BatchIndividualControls):
        return existing
    manager = BatchIndividualControls(workspace)
    workspace._batch_individual_controls = manager
    return manager


__all__ = ["BatchIndividualControls", "install_batch_individual_controls"]
