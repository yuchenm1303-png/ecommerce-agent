from __future__ import annotations

import json
from collections import defaultdict, deque
from pathlib import Path
from types import MethodType
from typing import Any, Iterable

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMessageBox,
    QPushButton,
    QWidget,
)

from .result_loader import latest_resolver_manifest


_MANUAL_PHOTO_SIDECAR = "listing-photo-intent.json"
_LISTING_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_MAX_PRODUCT_PHOTOS = 5
_LISTING_IMAGE_FILTER = "Product Photos (*.jpg *.jpeg *.png *.webp);;All files (*.*)"


def _normalized_manual_images(values: Iterable[object]) -> tuple[Path, ...]:
    """Return explicit Product Photos only, preserving user selection order."""

    output: list[Path] = []
    seen: set[str] = set()
    for value in values:
        path = Path(str(value)).expanduser().resolve()
        if path.suffix.casefold() not in _LISTING_IMAGE_SUFFIXES:
            continue
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(path)
        if len(output) >= _MAX_PRODUCT_PHOTOS:
            break
    return tuple(output)


def _replace_upload_image_args(argv: list[str], images: tuple[Path, ...]) -> list[str]:
    """Replace only Product Photos arguments; leave field execution untouched."""

    output: list[str] = []
    index = 0
    while index < len(argv):
        if argv[index] == "--upload-image":
            index += 2
            continue
        output.append(argv[index])
        index += 1
    for image in images:
        output.extend(["--upload-image", str(image)])
    return output


def _layout_containing(root: QLayout | None, widget: QWidget) -> QLayout | None:
    if root is None:
        return None
    for index in range(root.count()):
        item = root.itemAt(index)
        child_widget = item.widget()
        if child_widget is widget:
            return root
        child_layout = item.layout()
        found = _layout_containing(child_layout, widget) if child_layout is not None else None
        if found is None and child_widget is not None:
            found = _layout_containing(child_widget.layout(), widget)
        if found is not None:
            return found
    return None


def _customer_pack_images(outputs: dict[str, Any]) -> set[Path]:
    manifest_text = str(outputs.get("product_pack_manifest") or "").strip()
    if not manifest_text:
        return set()
    manifest = Path(manifest_text).expanduser().resolve()
    if not manifest.is_file():
        return set()
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return set()
    if not isinstance(payload, dict):
        return set()
    values = [
        *(payload.get("evidence_images") or []),
        *(payload.get("listing_images") or []),
    ]
    return {
        Path(str(value)).expanduser().resolve()
        for value in values
        if str(value).strip()
    }


def _supplier_listing_images(run_dir: Path) -> tuple[Path, ...]:
    """Return automatic listing images with customer auxiliary images excluded."""

    manifest_path = latest_resolver_manifest(run_dir, "03-hot-resolver")
    if manifest_path is None or not manifest_path.is_file():
        return ()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return ()
    outputs = manifest.get("outputs") or {}
    if not isinstance(outputs, dict):
        return ()
    customer_images = _customer_pack_images(outputs)
    output: list[Path] = []
    seen: set[Path] = set()
    for value in outputs.get("primary_source_listing_images") or []:
        path = Path(str(value)).expanduser().resolve()
        if path in customer_images or path in seen or not path.is_file():
            continue
        seen.add(path)
        output.append(path)
        if len(output) >= _MAX_PRODUCT_PHOTOS:
            break
    return tuple(output)


