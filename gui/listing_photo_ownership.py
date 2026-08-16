from __future__ import annotations

import json
from collections import defaultdict, deque
from pathlib import Path
from types import MethodType
from typing import Any, Iterable

from PySide6.QtWidgets import QLabel


_MANUAL_PHOTO_SIDECAR = "listing-photo-intent.json"
_LISTING_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_MAX_PRODUCT_PHOTOS = 5


def _normalized_manual_images(values: Iterable[object]) -> tuple[Path, ...]:
    """Return the exact user-selected listing photos, preserving selection order.

    Product material files remain evidence for Resolver. Only image files that are
    valid Product Photos inputs are promoted to the explicit listing-photo intent.
    No supplier image is ever mixed into a non-empty manual selection.
    """

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
    """Replace the executor photo list without touching any other execution arg."""

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


class ListingPhotoOwnership:
    """Own user-selected Product Photos per Batch Job.

    The existing Batch product-material picker serves two distinct purposes:
    documents/tables/images are Resolver evidence during prepare, while selected
    JPG/JPEG/PNG/WebP files are also an explicit Product Photos override for that
    exact Job. The override is persisted per Job and is applied only when Product
    Photos authorization is ON. If a Job has no manual photo intent, the existing
    Resolver listing-image fallback remains unchanged.
    """

    def __init__(self, window: Any) -> None:
        self.window = window
        self.workspace = window.batch_workspace
        self.controller = self.workspace.controller
        self.editor = getattr(self.workspace, "_batch_url_editor", None)
        self.files_ui = getattr(window, "_batch_product_files_ui", None)
        if self.editor is None or self.files_ui is None:
            raise RuntimeError("Listing photo ownership requires Batch URL editor and product files UI")

        self._manual_images_by_job_id: dict[str, tuple[Path, ...]] = {}
        self._install_prepare_binding()
        self._install_execute_refresh()
        self._install_execution_override()
        self._clarify_product_files_copy()

    def _row_entries(self) -> list[tuple[str, tuple[Path, ...]]]:
        entries: list[tuple[str, tuple[Path, ...]]] = []
        for row in list(self.editor.rows):
            if not bool(row.is_enabled()):
                continue
            url = str(row.url() or "").strip()
            if not url:
                continue
            files = getattr(row, "product_files", ()) or ()
            entries.append((url.casefold(), _normalized_manual_images(files)))
        return entries

    def _match_rows_to_urls(
        self,
        urls: Iterable[str],
    ) -> list[tuple[Path, ...] | None]:
        """Occurrence-aware matching keeps duplicate supplier URLs job-isolated."""

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

        def start_prepare(
            _controller: Any,
            urls: list[str],
            config: Any,
            **kwargs: Any,
        ):
            pending = self._match_rows_to_urls(urls)
            batch = original(urls, config, **kwargs)
            for index, job in enumerate(batch.jobs):
                images = pending[index] if index < len(pending) else None
                if images is None:
                    images = ()
                self._set_job_images(job, images)
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
                except Exception:
                    manual = ()
                if manual:
                    argv = _replace_upload_image_args(argv, manual)
                    _controller.log.emit(
                        f"[{job_id}] listing_photos=MANUAL count={len(manual)} "
                        "supplier_fallback=disabled"
                    )
            original(job_id, stage, argv)

        self.controller._spawn = MethodType(spawn, self.controller)

    def _set_job_images(self, job: Any, images: tuple[Path, ...]) -> None:
        normalized = _normalized_manual_images(images)
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
                    "schema_version": 1,
                    "product_url": str(job.product_url),
                    "source": "user_selected_batch_product_files",
                    "manual_listing_images": [str(path) for path in images],
                    "manual_precedence": bool(images),
                    "fallback_when_empty": "resolver_primary_source_listing_images",
                    "max_product_photos": _MAX_PRODUCT_PHOTOS,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _clarify_product_files_copy(self) -> None:
        detail = getattr(self.files_ui, "_detail", None)
        if detail is None:
            return
        subtitle = detail.findChild(QLabel, "batchProductFilesSubtitle")
        if subtitle is not None:
            subtitle.setText("图片 = 当前商品手动 Product Photos · 文档/表格 = AI 补充资料")
            subtitle.setToolTip(
                "当底部“上传本次商品图”开启时，本行手动添加的 JPG/JPEG/PNG/WebP "
                "优先于供应商链接抓图；不会与其他 Job 混用。"
            )
        add_button = getattr(detail, "add_button", None)
        if add_button is not None:
            add_button.setToolTip(
                "可多选资料。JPG/JPEG/PNG/WebP 同时作为当前 Job 的手动 Product Photos；"
                "PDF/Word/表格/ZIP 只作为 Resolver 补充证据。"
            )


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
