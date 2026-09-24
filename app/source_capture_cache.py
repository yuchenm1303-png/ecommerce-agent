from __future__ import annotations

import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .source_snapshot import SourceSnapshot, source_snapshot_from_json
from .supplier_url_identity import supplier_request_identity


@dataclass(slots=True, frozen=True)
class SourceCacheMaterialization:
    snapshot_path: Path
    screenshot_path: Path
    snapshot: SourceSnapshot
    product_image_paths: tuple[Path, ...]


@dataclass(slots=True, frozen=True)
class SourceCachePublishResult:
    published: bool
    detail: str = ""
    generation: str = ""


def _pointer_path(cache_dir: Path, cache_key: str) -> Path:
    return cache_dir / f"{cache_key}.current"


def _legacy_slot(cache_dir: Path, cache_key: str) -> Path:
    return cache_dir / cache_key


def _generation_dir(cache_dir: Path, cache_key: str) -> Path:
    generation = f"{time.time_ns():x}-{os.getpid():x}-{uuid.uuid4().hex[:12]}"
    return cache_dir / f"{cache_key}.{generation}"


def _safe_remove(path: Path) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    except OSError:
        pass


def _canonical_identity(value: str) -> str:
    return supplier_request_identity(str(value or "").strip())


def _resolve_cache_slot(cache_dir: Path, cache_key: str) -> Path | None:
    """Resolve one immutable cache generation, with legacy-slot compatibility.

    Readers only follow a fully-written pointer file. A generation directory is
    never published until its copy and source-snapshot validation are complete,
    so concurrent writers cannot expose a partially-copied cache tree.
    """

    pointer = _pointer_path(cache_dir, cache_key)
    try:
        generation_name = pointer.read_text(encoding="utf-8").strip()
        if (
            generation_name
            and Path(generation_name).name == generation_name
            and generation_name.startswith(f"{cache_key}.")
        ):
            generation = cache_dir / generation_name
            if generation.is_dir():
                return generation
    except (OSError, UnicodeError):
        pass

    legacy = _legacy_slot(cache_dir, cache_key)
    return legacy if legacy.is_dir() else None


def _cleanup_partial_materialization(output_dir: Path) -> None:
    for path in (
        output_dir / "source-snapshot.json",
        output_dir / "source-page.png",
        output_dir / "product-images",
    ):
        _safe_remove(path)


def read_source_capture_cache(
    source_url: str,
    *,
    cache_key: str,
    output_dir: Path,
    cache_dir: Path | None,
    cache_ttl_seconds: int,
) -> SourceCacheMaterialization | None:
    """Materialize a cache hit or return a clean miss; cache I/O is never fatal."""

    if cache_dir is None or cache_ttl_seconds <= 0:
        return None

    try:
        slot = _resolve_cache_slot(cache_dir, cache_key)
        if slot is None:
            return None
        snapshot = slot / "source-snapshot.json"
        screenshot = slot / "source-page.png"
        cached_images = slot / "product-images"
        if not snapshot.is_file():
            return None

        age = time.time() - snapshot.stat().st_mtime
        if age < 0 or age > cache_ttl_seconds:
            return None

        cached_snapshot = source_snapshot_from_json(snapshot)
        if _canonical_identity(cached_snapshot.requested_url) != _canonical_identity(source_url):
            return None

        cached_image_files = (
            tuple(sorted(path for path in cached_images.glob("*") if path.is_file()))
            if cached_images.is_dir()
            else ()
        )

        output_dir.mkdir(parents=True, exist_ok=True)
        output_snapshot = output_dir / "source-snapshot.json"
        output_screenshot = output_dir / "source-page.png"
        output_images = output_dir / "product-images"

        shutil.copy2(snapshot, output_snapshot)
        if output_screenshot.exists():
            output_screenshot.unlink()
        if screenshot.is_file():
            shutil.copy2(screenshot, output_screenshot)
        if output_images.exists():
            shutil.rmtree(output_images)
        if cached_image_files:
            shutil.copytree(cached_images, output_images)

        product_images = (
            tuple(sorted(path for path in output_images.glob("*") if path.is_file()))
            if output_images.is_dir()
            else ()
        )
        return SourceCacheMaterialization(
            snapshot_path=output_snapshot,
            screenshot_path=output_screenshot,
            snapshot=cached_snapshot,
            product_image_paths=product_images,
        )
    except Exception as exc:
        _cleanup_partial_materialization(output_dir)
        print(
            "SOURCE_CACHE READ_MISS "
            f"key={cache_key} error={type(exc).__name__}: {exc}",
            flush=True,
        )
        return None


def _replace_pointer_with_retry(source: Path, target: Path, *, attempts: int = 5) -> None:
    """Atomically publish a tiny pointer file, retrying transient Windows locks."""

    for attempt in range(max(1, attempts)):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(0.05 * (2**attempt))


def publish_source_capture_cache(
    source_url: str,
    *,
    cache_key: str,
    source_dir: Path,
    cache_dir: Path | None,
) -> SourceCachePublishResult:
    """Best-effort publish of an immutable source-capture cache generation.

    Cache is an optimization, never the source-of-truth result. Writers copy to a
    unique immutable generation and publish only a small pointer file. Concurrent
    writers therefore never delete/rename each other's directories, and a Windows
    sharing violation while publishing the pointer cannot turn an already-successful
    browser capture into a failed product job.
    """

    if cache_dir is None:
        return SourceCachePublishResult(False, "cache disabled")

    generation_dir: Path | None = None
    pointer_temp: Path | None = None
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        generation_dir = _generation_dir(cache_dir, cache_key)
        shutil.copytree(source_dir, generation_dir)

        snapshot_path = generation_dir / "source-snapshot.json"
        if not snapshot_path.is_file():
            raise RuntimeError("captured source directory has no source-snapshot.json")
        cached_snapshot = source_snapshot_from_json(snapshot_path)
        if _canonical_identity(cached_snapshot.requested_url) != _canonical_identity(source_url):
            raise RuntimeError("captured source identity does not match cache key owner")

        pointer = _pointer_path(cache_dir, cache_key)
        pointer_temp = cache_dir / f".{cache_key}.current.{uuid.uuid4().hex}.tmp"
        with pointer_temp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(generation_dir.name)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _replace_pointer_with_retry(pointer_temp, pointer)
        return SourceCachePublishResult(
            True,
            generation=generation_dir.name,
        )
    except Exception as exc:
        if pointer_temp is not None:
            _safe_remove(pointer_temp)
        if generation_dir is not None:
            _safe_remove(generation_dir)
        detail = f"{type(exc).__name__}: {exc}"
        print(
            f"SOURCE_CACHE WRITE_SKIPPED key={cache_key} error={detail}",
            flush=True,
        )
        return SourceCachePublishResult(False, detail)


__all__ = [
    "SourceCacheMaterialization",
    "SourceCachePublishResult",
    "publish_source_capture_cache",
    "read_source_capture_cache",
]