class ListingPhotoOwnership:
    """Keep AI auxiliary evidence and Makro Product Photos as separate intents.

    Auxiliary files are owned by the existing Product Pack / Batch supplemental
    evidence UI and may be inspected by Resolver. Product Photos have their own
    explicit picker and are the only user-selected images promoted to
    ``--upload-image``. Customer auxiliary images are also removed from the
    automatic supplier-photo fallback before real execution.
    """

    def __init__(self, window: Any) -> None:
        self.window = window
        self.workspace = window.batch_workspace
        self.controller = self.workspace.controller
        self.editor = getattr(self.workspace, "_batch_url_editor", None)
        self.files_ui = getattr(window, "_batch_product_files_ui", None)
        if self.editor is None:
            raise RuntimeError("Listing photo ownership requires Batch URL editor")

        self._manual_images_by_job_id: dict[str, tuple[Path, ...]] = {}
        self.single_photo_button: QPushButton | None = None

        self._install_single_surface()
        self._install_batch_surface()
        self._install_prepare_binding()
        self._install_execute_refresh()
        self._install_execution_override()
        self._clarify_auxiliary_copy()

    # --------------------------------------------------------------- Single UI
    def _install_single_surface(self) -> None:
        auxiliary = getattr(self.window, "product_pack_button", None)
        if isinstance(auxiliary, QPushButton):
            original_set_files = getattr(self.window, "_set_product_files", None)
            if callable(original_set_files):
                def set_product_files(_window: Any, paths: tuple[Path, ...]) -> None:
                    original_set_files(paths)
                    self._sync_single_auxiliary_copy()

                self.window._set_product_files = MethodType(set_product_files, self.window)
            auxiliary.clicked.connect(
                lambda _checked=False: QTimer.singleShot(0, self._retitle_single_auxiliary_panel)
            )
            self._sync_single_auxiliary_copy()

            root = self.window.centralWidget().layout() if self.window.centralWidget() is not None else None
            row = _layout_containing(root, auxiliary)
            if isinstance(row, QHBoxLayout):
                button = QPushButton("商品图片 0")
                button.setObjectName("quietButton")
                button.setToolTip(
                    "只选择最终写入 Makro Product Photos 的图片；这些图片不会作为 AI / Resolver 辅助资料。"
                )
                button.clicked.connect(self._pick_single_images)
                insert_at = row.indexOf(auxiliary) + 1
                row.insertWidget(max(0, insert_at), button)
                self.single_photo_button = button

        real_picker = getattr(self.window, "real_pick_images_button", None)
        if isinstance(real_picker, QPushButton):
            try:
                real_picker.clicked.disconnect()
            except (RuntimeError, TypeError):
                pass
            real_picker.setText("管理商品图片…")
            real_picker.setToolTip(
                "与顶部“商品图片”共用同一列表；只用于 Makro Product Photos，不参与 AI 字段理解。"
            )
            real_picker.clicked.connect(self._pick_single_images)

        original_reset = getattr(self.window, "_reset_result_views", None)
        if callable(original_reset):
            def reset_result_views(_window: Any) -> None:
                manual = list(getattr(_window, "_selected_upload_images", []) or [])
                original_reset()
                _window._selected_upload_images = manual
                self._sync_single_photo_copy()

            self.window._reset_result_views = MethodType(reset_result_views, self.window)

        original_unlock = getattr(self.window, "_unlock_real_execution", None)
        if callable(original_unlock):
            def unlock_real_execution(_window: Any, result: Any) -> None:
                manual = list(getattr(_window, "_selected_upload_images", []) or [])
                original_unlock(result)
                # ProductInputWindow historically promoted Product Pack images to
                # Product Photos here. Undo that promotion: auxiliary evidence is
                # never an upload intent.
                _window._selected_upload_images = manual
                if manual:
                    _window.real_image_count.setText(f"MANUAL {len(manual)}")
                    _window.real_image_count.setToolTip("\n".join(str(path) for path in manual))
                elif getattr(_window, "_selected_product_files", ()):
                    automatic = _supplier_listing_images(Path(result.run_dir))
                    if automatic:
                        _window.real_image_count.setText(f"AUTO SUPPLIER {len(automatic)}")
                        _window.real_image_count.setToolTip("\n".join(str(path) for path in automatic))
                    else:
                        _window.real_image_count.setText("商品图片 0 · 请手动选择")
                        _window.real_image_count.setToolTip(
                            "辅助资料中的图片只给 AI 看，不会自动上传到 Makro。"
                        )
                self._sync_single_photo_copy()

            self.window._unlock_real_execution = MethodType(unlock_real_execution, self.window)

        original_start_real = getattr(self.window, "_start_real_execution", None)
        if callable(original_start_real):
            def start_real_execution(_window: Any) -> None:
                manual = list(getattr(_window, "_selected_upload_images", []) or [])
                auxiliary_files = tuple(getattr(_window, "_selected_product_files", ()) or ())
                upload_enabled = bool(getattr(_window, "real_upload_check", None).isChecked())
                if not upload_enabled or manual or not auxiliary_files:
                    original_start_real()
                    return

                result = getattr(_window, "current_result", None)
                automatic = _supplier_listing_images(Path(result.run_dir)) if result is not None else ()
                if not automatic:
                    QMessageBox.warning(
                        _window,
                        "请先选择商品图片",
                        "辅助资料中的图片只给 AI / Resolver 看，不会写入 Makro Product Photos。\n\n"
                        "请使用顶部“商品图片”入口选择要真正上传的图片。",
                    )
                    return
                _window._selected_upload_images = list(automatic)
                try:
                    original_start_real()
                finally:
                    _window._selected_upload_images = manual
                    self._sync_single_photo_copy()

            self.window._start_real_execution = MethodType(start_real_execution, self.window)

        url_input = getattr(self.window, "url_input", None)
        if url_input is not None:
            url_input.textEdited.connect(self._single_url_edited)
        self._sync_single_photo_copy()

    def _sync_single_auxiliary_copy(self) -> None:
        button = getattr(self.window, "product_pack_button", None)
        if not isinstance(button, QPushButton):
            return
        files = tuple(getattr(self.window, "_selected_product_files", ()) or ())
        button.setText(f"辅助资料 {len(files)}" if files else "辅助资料…")
        if files:
            button.setToolTip(
                "仅作为 AI / Resolver 商品证据，不会写入 Makro Product Photos：\n"
                + "\n".join(str(path) for path in files)
            )
        else:
            button.setToolTip(
                "添加给 AI / Resolver 看的辅助资料：PDF、Word、表格、图片或 ZIP。"
                "这里的图片不会作为 Makro Product Photos 上传。"
            )

    def _retitle_single_auxiliary_panel(self) -> None:
        replacements = {
            "商品资料包": "辅助资料",
            "上传商品资料": "辅助资料",
        }
        for label in self.window.findChildren(QLabel):
            replacement = replacements.get(str(label.text() or "").strip())
            if replacement is not None:
                label.setText(replacement)

    def _pick_single_images(self, _checked: bool = False) -> None:
        existing = list(_normalized_manual_images(getattr(self.window, "_selected_upload_images", ()) or ()))
        if len(existing) >= _MAX_PRODUCT_PHOTOS:
            answer = QMessageBox.question(
                self.window,
                "商品图片已满",
                f"当前已经选择 {_MAX_PRODUCT_PHOTOS} 张 Product Photos。是否清空后重新选择？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            existing = []

        initial_dir = str(existing[-1].parent) if existing else ""
        files, _selected_filter = QFileDialog.getOpenFileNames(
            self.window,
            "选择写入 Makro Product Photos 的图片",
            initial_dir,
            _LISTING_IMAGE_FILTER,
        )
        if not files:
            return
        combined = [*existing, *(Path(value).expanduser().resolve() for value in files)]
        normalized = _normalized_manual_images(combined)
        if len(combined) > len(normalized) and len(normalized) >= _MAX_PRODUCT_PHOTOS:
            QMessageBox.information(
                self.window,
                "Product Photos 数量限制",
                f"本次保留前 {_MAX_PRODUCT_PHOTOS} 张图片；可以再次打开入口重新选择。",
            )
        self.window._selected_upload_images = list(normalized)
        self._sync_single_photo_copy()

    def _sync_single_photo_copy(self) -> None:
        images = _normalized_manual_images(getattr(self.window, "_selected_upload_images", ()) or ())
        if self.single_photo_button is not None:
            self.single_photo_button.setText(f"商品图片 {len(images)}" if images else "商品图片…")
            self.single_photo_button.setToolTip(
                ("只用于 Makro Product Photos，不参与 AI / Resolver。\n" + "\n".join(str(p) for p in images))
                if images
                else "选择最终写入 Makro Product Photos 的图片；不会作为 AI 辅助资料。"
            )
        if images and hasattr(self.window, "real_image_count"):
            self.window.real_image_count.setText(f"MANUAL {len(images)}")
            self.window.real_image_count.setToolTip("\n".join(str(path) for path in images))

    def _single_url_edited(self, text: str) -> None:
        result = getattr(self.window, "current_result", None)
        if result is None:
            return
        previous = str(getattr(result, "product_url", "") or "").strip()
        current = str(text or "").strip()
        if previous and current and previous.casefold() != current.casefold():
            self.window._selected_upload_images = []
            self._sync_single_photo_copy()

    # --------------------------------------------------------------- Batch UI
    def _install_batch_surface(self) -> None:
        for row in list(self.editor.rows):
            self._decorate_batch_row(row)

        original_add_row = self.editor.add_row

        def add_row(_editor: Any, *args: Any, **kwargs: Any):
            row = original_add_row(*args, **kwargs)
            self._decorate_batch_row(row)
            return row

        self.editor.add_row = MethodType(add_row, self.editor)
        self.controller.jobs_changed.connect(lambda _jobs: self._sync_batch_photo_buttons())

    def _decorate_batch_row(self, row: Any) -> None:
        layout = row.layout()
        remove = getattr(row, "remove_button", None)
        if not isinstance(layout, QHBoxLayout) or not isinstance(remove, QPushButton):
            return
        if isinstance(getattr(row, "listing_photos_button", None), QPushButton):
            return

        row.listing_photo_files = tuple(getattr(row, "listing_photo_files", ()) or ())
        button = QPushButton("商品图片 0", row)
        button.setObjectName("batchListingPhotosButton")
        button.setFixedSize(86, 28)
        button.setToolTip(
            "只选择最终写入这一条商品 Makro Product Photos 的图片；不参与 AI / Resolver 商品理解。"
        )
        button.setStyleSheet(
            "QPushButton#batchListingPhotosButton {"
            "min-height:28px;max-height:28px;padding:0 8px;"
            "border:1px solid rgba(157,243,239,48);border-radius:8px;"
            "background:rgba(38,83,91,72);color:rgba(231,253,251,220);"
            "font-size:11px;font-weight:720;}"
            "QPushButton#batchListingPhotosButton:hover {"
            "border-color:rgba(157,243,239,112);background:rgba(43,112,118,104);}"
            "QPushButton#batchListingPhotosButton:disabled {"
            "border-color:rgba(255,255,255,14);background:rgba(20,28,38,42);"
            "color:rgba(225,237,247,74);}"
        )
        button.clicked.connect(lambda _checked=False, current=row: self._pick_batch_images(current))
        index = layout.indexOf(remove)
        layout.insertWidget(index if index >= 0 else layout.count(), button, 0, Qt.AlignmentFlag.AlignVCenter)
        row.listing_photos_button = button
        row.toggle.toggled.connect(lambda _checked, current=row: self._refresh_batch_row(current))
        self._refresh_batch_row(row)

    def _pick_batch_images(self, row: Any) -> None:
        if row not in self.editor.rows or not bool(row.is_enabled()):
            return
        existing = list(self._row_listing_images(row))
        if len(existing) >= _MAX_PRODUCT_PHOTOS:
            answer = QMessageBox.question(
                self.window,
                "商品图片已满",
                f"当前商品已经选择 {_MAX_PRODUCT_PHOTOS} 张 Product Photos。是否清空后重新选择？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            existing = []
        initial_dir = str(existing[-1].parent) if existing else ""
        files, _selected_filter = QFileDialog.getOpenFileNames(
            self.window,
            "选择这一条商品写入 Makro Product Photos 的图片",
            initial_dir,
            _LISTING_IMAGE_FILTER,
        )
        if not files:
            return
        combined = [*existing, *(Path(value).expanduser().resolve() for value in files)]
        row.listing_photo_files = _normalized_manual_images(combined)
        self._refresh_batch_row(row)
        job = self._job_for_row(row)
        if job is not None:
            self._set_job_images(job, self._row_listing_images(row))
            self.controller._persist_emit()

    @staticmethod
    def _row_listing_images(row: Any) -> tuple[Path, ...]:
        return _normalized_manual_images(getattr(row, "listing_photo_files", ()) or ())

    def _refresh_batch_row(self, row: Any) -> None:
        button = getattr(row, "listing_photos_button", None)
        if not isinstance(button, QPushButton):
            return
        images = self._row_listing_images(row)
        button.setText(f"商品图片 {len(images)}" if images else "商品图片…")
        button.setEnabled(bool(row.is_enabled()) and not self._row_executing(row))
        button.setToolTip(
            ("只用于 Makro Product Photos，不参与 AI / Resolver。\n" + "\n".join(str(p) for p in images))
            if images
            else "选择最终写入这一条商品 Makro Product Photos 的图片；辅助资料里的图片不会在这里自动出现。"
        )

    def _sync_batch_photo_buttons(self) -> None:
        for row in list(self.editor.rows):
            self._refresh_batch_row(row)

    def _row_executing(self, row: Any) -> bool:
        job = self._job_for_row(row)
        if job is None:
            return False
        job_id = str(job.job_id)
        return any(
            owned_job_id == job_id and stage == "execute"
            for _process, (owned_job_id, stage) in self.controller._processes.items()
        )

    def _job_for_row(self, row: Any) -> Any | None:
        job_id = str(getattr(row, "_individual_job_id", "") or "")
        if not job_id:
            return None
        try:
            return self.controller._job(job_id)
        except Exception:
            return None

    def _row_for_job(self, job: Any) -> Any | None:
        job_id = str(getattr(job, "job_id", "") or "")
        for row in list(self.editor.rows):
            if str(getattr(row, "_individual_job_id", "") or "") == job_id:
                return row
        return None

    # ---------------------------------------------------------- Batch ownership
    def _row_entries(self) -> list[tuple[str, tuple[Path, ...]]]:
        entries: list[tuple[str, tuple[Path, ...]]] = []
        for row in list(self.editor.rows):
            if not bool(row.is_enabled()):
                continue
            url = str(row.url() or "").strip()
            if not url:
                continue
            entries.append((url.casefold(), self._row_listing_images(row)))
        return entries

    def _match_rows_to_urls(self, urls: Iterable[str]) -> list[tuple[Path, ...] | None]:
        buckets: dict[str, deque[tuple[Path, ...]]] = defaultdict(deque)
        for key, images in self._row_entries():
            buckets[key].append(images)
        matched: list[tuple[Path, ...] | None] = []
        for value in urls:
            bucket = buckets.get(str(value).strip().casefold())
            matched.append(bucket.popleft() if bucket else None)
        return matched

    def _install_prepare_binding(self) -> None:
        original = self.controller.start_prepare

        def start_prepare(_controller: Any, urls: list[str], config: Any, **kwargs: Any):
            pending = self._match_rows_to_urls(urls)
            batch = original(urls, config, **kwargs)
            for index, job in enumerate(batch.jobs):
                images = pending[index] if index < len(pending) else None
                self._set_job_images(job, images or ())
            return batch

        self.controller.start_prepare = MethodType(start_prepare, self.controller)

    def _install_execute_refresh(self) -> None:
        original = self.controller.start_execution

        def start_execution(_controller: Any, *args: Any, **kwargs: Any):
            batch = _controller.batch
            if batch is not None:
                matched = self._match_rows_to_urls(job.product_url for job in batch.jobs)
                for index, job in enumerate(batch.jobs):
                    images = matched[index] if index < len(matched) else None
                    if images is not None:
                        self._set_job_images(job, images)
            return original(*args, **kwargs)

        self.controller.start_execution = MethodType(start_execution, self.controller)

    def _install_execution_override(self) -> None:
        original = self.controller._spawn

        def spawn(_controller: Any, job_id: str, stage: str, args: list[str]) -> None:
            argv = list(args)
            if stage == "execute" and bool(getattr(_controller, "_execution_images", False)):
                try:
                    job = _controller._job(job_id)
                    manual = self._job_images(job)
                    chosen = manual or _supplier_listing_images(Path(job.run_dir))
                except Exception:
                    manual = ()
                    chosen = ()
                argv = _replace_upload_image_args(argv, tuple(chosen))
                source = "MANUAL" if manual else "SUPPLIER_AUTO"
                _controller.log.emit(
                    f"[{job_id}] listing_photos={source} count={len(chosen)} "
                    "auxiliary_images=excluded"
                )
            original(job_id, stage, argv)

        self.controller._spawn = MethodType(spawn, self.controller)

    def _set_job_images(self, job: Any, images: tuple[Path, ...]) -> None:
        row = self._row_for_job(job)
        normalized = self._row_listing_images(row) if row is not None else _normalized_manual_images(images)
        self._manual_images_by_job_id[str(job.job_id)] = normalized
        self._write_sidecar(job, normalized)

    def _job_images(self, job: Any) -> tuple[Path, ...]:
        key = str(job.job_id)
        if key in self._manual_images_by_job_id:
            return self._manual_images_by_job_id[key]
        sidecar = Path(job.run_dir).parent / _MANUAL_PHOTO_SIDECAR
        if not sidecar.is_file():
            return ()
        try:
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:
            return ()
        if not isinstance(payload, dict):
            return ()
        return _normalized_manual_images(payload.get("manual_listing_images") or ())

    @staticmethod
    def _write_sidecar(job: Any, images: tuple[Path, ...]) -> None:
        root = Path(job.run_dir).parent
        root.mkdir(parents=True, exist_ok=True)
        target = root / _MANUAL_PHOTO_SIDECAR
        target.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "product_url": str(job.product_url),
                    "source": "user_selected_listing_photos",
                    "manual_listing_images": [str(path) for path in images],
                    "manual_precedence": bool(images),
                    "automatic_fallback": "supplier_listing_images_only",
                    "auxiliary_images_uploadable": False,
                    "max_product_photos": _MAX_PRODUCT_PHOTOS,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    # ------------------------------------------------------------ Auxiliary UI
    def _clarify_auxiliary_copy(self) -> None:
        files_ui = self.files_ui
        if files_ui is None:
            return
        detail = getattr(files_ui, "_detail", None)
        if detail is not None:
            title = detail.findChild(QLabel, "batchProductFilesTitle")
            subtitle = detail.findChild(QLabel, "batchProductFilesSubtitle")
            if title is not None:
                title.setText("辅助资料")
            if subtitle is not None:
                subtitle.setText("只给 AI / Resolver 看 · PDF / Word / 表格 / 图片 / ZIP")
                subtitle.setToolTip("辅助资料不会作为 Makro Product Photos 上传。")
            add_button = getattr(detail, "add_button", None)
            if isinstance(add_button, QPushButton):
                add_button.setText("＋ 选择辅助资料")
                add_button.setToolTip(
                    "可多选 PDF / Word / 表格 / 图片 / ZIP；全部只作为当前商品的 AI / Resolver 补充证据。"
                )

        original_refresh = getattr(files_ui, "_refresh_row", None)
        if callable(original_refresh):
            def refresh_row(_files_ui: Any, row: Any) -> None:
                original_refresh(row)
                button = getattr(row, "product_files_button", None)
                if not isinstance(button, QPushButton):
                    return
                files = tuple(_files_ui._row_files(row))
                opened = bool(getattr(_files_ui, "_active_row", None) is row and _files_ui._panel.isVisible())
                button.setText(f"辅助资料 {len(files)}  {'▴' if opened else '▾'}")
                button.setToolTip(
                    "只作为 AI / Resolver 商品证据，不会上传到 Makro Product Photos。"
                    + (("\n" + "\n".join(path.name for path in files[:8])) if files else "")
                )

            files_ui._refresh_row = MethodType(refresh_row, files_ui)
            for row in list(self.editor.rows):
                files_ui._refresh_row(row)


def install_listing_photo_ownership(window: Any) -> ListingPhotoOwnership:
    existing = getattr(window, "_listing_photo_ownership", None)
    if isinstance(existing, ListingPhotoOwnership):
        return existing
    layer = ListingPhotoOwnership(window)
    window._listing_photo_ownership = layer
    return layer


__all__ = [
    "ListingPhotoOwnership",
    "install_listing_photo_ownership",
]
