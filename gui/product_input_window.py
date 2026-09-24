from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from app.product_pack import SUPPORTED_PRODUCT_PACK_SUFFIXES

from .product_pack_review import ProductPackReviewDialog
from .readonly_runner import RunnerConfig
from .result_loader import RunResult
from .workflow_console_window import WorkflowMainWindow


_PRODUCT_PACK_FILTER = (
    "Product materials ("
    "*.pdf *.docx *.xlsx *.xlsm *.csv *.tsv *.txt *.md "
    "*.jpg *.jpeg *.png *.webp *.gif *.avif *.zip"
    ");;Documents (*.pdf *.docx *.txt *.md);;"
    "Tables (*.xlsx *.xlsm *.csv *.tsv);;"
    "Images (*.jpg *.jpeg *.png *.webp *.gif *.avif);;"
    "Archives (*.zip);;All files (*.*)"
)


class ProductInputWorkflowMainWindow(WorkflowMainWindow):
    """Formal Single workspace with URL and customer Product Pack as peer inputs."""

    def __init__(self, project_root: Path) -> None:
        self._selected_product_files: tuple[Path, ...] = ()
        self._product_pack_review_result: RunResult | None = None
        super().__init__(project_root)

    def _build_input_card(self):
        card = super()._build_input_card()
        layout = card.layout()
        if not isinstance(layout, QVBoxLayout):
            return card

        source_row = layout.itemAt(1).layout() if layout.count() > 1 else None
        if isinstance(source_row, QHBoxLayout):
            self.product_pack_button = QPushButton("上传资料…")
            self.product_pack_button.setObjectName("quietButton")
            self.product_pack_button.setToolTip(
                "打开商品资料面板；可反复添加多个文件或整个文件夹，并在开始前统一检查。"
            )
            self.product_pack_button.clicked.connect(self._open_product_pack_panel)

            start_index = source_row.indexOf(self.start_button)
            insert_at = start_index if start_index >= 0 else source_row.count()
            source_row.insertWidget(insert_at, self.product_pack_button)

        self.url_input.textEdited.connect(self._on_url_edited)

        for label in card.findChildren(QLabel):
            text = label.text()
            if "只输入一个 1688 / supplier 商品 URL" in text:
                label.setText(
                    "商品来源二选一：粘贴 1688 / supplier 链接，或打开资料面板上传客户现成的"
                    "文档、表格、图片或 ZIP。两种入口后续共用同一 Resolver → Fill Plan → Makro 链路。"
                )
                label.setWordWrap(True)
                self.product_input_hint = label
                break
        return card

    @staticmethod
    def _pack_summary(paths: tuple[Path, ...]) -> str:
        counts = Counter(path.suffix.casefold() for path in paths)
        groups = {
            "PDF": counts[".pdf"],
            "Word": counts[".docx"],
            "Excel/CSV": sum(counts[key] for key in (".xlsx", ".xlsm", ".csv", ".tsv")),
            "Text": sum(counts[key] for key in (".txt", ".md")),
            "Images": sum(
                counts[key]
                for key in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")
            ),
            "ZIP": counts[".zip"],
        }
        detail = " · ".join(f"{name} {count}" for name, count in groups.items() if count)
        return f"客户资料包 · {len(paths)} files" + (f" · {detail}" if detail else "")

    @staticmethod
    def _file_size_text(path: Path) -> str:
        try:
            size = path.stat().st_size
        except OSError:
            return "—"
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / (1024 * 1024):.1f} MB"

    @staticmethod
    def _file_kind(path: Path) -> str:
        suffix = path.suffix.casefold()
        if suffix == ".pdf":
            return "PDF"
        if suffix == ".docx":
            return "Word"
        if suffix in {".xlsx", ".xlsm", ".csv", ".tsv"}:
            return "Table"
        if suffix in {".txt", ".md"}:
            return "Text"
        if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}:
            return "Image"
        if suffix == ".zip":
            return "ZIP"
        return suffix.lstrip(".").upper() or "File"

    def _validate_product_paths(self, paths: tuple[Path, ...]) -> tuple[Path, ...] | None:
        unsupported = [
            path.name
            for path in paths
            if path.suffix.casefold() not in SUPPORTED_PRODUCT_PACK_SUFFIXES
        ]
        if unsupported:
            QMessageBox.warning(
                self,
                "存在不支持的资料格式",
                "这些文件不会进入商品资料包：\n" + "\n".join(unsupported[:20]),
            )
            return None
        missing = [str(path) for path in paths if not path.is_file()]
        if missing:
            QMessageBox.warning(self, "资料文件不存在", "\n".join(missing[:20]))
            return None
        return paths

    def _set_product_files(self, paths: tuple[Path, ...]) -> None:
        unique: list[Path] = []
        seen: set[Path] = set()
        for path in paths:
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            unique.append(resolved)

        self._selected_product_files = tuple(unique)
        self._product_pack_review_result = None
        if self._selected_product_files:
            self.url_input.clear()
            self.url_input.setPlaceholderText(self._pack_summary(self._selected_product_files))
            self.current_page_check.setChecked(False)
            self.product_pack_button.setText(f"资料包 · {len(self._selected_product_files)}")
            self.product_pack_button.setToolTip("\n".join(str(path) for path in self._selected_product_files))
        else:
            self.url_input.setPlaceholderText("https://detail.1688.com/offer/...")
            self.product_pack_button.setText("上传资料…")
            self.product_pack_button.setToolTip(
                "打开商品资料面板；可反复添加多个文件或整个文件夹，并在开始前统一检查。"
            )
        self._sync_product_input_controls()

    def _add_product_files(self, paths: tuple[Path, ...]) -> bool:
        validated = self._validate_product_paths(paths)
        if validated is None:
            return False
        self._set_product_files(self._selected_product_files + validated)
        return True

    def _pick_product_files(self, _checked: bool = False) -> bool:
        files, _selected_filter = QFileDialog.getOpenFileNames(
            self,
            "添加同一商品的多个文档 / 表格 / 图片",
            "",
            _PRODUCT_PACK_FILTER,
        )
        if not files:
            return False
        return self._add_product_files(tuple(Path(value).resolve() for value in files))

    def _pick_product_folder(self, _checked: bool = False) -> bool:
        folder = QFileDialog.getExistingDirectory(self, "添加商品资料文件夹", "")
        if not folder:
            return False
        root = Path(folder).resolve()
        paths = tuple(
            path.resolve()
            for path in sorted(root.rglob("*"))
            if path.is_file() and path.suffix.casefold() in SUPPORTED_PRODUCT_PACK_SUFFIXES
        )
        if not paths:
            QMessageBox.information(
                self,
                "文件夹没有可用资料",
                "没有找到支持的 PDF / Word / Excel / CSV / TXT / 图片 / ZIP。",
            )
            return False
        return self._add_product_files(paths)

    def _clear_product_files(self, _checked: bool = False) -> None:
        self._set_product_files(())

    def _remove_product_files(self, paths: tuple[Path, ...]) -> None:
        removed = {path.resolve() for path in paths}
        self._set_product_files(
            tuple(path for path in self._selected_product_files if path.resolve() not in removed)
        )

    @staticmethod
    def _detail_section(controller, title: str) -> QVBoxLayout:  # noqa: ANN001
        frame = QFrame()
        frame.setObjectName("cardDetailSection")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)
        heading = QLabel(title)
        heading.setObjectName("cardDetailSectionTitle")
        layout.addWidget(heading)
        controller.body_layout.addWidget(frame)
        return layout

    def _open_product_pack_panel(self, _checked: bool = False) -> None:
        controller = getattr(self, "_card_details", None)
        if controller is None or not callable(getattr(controller, "open_custom", None)):
            QMessageBox.warning(self, "资料面板不可用", "详情面板尚未完成初始化。")
            return

        def populate() -> None:
            overview = self._detail_section(controller, "商品资料包")
            summary = QLabel()
            summary.setObjectName("cardDetailText")
            summary.setWordWrap(True)
            overview.addWidget(summary)

            actions = QHBoxLayout()
            actions.setSpacing(8)
            add_files = QPushButton("添加文件")
            add_files.setObjectName("modalPrimaryButton")
            add_files.setToolTip("一次可多选多个文件；之后还可以继续追加。")
            add_folder = QPushButton("添加文件夹")
            add_folder.setObjectName("modalPrimaryButton")
            add_folder.setToolTip("递归加入文件夹内所有支持的商品资料文件。")
            remove = QPushButton("移除选中")
            remove.setObjectName("quietButton")
            clear = QPushButton("清空全部")
            clear.setObjectName("modalDangerButton")
            actions.addWidget(add_files)
            actions.addWidget(add_folder)
            actions.addWidget(remove)
            actions.addWidget(clear)
            actions.addStretch(1)
            overview.addLayout(actions)

            files_section = self._detail_section(controller, "已选择文件")
            table = QTableWidget(0, 4)
            table.setObjectName("cardDetailTable")
            table.setHorizontalHeaderLabels(["文件", "类型", "大小", "位置"])
            table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
            table.verticalHeader().setVisible(False)
            table.verticalHeader().setDefaultSectionSize(34)
            table.setAlternatingRowColors(True)
            table.setMinimumHeight(250)
            table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            header = table.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
            files_section.addWidget(table, 1)

            result_section = self._detail_section(controller, "解析与后续")
            result_status = QLabel()
            result_status.setObjectName("cardDetailText")
            result_status.setWordWrap(True)
            result_section.addWidget(result_status)
            result_actions = QHBoxLayout()
            review = QPushButton("查看解析结果")
            review.setObjectName("modalPrimaryButton")
            result_actions.addWidget(review)
            result_actions.addStretch(1)
            result_section.addLayout(result_actions)

            info = QLabel(
                "支持 PDF、Word、Excel/CSV、TXT/Markdown、常见图片和 ZIP。"
                "可以多次追加，也可以直接导入一个资料文件夹；真正开始任务时才会把当前列表"
                "作为一个 Product Pack 交给同一 Resolver。"
            )
            info.setObjectName("modalMetaLabel")
            info.setWordWrap(True)
            controller.body_layout.addWidget(info)
            controller.body_layout.addStretch(1)

            def refresh() -> None:
                paths = self._selected_product_files
                summary.setText(
                    self._pack_summary(paths)
                    if paths
                    else "尚未添加资料。可以一次多选多个文件，或直接添加一个资料文件夹。"
                )
                table.setRowCount(len(paths))
                for row, path in enumerate(paths):
                    values = (
                        path.name,
                        self._file_kind(path),
                        self._file_size_text(path),
                        str(path.parent),
                    )
                    for column, value in enumerate(values):
                        item = QTableWidgetItem(value)
                        item.setToolTip(str(path))
                        table.setItem(row, column, item)
                remove.setEnabled(bool(paths))
                clear.setEnabled(bool(paths))
                ready = self._product_pack_review_result is not None
                review.setEnabled(ready)
                if ready:
                    result_status.setText(
                        "当前资料包已经完成 Resolver + Fill Plan，可查看字段来源、警告和 required 冲突确认。"
                    )
                elif paths:
                    result_status.setText(
                        "资料已经就绪。关闭面板后点击“新建任务 · 使用资料包”开始解析；"
                        "完成后这里会开放解析结果。"
                    )
                else:
                    result_status.setText("先添加商品资料；没有文件时不会切换到 Product Pack 模式。")

            def add_more_files() -> None:
                self._pick_product_files()
                refresh()

            def add_folder_files() -> None:
                self._pick_product_folder()
                refresh()

            def remove_selected() -> None:
                model = table.selectionModel()
                rows = sorted({index.row() for index in model.selectedRows()}) if model else []
                paths = tuple(
                    self._selected_product_files[row]
                    for row in rows
                    if 0 <= row < len(self._selected_product_files)
                )
                if paths:
                    self._remove_product_files(paths)
                    refresh()

            def clear_all() -> None:
                self._clear_product_files()
                refresh()

            def open_review() -> None:
                controller.close()
                QTimer.singleShot(0, self._open_product_pack_review)

            add_files.clicked.connect(add_more_files)
            add_folder.clicked.connect(add_folder_files)
            remove.clicked.connect(remove_selected)
            clear.clicked.connect(clear_all)
            review.clicked.connect(open_review)
            refresh()

        controller.open_custom(
            title="上传商品资料",
            eyebrow="PRODUCT PACK · FILE MANAGER",
            populate=populate,
            ratio=(0.84, 0.84),
        )

    def _on_url_edited(self, text: str) -> None:
        if str(text or "").strip() and self._selected_product_files:
            self._clear_product_files()

    def _sync_product_input_controls(self) -> None:
        pack_mode = bool(self._selected_product_files)
        busy = self.runner.is_running
        self.current_page_check.setEnabled(not pack_mode and not busy)
        if pack_mode:
            self.current_page_check.setChecked(False)
        for name in ("step1_button", "step2_button", "step3_button"):
            button = getattr(self, name, None)
            if isinstance(button, QPushButton):
                button.setEnabled(not pack_mode and not busy)
                if pack_mode:
                    button.setToolTip(
                        "客户资料包使用完整新建任务；阶段诊断仍用于 URL / 当前 Makro 页面。"
                    )
        self.start_button.setText("新建任务 · 使用资料包" if pack_mode else "启动单链接任务")

    def _clear_staged_listing_images(self) -> None:
        self._selected_upload_images = []
        if hasattr(self, "real_image_count"):
            self.real_image_count.setText("0 files")
            self.real_image_count.setToolTip("")
        if hasattr(self, "real_upload_check"):
            self.real_upload_check.setChecked(False)

    def _reset_result_views(self) -> None:
        self._clear_staged_listing_images()
        self._product_pack_review_result = None
        super()._reset_result_views()

    def _start_mode(self, mode: str) -> None:
        if not self._selected_product_files:
            super()._start_mode(mode)
            return
        if mode != "full":
            QMessageBox.information(
                self,
                "资料包使用完整任务",
                "客户资料包会从商品证据 → Step 1 → Step 2 → Resolver → Fill Plan 完整运行；"
                "阶段诊断不单独消费资料包。",
            )
            return
        if self._batch_is_busy():
            QMessageBox.warning(self, "无法开始 Single", "Batch worker 仍在运行。")
            return
        if getattr(self, "execution_runner", None) is not None and self.execution_runner.is_running:
            QMessageBox.warning(self, "无法开始", "真实 Step 3 执行仍在运行。")
            return

        config = RunnerConfig(
            product_files=tuple(str(path) for path in self._selected_product_files),
            makro_cdp_port=int(self.makro_port.value()),
            source_cdp_port=int(self.source_port.value()),
            source_use_current_page=False,
        )
        try:
            self._reset_result_views()
            self.runner.start(config, mode="full")
            self.open_run_button.setEnabled(True)
        except Exception as exc:
            QMessageBox.critical(self, "无法开始资料包任务", str(exc))

    @staticmethod
    def _pack_listing_images(result: RunResult) -> tuple[Path, ...]:
        workflow_path = result.run_dir / "run-manifest.json"
        if not workflow_path.is_file():
            return ()
        try:
            workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ()
        if str(workflow.get("input_mode") or "") != "customer_product_pack":
            return ()

        product_input = workflow.get("product_input") or {}
        values = product_input.get("listing_images") if isinstance(product_input, dict) else []
        if not values:
            bootstrap = workflow.get("bootstrap_source") or {}
            values = bootstrap.get("listing_images") if isinstance(bootstrap, dict) else []
        if not values:
            outputs = result.resolver.get("outputs") or {}
            values = (
                outputs.get("primary_source_listing_images")
                if isinstance(outputs, dict)
                else []
            )
        if not isinstance(values, list):
            return ()

        output: list[Path] = []
        seen: set[Path] = set()
        for value in values:
            path = Path(str(value)).resolve()
            if not path.is_file() or path in seen:
                continue
            seen.add(path)
            output.append(path)
        return tuple(output)

    def _is_pack_result(self, result: RunResult) -> bool:
        workflow_path = result.run_dir / "run-manifest.json"
        if not workflow_path.is_file():
            return False
        try:
            workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return str(workflow.get("input_mode") or "") == "customer_product_pack"

    def _open_product_pack_review(self, _checked: bool = False) -> None:
        result = self._product_pack_review_result
        if result is None:
            QMessageBox.information(self, "暂无解析结果", "请先完成当前客户资料包的 Resolver + Fill Plan。")
            return
        try:
            dialog = ProductPackReviewDialog(result, self)
            dialog.exec()
        except Exception as exc:
            QMessageBox.warning(self, "无法打开资料解析结果", str(exc))
            return
        if dialog.confirmed_count:
            self.fields_hint.setText(
                f"资料包冲突确认完成 · 当前任务已明确确认 {dialog.confirmed_count} 个 required 字段"
            )
            self.real_policy_hint.setText(
                "已确认的冲突值会作为当前任务的显式用户 override；真正写入前仍由 canonical executor "
                "重新绑定 live field 并校验 option / unit。Save / 图片继续显式授权，QC 永久锁定。"
            )

    def _unlock_real_execution(self, result: RunResult) -> None:
        super()._unlock_real_execution(result)
        if self._is_pack_result(result):
            self._product_pack_review_result = result

        images = self._pack_listing_images(result)
        if not images:
            return

        self._selected_upload_images = list(images)
        self.real_image_count.setText(f"{len(images)} files · from pack")
        self.real_image_count.setToolTip("\n".join(str(path) for path in images))
        self.real_upload_check.setChecked(False)
        if result.ready > 0:
            self.real_policy_hint.setText(
                f"read-only acceptance 已通过：READY={result.ready}。资料包中有 {len(images)} 张"
                "可用 Listing Photos 已预选；上传资料面板可继续查看解析结果 / required 冲突。"
                "勾选“上传图片”后才会真正上传；Save / 图片仍是显式授权，QC 继续锁定。"
            )

    def _set_running(self, running: bool) -> None:
        super()._set_running(running)
        if hasattr(self, "product_pack_button"):
            self.product_pack_button.setEnabled(not running)
        if not running:
            self._sync_product_input_controls()


__all__ = ["ProductInputWorkflowMainWindow"]
