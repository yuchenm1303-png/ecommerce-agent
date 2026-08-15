from __future__ import annotations

import json
from pathlib import Path
from types import MethodType
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.product_pack import SUPPORTED_PRODUCT_PACK_SUFFIXES

from .anchored_quick_panel import AnchoredQuickPanel, AnchoredQuickPanelPlacement


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


class BatchProductFilesDetail(QWidget):
    """Content surface placed inside the reusable anchored popup shell."""

    add_requested = Signal()
    remove_requested = Signal(str)
    clear_requested = Signal()
    done_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("batchProductFilesDetail")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)
        title_box = QVBoxLayout()
        title_box.setSpacing(1)

        title = QLabel("商品资料")
        title.setObjectName("batchProductFilesTitle")
        subtitle = QLabel("仅用于这一条商品 · PDF / Word / 表格 / 图片 / ZIP")
        subtitle.setObjectName("batchProductFilesSubtitle")
        subtitle.setTextFormat(Qt.TextFormat.PlainText)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)

        self.count_chip = QLabel("0 FILES")
        self.count_chip.setObjectName("batchProductFilesCount")
        self.count_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.count_chip.setMinimumWidth(70)
        self.count_chip.setFixedHeight(26)

        header.addLayout(title_box, 1)
        header.addWidget(self.count_chip, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        self.context_label = QLabel("等待选择商品行")
        self.context_label.setObjectName("batchProductFilesContext")
        self.context_label.setTextFormat(Qt.TextFormat.PlainText)
        self.context_label.setToolTip("当前资料只绑定到这一条 Batch 商品")
        root.addWidget(self.context_label)

        self.add_button = QPushButton("＋ 选择资料")
        self.add_button.setObjectName("batchProductFilesPrimary")
        self.add_button.setFixedHeight(44)
        self.add_button.setToolTip(
            "可多选 PDF / Word / 表格 / 图片 / ZIP；资料只属于当前商品。"
        )
        self.add_button.clicked.connect(self.add_requested.emit)
        root.addWidget(self.add_button)

        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("batchProductFilesScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setMinimumHeight(138)

        self.files_host = QWidget()
        self.files_host.setObjectName("batchProductFilesHost")
        self.files_layout = QVBoxLayout(self.files_host)
        self.files_layout.setContentsMargins(0, 0, 0, 0)
        self.files_layout.setSpacing(6)
        self.scroll.setWidget(self.files_host)
        root.addWidget(self.scroll, 1)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.clear_button = QPushButton("清空全部")
        self.clear_button.setObjectName("batchProductFilesQuiet")
        self.clear_button.setFixedHeight(32)
        self.clear_button.clicked.connect(self.clear_requested.emit)
        self.done_button = QPushButton("完成")
        self.done_button.setObjectName("batchProductFilesDone")
        self.done_button.setFixedHeight(32)
        self.done_button.setMinimumWidth(76)
        self.done_button.clicked.connect(self.done_requested.emit)
        footer.addWidget(self.clear_button)
        footer.addStretch(1)
        footer.addWidget(self.done_button)
        root.addLayout(footer)

        self.setStyleSheet(
            """
            QWidget#batchProductFilesDetail {
                background: transparent;
                color: rgba(241, 249, 255, 236);
            }
            QLabel#batchProductFilesTitle {
                color: rgba(248, 252, 255, 247);
                font-size: 17px;
                font-weight: 780;
            }
            QLabel#batchProductFilesSubtitle,
            QLabel#batchProductFilesContext {
                color: rgba(218, 235, 247, 120);
                font-size: 10px;
                font-weight: 640;
            }
            QLabel#batchProductFilesCount {
                color: rgba(207, 250, 246, 225);
                background: rgba(111, 213, 213, 30);
                border: 1px solid rgba(145, 246, 237, 48);
                border-radius: 10px;
                padding: 0 8px;
                font-size: 9px;
                font-weight: 760;
            }
            QPushButton#batchProductFilesPrimary {
                border: 1px solid rgba(159, 242, 241, 58);
                border-radius: 15px;
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(255,255,255,20),
                    stop:0.52 rgba(92,205,220,38),
                    stop:1 rgba(143,105,255,34)
                );
                color: rgba(244, 253, 255, 242);
                font-size: 12px;
                font-weight: 760;
            }
            QPushButton#batchProductFilesPrimary:hover {
                border-color: rgba(169, 248, 246, 100);
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(255,255,255,27),
                    stop:0.52 rgba(92,205,220,55),
                    stop:1 rgba(143,105,255,48)
                );
            }
            QPushButton#batchProductFilesPrimary:pressed {
                background: rgba(55, 128, 152, 78);
            }
            QScrollArea#batchProductFilesScroll,
            QScrollArea#batchProductFilesScroll > QWidget > QWidget,
            QWidget#batchProductFilesHost {
                background: transparent;
                border: none;
            }
            QFrame#batchProductFileEntry {
                background: rgba(4, 17, 31, 86);
                border: 1px solid rgba(255, 255, 255, 20);
                border-radius: 12px;
            }
            QLabel#batchProductFileName {
                color: rgba(241, 249, 255, 222);
                font-size: 10px;
                font-weight: 720;
            }
            QLabel#batchProductFileMeta {
                color: rgba(179, 218, 239, 105);
                font-size: 8px;
                font-weight: 700;
            }
            QLabel#batchProductFilesEmpty {
                color: rgba(215, 234, 247, 110);
                font-size: 10px;
                font-weight: 640;
            }
            QPushButton#batchProductFileRemove {
                min-height: 27px;
                max-height: 27px;
                border: 1px solid rgba(255, 154, 173, 38);
                border-radius: 9px;
                background: rgba(129, 46, 64, 40);
                color: rgba(255, 215, 223, 195);
                font-size: 9px;
                font-weight: 720;
            }
            QPushButton#batchProductFileRemove:hover {
                border-color: rgba(255, 162, 181, 80);
                background: rgba(169, 51, 75, 68);
            }
            QPushButton#batchProductFilesQuiet,
            QPushButton#batchProductFilesDone {
                border-radius: 11px;
                padding: 0 13px;
                font-size: 10px;
                font-weight: 730;
            }
            QPushButton#batchProductFilesQuiet {
                border: 1px solid rgba(255,255,255,23);
                background: rgba(255,255,255,10);
                color: rgba(229,241,250,155);
            }
            QPushButton#batchProductFilesQuiet:hover {
                border-color: rgba(255,255,255,43);
                background: rgba(255,255,255,17);
            }
            QPushButton#batchProductFilesQuiet:disabled {
                border-color: rgba(255,255,255,12);
                color: rgba(229,241,250,65);
            }
            QPushButton#batchProductFilesDone {
                border: 1px solid rgba(151, 238, 235, 47);
                background: rgba(69, 164, 176, 47);
                color: rgba(231, 253, 251, 225);
            }
            QPushButton#batchProductFilesDone:hover {
                border-color: rgba(165, 246, 242, 80);
                background: rgba(76, 177, 188, 66);
            }
            """
        )
        self.set_files(())

    def set_context(
        self,
        *,
        row_number: int,
        url: str,
        files: tuple[Path, ...],
    ) -> None:
        host = url.split("://", 1)[-1].split("/", 1)[0] if url else "未填写链接"
        self.context_label.setText(f"ITEM {row_number:02d}  ·  {host}")
        self.context_label.setToolTip(url or "当前资料只绑定到这一条 Batch 商品")
        self.set_files(files)

    def set_files(self, files: tuple[Path, ...]) -> None:
        while self.files_layout.count():
            item = self.files_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self.count_chip.setText(f"{len(files)} FILES")
        self.clear_button.setEnabled(bool(files))
        if not files:
            empty = QLabel("还没有补充资料\n点击上方“选择资料”添加到当前商品。")
            empty.setObjectName("batchProductFilesEmpty")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setWordWrap(True)
            empty.setMinimumHeight(126)
            self.files_layout.addWidget(empty)
            self.files_layout.addStretch(1)
            return

        for path in files:
            entry = QFrame()
            entry.setObjectName("batchProductFileEntry")
            row = QHBoxLayout(entry)
            row.setContentsMargins(10, 7, 8, 7)
            row.setSpacing(8)

            meta = QVBoxLayout()
            meta.setSpacing(1)
            display_name = path.name
            if len(display_name) > 50:
                display_name = f"{display_name[:26]}…{display_name[-20:]}"
            name = QLabel(display_name)
            name.setObjectName("batchProductFileName")
            name.setTextFormat(Qt.TextFormat.PlainText)
            name.setToolTip(str(path))
            name.setSizePolicy(
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Preferred,
            )
            suffix = QLabel((path.suffix.lstrip(".") or "FILE").upper())
            suffix.setObjectName("batchProductFileMeta")
            meta.addWidget(name)
            meta.addWidget(suffix)

            remove = QPushButton("移除")
            remove.setObjectName("batchProductFileRemove")
            remove.setFixedWidth(48)
            remove.clicked.connect(
                lambda _checked=False, value=str(path): self.remove_requested.emit(value)
            )

            row.addLayout(meta, 1)
            row.addWidget(remove, 0, Qt.AlignmentFlag.AlignVCenter)
            self.files_layout.addWidget(entry)

        self.files_layout.addStretch(1)


class BatchProductFilesUi(QObject):
    """Per-job customer evidence files for the existing Batch URL pipeline.

    The supplier URL remains the primary product source. Files selected on one row
    are process-local to that row's Batch job and are forwarded as repeated
    ``--product-file`` arguments only when its read-only prepare subprocess starts.
    The anchored panel is presentation-only; file ownership and forwarding remain
    URL/job scoped exactly as before.
    """

    def __init__(self, window: Any) -> None:
        super().__init__(window)
        self.window = window
        self.workspace = window.batch_workspace
        self.controller = self.workspace.controller
        self.editor = getattr(self.workspace, "_batch_url_editor", None)
        if self.editor is None:
            raise RuntimeError("Batch product files require BatchUrlEditor")

        self._active_row: Any | None = None
        self._detail = BatchProductFilesDetail()
        self._panel = AnchoredQuickPanel(
            self.window,
            self._detail,
            desired_width=430,
            desired_height=352,
            min_height=260,
            preferred_placement=AnchoredQuickPanelPlacement.Above,
            horizontal_bias=0.80,
            corner_radius=25,
            tail_height=12,
            tail_half_width=15,
            safe_margin=12,
            anchor_gap=7,
            body_padding=14,
        )
        self._detail.add_requested.connect(self._pick_active_files)
        self._detail.remove_requested.connect(self._remove_active_file)
        self._detail.clear_requested.connect(self._clear_active_files)
        self._detail.done_requested.connect(self._panel.dismiss)
        self._panel.dismissed.connect(self._panel_dismissed)

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
        button.setProperty("panelOpen", False)
        button.setFixedSize(76, 28)
        button.setToolTip(
            "点击展开这一条商品的资料详情；可在弹窗中添加、查看、移除或清空资料。"
        )
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
            "QPushButton#batchProductFilesButton[panelOpen=\"true\"] {"
            "  border-color:rgba(157,243,239,118);"
            "  background:rgba(43,102,119,118); color:rgba(239,255,253,242);"
            "}"
            "QPushButton#batchProductFilesButton:disabled {"
            "  border-color:rgba(255,255,255,14); background:rgba(20,28,38,42);"
            "  color:rgba(225,237,247,74);"
            "}"
        )
        button.clicked.connect(
            lambda _checked=False, current=row: self._toggle_panel(current)
        )

        index = layout.indexOf(remove)
        layout.insertWidget(
            index if index >= 0 else layout.count(),
            button,
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )
        row.product_files_button = button
        row.toggle.toggled.connect(
            lambda _checked, current=row: self._sync_row_enabled(current)
        )
        self._refresh_row(row)
        self._sync_row_enabled(row)

    def _toggle_panel(self, row: Any) -> None:
        if self._panel.isVisible() and self._active_row is row:
            self._panel.dismiss()
            return
        self._open_panel(row)

    def _open_panel(self, row: Any, *, animate: bool = True) -> None:
        if (
            row not in self.editor.rows
            or not bool(row.is_enabled())
            or bool(self.editor.locked)
        ):
            return

        previous = self._active_row
        if previous is not None and previous is not row:
            self._set_button_open(previous, False)
            self._refresh_row(previous)

        button = getattr(row, "product_files_button", None)
        if not isinstance(button, QPushButton):
            return

        self._active_row = row
        self._refresh_panel(row)
        self._set_button_open(row, True)
        self._panel.show_anchored(button, animate=animate)
        self._refresh_row(row)

    def _panel_dismissed(self) -> None:
        row = self._active_row
        self._active_row = None
        if row is not None:
            self._set_button_open(row, False)
            self._refresh_row(row)

    def _refresh_panel(self, row: Any) -> None:
        if self._active_row is not row:
            return
        try:
            row_number = self.editor.rows.index(row) + 1
        except ValueError:
            row_number = 1
        self._detail.set_context(
            row_number=row_number,
            url=str(row.url() or "").strip(),
            files=self._row_files(row),
        )

    @staticmethod
    def _set_button_open(row: Any, opened: bool) -> None:
        button = getattr(row, "product_files_button", None)
        if not isinstance(button, QPushButton):
            return
        button.setProperty("panelOpen", bool(opened))
        style = button.style()
        style.unpolish(button)
        style.polish(button)
        button.update()

    def _pick_active_files(self) -> None:
        row = self._active_row
        if row is not None:
            self._pick_files(row)

    def _pick_files(self, row: Any) -> None:
        if row not in self.editor.rows:
            return

        # A native file picker is its own top-level surface. Close the Qt.Popup
        # shell first, retain the row ownership locally, then restore the detail
        # panel after selection so the user returns to the exact same item.
        if self._panel.isVisible():
            self._panel.hide()

        files, _selected_filter = QFileDialog.getOpenFileNames(
            self.window,
            "添加这条商品的补充资料",
            "",
            _PRODUCT_FILE_FILTER,
        )
        if files:
            selected = tuple(Path(value).expanduser().resolve() for value in files)
            invalid = [
                path
                for path in selected
                if not path.is_file()
                or path.suffix.casefold() not in SUPPORTED_PRODUCT_PACK_SUFFIXES
            ]
            if invalid:
                QMessageBox.warning(
                    self.window,
                    "存在不可用资料",
                    "这些文件不会加入当前商品：\n"
                    + "\n".join(path.name for path in invalid[:20]),
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

        if (
            row in self.editor.rows
            and bool(row.is_enabled())
            and not bool(self.editor.locked)
        ):
            self._open_panel(row)

    def _remove_active_file(self, value: str) -> None:
        row = self._active_row
        if row is None:
            return
        target = Path(value).expanduser().resolve()
        row.product_files = tuple(
            path for path in self._row_files(row) if path != target
        )
        self._refresh_row(row)

    def _clear_active_files(self) -> None:
        row = self._active_row
        if row is not None:
            self._clear_files(row)

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
        opened = bool(self._active_row is row and self._panel.isVisible())
        if isinstance(button, QPushButton):
            button.setText(f"资料 {len(files)}  {'▴' if opened else '▾'}")
            if files:
                preview = "\n".join(path.name for path in files[:8])
                extra = len(files) - 8
                button.setToolTip(
                    "点击展开资料详情\n"
                    + preview
                    + (f"\n…还有 {extra} 个文件" if extra > 0 else "")
                )
            else:
                button.setToolTip(
                    "点击展开这一条商品的资料详情；可在弹窗中添加、查看、移除或清空资料。"
                )
        if opened:
            self._refresh_panel(row)

    def _sync_row_enabled(self, row: Any) -> None:
        button = getattr(row, "product_files_button", None)
        enabled = bool(row.is_enabled()) and not bool(self.editor.locked)
        if self._active_row is row and not enabled:
            self._panel.dismiss(animate=False)
        if isinstance(button, QPushButton):
            button.setEnabled(enabled)

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


__all__ = [
    "BatchProductFilesDetail",
    "BatchProductFilesUi",
    "install_batch_product_files",
]
