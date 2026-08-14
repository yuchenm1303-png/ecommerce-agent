from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from app.product_pack import SUPPORTED_PRODUCT_PACK_SUFFIXES

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
    """Formal Single workspace with URL and customer Product Pack as peer inputs.

    This class owns only input selection. Both paths immediately converge on the
    existing ReadOnlyRunner / Step 1+2 / Resolver / Fill Plan / real-execution
    chain; no second listing implementation is introduced in the GUI.
    """

    def __init__(self, project_root: Path) -> None:
        self._selected_product_files: tuple[Path, ...] = ()
        super().__init__(project_root)

    def _build_input_card(self):
        card = super()._build_input_card()
        layout = card.layout()
        if not isinstance(layout, QVBoxLayout):
            return card

        # WorkflowMainWindow preserves the base Product Source row at index 1.
        # Add the alternate input on that same line so the compact hero card does
        # not gain another vertical row and steal space from the live console.
        source_row = layout.itemAt(1).layout() if layout.count() > 1 else None
        if isinstance(source_row, QHBoxLayout):
            self.product_pack_button = QPushButton("上传资料…")
            self.product_pack_button.setObjectName("quietButton")
            self.product_pack_button.setToolTip(
                "一次选择同一商品的 PDF / Word / Excel / CSV / TXT / 图片 / ZIP。"
                "原始文件会复制进当前任务，表格和文档机械解析后再交给同一 Resolver。"
            )
            self.product_pack_button.clicked.connect(self._pick_product_files)

            self.product_pack_clear_button = QPushButton("清除资料")
            self.product_pack_clear_button.setObjectName("quietButton")
            self.product_pack_clear_button.setVisible(False)
            self.product_pack_clear_button.clicked.connect(self._clear_product_files)

            start_index = source_row.indexOf(self.start_button)
            insert_at = start_index if start_index >= 0 else source_row.count()
            source_row.insertWidget(insert_at, self.product_pack_button)
            source_row.insertWidget(insert_at + 1, self.product_pack_clear_button)

        self.url_input.textEdited.connect(self._on_url_edited)

        for label in card.findChildren(QLabel):
            text = label.text()
            if "只输入一个 1688 / supplier 商品 URL" in text:
                label.setText(
                    "商品来源二选一：粘贴 1688 / supplier 链接，或上传客户现成的文档、表格和图片资料。"
                    "两种入口后续共用同一 Resolver → Fill Plan → Makro 链路。"
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

    def _pick_product_files(self, _checked: bool = False) -> None:
        files, _selected_filter = QFileDialog.getOpenFileNames(
            self,
            "选择同一商品的文档 / 表格 / 图片资料",
            "",
            _PRODUCT_PACK_FILTER,
        )
        if not files:
            return
        paths = tuple(Path(value).resolve() for value in files)
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
            return
        missing = [str(path) for path in paths if not path.is_file()]
        if missing:
            QMessageBox.warning(self, "资料文件不存在", "\n".join(missing[:20]))
            return

        self._selected_product_files = paths
        self.url_input.clear()
        self.url_input.setPlaceholderText(self._pack_summary(paths))
        self.current_page_check.setChecked(False)
        self.product_pack_button.setText(f"资料包 · {len(paths)}")
        self.product_pack_button.setToolTip("\n".join(str(path) for path in paths))
        self.product_pack_clear_button.setVisible(True)
        self._sync_product_input_controls()

    def _clear_product_files(self, _checked: bool = False) -> None:
        self._selected_product_files = ()
        self.url_input.setPlaceholderText("https://detail.1688.com/offer/...")
        self.product_pack_button.setText("上传资料…")
        self.product_pack_button.setToolTip(
            "一次选择同一商品的 PDF / Word / Excel / CSV / TXT / 图片 / ZIP。"
        )
        self.product_pack_clear_button.setVisible(False)
        self._sync_product_input_controls()

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
        if pack_mode:
            self.start_button.setText("新建任务 · 使用资料包")
        else:
            self.start_button.setText("启动单链接任务")

    def _clear_staged_listing_images(self) -> None:
        self._selected_upload_images = []
        if hasattr(self, "real_image_count"):
            self.real_image_count.setText("0 files")
            self.real_image_count.setToolTip("")
        if hasattr(self, "real_upload_check"):
            self.real_upload_check.setChecked(False)

    def _reset_result_views(self) -> None:
        # Never let Listing Photos selected for a previous product leak into a
        # newly prepared product. Product-pack candidates are re-seeded from the
        # exact completed run below and still require explicit upload opt-in.
        self._clear_staged_listing_images()
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

    def _unlock_real_execution(self, result: RunResult) -> None:
        super()._unlock_real_execution(result)
        images = self._pack_listing_images(result)
        if not images:
            return

        self._selected_upload_images = list(images)
        self.real_image_count.setText(f"{len(images)} files · from pack")
        self.real_image_count.setToolTip("\n".join(str(path) for path in images))
        # Keep upload authorization false. The pack supplies candidate bytes; the
        # existing explicit checkbox + confirmation still owns the browser write.
        self.real_upload_check.setChecked(False)
        if result.ready > 0:
            self.real_policy_hint.setText(
                f"read-only acceptance 已通过：READY={result.ready}。资料包中有 {len(images)} 张"
                "可用 Listing Photos 已预选；勾选“上传图片”后才会真正上传。"
                "Save / 图片仍是显式授权，QC 继续锁定。"
            )

    def _set_running(self, running: bool) -> None:
        super()._set_running(running)
        if hasattr(self, "product_pack_button"):
            self.product_pack_button.setEnabled(not running)
            self.product_pack_clear_button.setEnabled(not running)
        if not running:
            self._sync_product_input_controls()


__all__ = ["ProductInputWorkflowMainWindow"]
