from __future__ import annotations

import json
from pathlib import Path
from types import MethodType
from typing import Any

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QMenu, QMessageBox, QPushButton

from app.product_pack import SUPPORTED_PRODUCT_PACK_SUFFIXES


_PRODUCT_FILE_FILTER = (
    "Product materials ("
    "*.pdf *.docx *.xlsx *.xlsm *.csv *.tsv *.txt *.md "
    "*.jpg *.jpeg *.png *.webp *.gif *.avif *.zip"
    ");;Documents (*.pdf *.docx *.txt *.md);;"
    "Tables (*.xlsx *.xlsm *.csv *.tsv);;"
    "Images (*.jpg *.jpeg *.png *.webp *.gif *.avif);;"
    "Archives (*.zip);;All files (*.*)"
)
_SIDECAR = "supplemental-product-files.json"


class BatchProductFilesUi(QObject):
    """Per-job customer evidence files for the existing Batch URL pipeline.

    The supplier URL remains the primary product source. Files selected on one row
    are process-local to that row's Batch job and are forwarded as repeated
    ``--product-file`` arguments only when its read-only prepare subprocess starts.
    """

    def __init__(self, window: Any) -> None:
        super().__init__(window)
        self.window = window
        self.workspace = window.batch_workspace
        self.controller = self.workspace.controller
        self.editor = getattr(self.workspace, "_batch_url_editor", None)
        if self.editor is None:
            raise RuntimeError("Batch product files require BatchUrlEditor")

        for row in list(self.editor.rows):
            self._decorate_row(row)

        original_add_row = self.editor.add_row

        def add_row(_editor: Any, *args: Any, **kwargs: Any):
            row = original_add_row(*args, **kwargs)
            self._decorate_row(row)
            return row

        self.editor.add_row = MethodType(add_row, self.editor)

        original_set_locked = self.editor.set_locked

        def set_locked(_editor: Any, locked: bool) -> None:
            original_set_locked(bool(locked))
            for row in list(_editor.rows):
                self._sync_row_enabled(row)

        self.editor.set_locked = MethodType(set_locked, self.editor)

        original_start_prepare = self.controller.start_prepare

        def start_prepare(_controller: Any, urls: list[str], config: Any, **kwargs: Any):
            mapping = self._files_by_url()
            _controller._supplemental_product_files_by_url = mapping
            batch = original_start_prepare(urls, config, **kwargs)
            for job in batch.jobs:
                files = mapping.get(str(job.product_url).casefold(), ())
                self._write_job_sidecar(job, files)
            return batch

        self.controller.start_prepare = MethodType(start_prepare, self.controller)

        original_spawn = self.controller._spawn

        def spawn(_controller: Any, job_id: str, stage: str, args: list[str]) -> None:
            argv = list(args)
            if stage == "prepare":
                try:
                    job = _controller._job(job_id)
                    files = self._job_files(job)
                except Exception:
                    files = ()
                if files:
                    for path in files:
                        argv.extend(["--product-file", str(path)])
                    _controller.log.emit(
                        f"[{job_id}] supplemental_product_files={len(files)}"
                    )
            original_spawn(job_id, stage, argv)

        self.controller._spawn = MethodType(spawn, self.controller)
        self._refresh_toolbar_hint()

    def _decorate_row(self, row: Any) -> None:
        layout = row.layout()
        remove = getattr(row, "remove_button", None)
        if not isinstance(layout, QHBoxLayout) or remove is None:
            return
        if isinstance(getattr(row, "product_files_button", None), QPushButton):
            return

        row.product_files = tuple()
        button = QPushButton("资料 0", row)
        button.setObjectName("batchProductFilesButton")
        button.setFixedSize(68, 28)
        button.setToolTip("点击直接添加这一条商品的补充资料；可多选 PDF / Word / 表格 / 图片 / ZIP。右键可清空。")
        button.setStyleSheet(
            "QPushButton#batchProductFilesButton {"
            "  min-height:28px; max-height:28px; padding:0 8px;"
            "  border:1px solid rgba(166,211,255,42); border-radius:8px;"
            "  background:rgba(27,54,78,82); color:rgba(224,243,255,220);"
            "  font-size:11px; font-weight:720;"
            "}"
            "QPushButton#batchProductFilesButton:hover {"
            "  border-color:rgba(158,220,255,105); background:rgba(35,75,105,108);"
            "}"
            "QPushButton#batchProductFilesButton:disabled {"
            "  border-color:rgba(255,255,255,14); background:rgba(20,28,38,42);"
            "  color:rgba(225,237,247,74);"
            "}"
        )

        # Primary interaction is a plain QPushButton click. Do not use setMenu():
        # menu-backed buttons are unreliable under the current QWidget/Quick
        # compositor and can consume the click without ever opening QFileDialog.
        button.clicked.connect(
            lambda _checked=False, current=row: self._pick_files(current)
        )

        # Clearing is secondary and therefore lives on an explicit right-click
        # context menu. Uploading never depends on this popup path.
        menu = QMenu(button)
        clear_action = menu.addAction("清空这条资料")
        clear_action.triggered.connect(
            lambda _checked=False, current=row: self._clear_files(current)
        )
        button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        button.customContextMenuRequested.connect(
            lambda pos, current_button=button, current_menu=menu: current_menu.exec(
                current_button.mapToGlobal(pos)
            )
        )

        index = layout.indexOf(remove)
        layout.insertWidget(index if index >= 0 else layout.count(), button, 0, Qt.AlignmentFlag.AlignVCenter)
        row.product_files_button = button
        row.product_files_clear_action = clear_action
        row.toggle.toggled.connect(lambda _checked, current=row: self._sync_row_enabled(current))
        self._refresh_row(row)
        self._sync_row_enabled(row)

    def _pick_files(self, row: Any) -> None:
        files, _selected_filter = QFileDialog.getOpenFileNames(
            self.window,
            "添加这条商品的补充资料",
            "",
            _PRODUCT_FILE_FILTER,
        )
        if not files:
            return
        selected = tuple(Path(value).expanduser().resolve() for value in files)
        invalid = [
            path
            for path in selected
            if not path.is_file() or path.suffix.casefold() not in SUPPORTED_PRODUCT_PACK_SUFFIXES
        ]
        if invalid:
            QMessageBox.warning(
                self.window,
                "存在不可用资料",
                "这些文件不会加入当前商品：\n" + "\n".join(path.name for path in invalid[:20]),
            )
        usable = [path for path in selected if path not in invalid]
        existing = list(self._row_files(row))
        seen = {path for path in existing}
        for path in usable:
            if path in seen:
                continue
            seen.add(path)
            existing.append(path)
        row.product_files = tuple(existing)
        self._refresh_row(row)

    def _clear_files(self, row: Any) -> None:
        row.product_files = tuple()
        self._refresh_row(row)

    @staticmethod
    def _row_files(row: Any) -> tuple[Path, ...]:
        output: list[Path] = []
        seen: set[Path] = set()
        for value in getattr(row, "product_files", ()) or ():
            path = Path(value).expanduser().resolve()
            if path in seen:
                continue
            seen.add(path)
            output.append(path)
        return tuple(output)

    def _refresh_row(self, row: Any) -> None:
        files = self._row_files(row)
        button = getattr(row, "product_files_button", None)
        if isinstance(button, QPushButton):
            button.setText(f"资料 {len(files)}")
            button.setToolTip(
                "\n".join(str(path) for path in files)
                if files
                else "点击直接添加这一条商品的补充资料；可多选 PDF / Word / 表格 / 图片 / ZIP。右键可清空。"
            )
        clear_action = getattr(row, "product_files_clear_action", None)
        if clear_action is not None:
            clear_action.setEnabled(bool(files))

    def _sync_row_enabled(self, row: Any) -> None:
        button = getattr(row, "product_files_button", None)
        if isinstance(button, QPushButton):
            button.setEnabled(bool(row.is_enabled()) and not bool(self.editor.locked))

    def _files_by_url(self) -> dict[str, tuple[Path, ...]]:
        output: dict[str, tuple[Path, ...]] = {}
        for row in list(self.editor.rows):
            url = str(row.url() or "").strip()
            if not row.is_enabled() or not url:
                continue
            files = self._row_files(row)
            if files:
                output[url.casefold()] = files
        return output

    def _job_files(self, job: Any) -> tuple[Path, ...]:
        mapping = getattr(self.controller, "_supplemental_product_files_by_url", {})
        if isinstance(mapping, dict):
            files = mapping.get(str(job.product_url).casefold(), ())
            if files:
                return tuple(Path(value).resolve() for value in files)
        sidecar = Path(job.run_dir).parent / _SIDECAR
        if not sidecar.is_file():
            return ()
        try:
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
            return tuple(
                Path(str(value)).resolve()
                for value in payload.get("files") or []
                if str(value).strip()
            )
        except Exception:
            return ()

    @staticmethod
    def _write_job_sidecar(job: Any, files: tuple[Path, ...]) -> None:
        root = Path(job.run_dir).parent
        root.mkdir(parents=True, exist_ok=True)
        target = root / _SIDECAR
        target.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "product_url": str(job.product_url),
                    "files": [str(path.resolve()) for path in files],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _refresh_toolbar_hint(self) -> None:
        for label in self.editor.findChildren(type(getattr(self.editor, "summary", None))):
            # QLabel is deliberately resolved from an existing editor child here so
            # this layer does not depend on a particular toolbar object name.
            if not hasattr(label, "text"):
                continue
            text = str(label.text() or "")
            if "每条链接独立 SKU规格" in text:
                label.setText("每条链接独立 SKU规格 + 补充资料 · 第 5 条起滚动")
                break


def install_batch_product_files(window: Any) -> BatchProductFilesUi:
    existing = getattr(window, "_batch_product_files_ui", None)
    if isinstance(existing, BatchProductFilesUi):
        return existing
    layer = BatchProductFilesUi(window)
    window._batch_product_files_ui = layer
    return layer


__all__ = ["BatchProductFilesUi", "install_batch_product_files"]
