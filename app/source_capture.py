from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError, sync_playwright

from .browser_session import cdp_endpoint, is_cdp_ready, launch_detached_edge
from .source_snapshot import (
    SourceAccessBlocked,
    SourceSnapshot,
    capture_page_snapshot,
    source_snapshot_from_json,
    write_source_snapshot,
)


DEFAULT_SOURCE_CDP_PORT = 9333
SOURCE_CAPTURE_CACHE_VERSION = 4

_DETAIL_DOCUMENT_PATTERN = re.compile(
    r"detail(?:Url|_url)[^h]{0,48}(https?://[^\\\"'<>\s]+)",
    re.IGNORECASE,
)
_IMAGE_URL_PATTERN = re.compile(
    r"(?:(?:https?:)?\\?/\\?/)[^\\\"'<>\s]+?\.(?:jpe?g|png|webp|gif|avif)(?:\?[^\\\"'<>\s]*)?",
    re.IGNORECASE,
)
_NO_EVALUATE_ARG = object()


@dataclass(slots=True, frozen=True)
class CapturedProductSource:
    snapshot_path: Path
    screenshot_path: Path
    snapshot: SourceSnapshot
    launched_now: bool
    product_image_paths: tuple[Path, ...] = ()
    cache_hit: bool = False


def validate_source_url(value: str) -> str:
    url = value.strip()
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("product URL 必须是完整 http/https URL。")
    return url


def _canonical_source_url(value: str) -> str:
    parsed = urlsplit(validate_source_url(value))
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}"


def _source_cache_key(value: str) -> str:
    payload = f"v{SOURCE_CAPTURE_CACHE_VERSION}|{_canonical_source_url(value)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _unescape_embedded(value: str) -> str:
    return html.unescape(value).replace(r"\/", "/")


def _detail_document_urls(snapshot: SourceSnapshot, *, max_urls: int = 4) -> list[str]:
    """Extract bounded, exact-page detail documents exposed by embedded page data."""

    output: list[str] = []
    seen: set[str] = set()
    for raw in snapshot.embedded_data:
        text = _unescape_embedded(raw)
        for match in _DETAIL_DOCUMENT_PATTERN.finditer(text):
            url = match.group(1).rstrip("\\")
            try:
                url = validate_source_url(url)
            except ValueError:
                continue
            if url in seen:
                continue
            seen.add(url)
            output.append(url)
            if len(output) >= max_urls:
                return output
    return output


def _detail_image_urls_from_text(value: str, *, max_urls: int = 32) -> list[str]:
    """Extract image assets from one supplier detail document without interpreting them."""

    text = _unescape_embedded(value)
    output: list[str] = []
    seen: set[str] = set()
    for match in _IMAGE_URL_PATTERN.finditer(text):
        url = match.group(0).replace(r"\/", "/")
        if url.startswith("//"):
            url = "https:" + url
        try:
            url = validate_source_url(url)
        except ValueError:
            continue
        if url in seen:
            continue
        seen.add(url)
        output.append(url)
        if len(output) >= max_urls:
            break
    return output


def _discover_detail_images(
    context,
    snapshot: SourceSnapshot,
    *,
    max_documents: int = 4,
    max_images: int = 32,
) -> tuple[list[str], list[str]]:
    """Fetch supplier-declared detail documents and return their exact image URLs."""

    documents = _detail_document_urls(snapshot, max_urls=max_documents)
    images: list[str] = []
    seen: set[str] = set()
    for document_url in documents:
        response = None
        try:
            response = context.request.get(document_url, timeout=15_000, fail_on_status_code=False)
            if not response.ok:
                continue
            body = response.text()
            for image_url in _detail_image_urls_from_text(body, max_urls=max_images):
                if image_url in seen:
                    continue
                seen.add(image_url)
                images.append(image_url)
                if len(images) >= max_images:
                    return documents, images
        except Exception:
            continue
        finally:
            if response is not None:
                try:
                    response.dispose()
                except Exception:
                    pass
    return documents, images


def _connect_source_edge(playwright, *, profile_dir: Path, port: int, start_url: str):
    launched_now = not is_cdp_ready(port)
    if launched_now:
        launch_detached_edge(profile_dir=profile_dir, port=port, start_url=start_url)
    browser = playwright.chromium.connect_over_cdp(cdp_endpoint(port))
    contexts = list(browser.contexts)
    if not contexts:
        raise RuntimeError("已连接 source Edge，但没有 browser context。")
    context = contexts[0]
    pages = list(context.pages)
    page = pages[-1] if pages else context.new_page()
    return browser, context, page, launched_now


def _is_navigation_context_error(exc: BaseException) -> bool:
    text = str(exc).casefold()
    return "execution context was destroyed" in text and "navigation" in text


def _is_timeout_error(exc: BaseException) -> bool:
    text = str(exc).casefold()
    return "timeout" in text and "exceeded" in text


def _wait_for_navigation_recovery(page, *, settle_ms: int) -> None:
    """Wait for a replacement execution context after a supplier-side navigation.

    1688 may perform a canonical/client redirect after DOMContentLoaded. That is
    normal page behavior and must not abort a source capture. Only the specific
    transient execution-context error is retried; unrelated Playwright failures
    still fail closed.
    """

    try:
        page.wait_for_load_state("domcontentloaded", timeout=15_000)
    except PlaywrightError:
        # A second client-side transition may already be in flight. The short
        # settle delay below gives the new main-frame context time to attach.
        pass
    page.wait_for_timeout(max(250, min(1_500, int(settle_ms) or 250)))


def _evaluate_with_navigation_retry(
    page,
    expression: str,
    arg=_NO_EVALUATE_ARG,
    *,
    settle_ms: int,
    attempts: int = 4,
):
    for attempt in range(max(1, attempts)):
        try:
            if arg is _NO_EVALUATE_ARG:
                return page.evaluate(expression)
            return page.evaluate(expression, arg)
        except PlaywrightError as exc:
            if not _is_navigation_context_error(exc) or attempt + 1 >= attempts:
                raise
            _wait_for_navigation_recovery(page, settle_ms=settle_ms)
    raise RuntimeError("unreachable navigation retry state")


def _load_lazy_page(page, *, initial_wait_ms: int, scroll_wait_ms: int, max_scroll_steps: int) -> None:
    if initial_wait_ms:
        page.wait_for_timeout(initial_wait_ms)
    stable_rounds = 0
    previous_height = 0
    for _ in range(max_scroll_steps):
        state = _evaluate_with_navigation_retry(
            page,
            """() => ({
                y: window.scrollY,
                h: Math.max(document.body?.scrollHeight || 0, document.documentElement?.scrollHeight || 0),
                vh: window.innerHeight || 800
            })""",
            settle_ms=scroll_wait_ms,
        )
        height = int(state.get("h") or 0)
        y = int(state.get("y") or 0)
        viewport = max(400, int(state.get("vh") or 800))
        if height <= 0:
            break
        if y + viewport >= height - 8:
            stable_rounds = stable_rounds + 1 if height == previous_height else 0
            if stable_rounds >= 2:
                break
        _evaluate_with_navigation_retry(
            page,
            "(step) => window.scrollBy(0, step)",
            max(300, int(viewport * 0.85)),
            settle_ms=scroll_wait_ms,
        )
        if scroll_wait_ms:
            page.wait_for_timeout(scroll_wait_ms)
        previous_height = height
    _evaluate_with_navigation_retry(
        page,
        "() => window.scrollTo(0, 0)",
        settle_ms=scroll_wait_ms,
    )
    if scroll_wait_ms:
        page.wait_for_timeout(scroll_wait_ms)


def _capture_snapshot_with_navigation_retry(
    page,
    *,
    requested_url: str,
    max_visible_text_chars: int,
    settle_ms: int,
    attempts: int = 4,
) -> SourceSnapshot:
    for attempt in range(max(1, attempts)):
        try:
            return capture_page_snapshot(
                page,
                requested_url=requested_url,
                max_visible_text_chars=max_visible_text_chars,
            )
        except PlaywrightError as exc:
            if not _is_navigation_context_error(exc) or attempt + 1 >= attempts:
                raise
            _wait_for_navigation_recovery(page, settle_ms=settle_ms)
    raise RuntimeError("unreachable snapshot retry state")


def _screenshot_with_navigation_retry(
    page,
    path: Path,
    *,
    settle_ms: int,
    attempts: int = 4,
) -> None:
    """Capture a useful screenshot without making full-page rendering a hard gate.

    Very long/dynamic supplier pages can exceed Playwright's screenshot timeout
    even after the source snapshot is already valid. Retry only navigation races;
    on a plain full-page timeout, immediately fall back to the current viewport.
    """

    for attempt in range(max(1, attempts)):
        try:
            page.screenshot(path=str(path), full_page=True)
            return
        except PlaywrightError as exc:
            if _is_navigation_context_error(exc) and attempt + 1 < attempts:
                _wait_for_navigation_recovery(page, settle_ms=settle_ms)
                continue
            if not _is_timeout_error(exc):
                raise
            break

    for attempt in range(max(1, attempts)):
        try:
            page.screenshot(path=str(path), full_page=False)
            return
        except PlaywrightError as exc:
            if not _is_navigation_context_error(exc) or attempt + 1 >= attempts:
                raise
            _wait_for_navigation_recovery(page, settle_ms=settle_ms)


def _image_extension(content_type: str, url: str) -> str:
    mime = content_type.split(";", 1)[0].strip().casefold()
    by_mime = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/avif": ".avif",
    }
    if mime in by_mime:
        return by_mime[mime]
    suffix = Path(urlsplit(url).path).suffix.lower()
    return suffix if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"} else ".img"


def _download_page_images(context, image_urls: list[str], output_dir: Path, *, max_images: int = 32) -> tuple[Path, ...]:
    """Download large images already exposed by the exact page; no image semantics here."""

    if not image_urls:
        return ()
    output_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    seen_hashes: set[str] = set()

    for url in image_urls:
        if len(saved) >= max_images:
            break
        response = None
        try:
            response = context.request.get(url, timeout=15_000, fail_on_status_code=False)
            if not response.ok:
                continue
            headers = {str(k).casefold(): str(v) for k, v in response.headers.items()}
            content_type = headers.get("content-type", "")
            if content_type and not content_type.casefold().startswith("image/"):
                continue
            body = response.body()
            if len(body) < 4_096:
                continue
            digest = hashlib.sha256(body).hexdigest()
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)
            ext = _image_extension(content_type, url)
            path = output_dir / f"source-image-{len(saved) + 1:02d}-{digest[:10]}{ext}"
            path.write_bytes(body)
            saved.append(path)
        except Exception:
            # Image downloads are supplemental. The snapshot + screenshot remain
            # valid evidence even when a CDN refuses an individual request.
            continue
        finally:
            if response is not None:
                try:
                    response.dispose()
                except Exception:
                    pass
    return tuple(saved)


def _cached_capture(
    source_url: str,
    *,
    output_dir: Path,
    cache_dir: Path | None,
    cache_ttl_seconds: int,
) -> CapturedProductSource | None:
    if cache_dir is None or cache_ttl_seconds <= 0:
        return None
    slot = cache_dir / _source_cache_key(source_url)
    snapshot = slot / "source-snapshot.json"
    screenshot = slot / "source-page.png"
    if not snapshot.is_file() or not screenshot.is_file():
        return None
    age = time.time() - snapshot.stat().st_mtime
    if age < 0 or age > cache_ttl_seconds:
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(snapshot, output_dir / snapshot.name)
    shutil.copy2(screenshot, output_dir / screenshot.name)
    cached_images = slot / "product-images"
    if cached_images.is_dir():
        shutil.copytree(cached_images, output_dir / "product-images", dirs_exist_ok=True)

    output_snapshot = output_dir / "source-snapshot.json"
    output_screenshot = output_dir / "source-page.png"
    product_images = tuple(sorted((output_dir / "product-images").glob("*") if (output_dir / "product-images").is_dir() else []))
    return CapturedProductSource(
        snapshot_path=output_snapshot,
        screenshot_path=output_screenshot,
        snapshot=source_snapshot_from_json(output_snapshot),
        launched_now=False,
        product_image_paths=product_images,
        cache_hit=True,
    )


def _refresh_capture_cache(source_url: str, source_dir: Path, cache_dir: Path | None) -> None:
    if cache_dir is None:
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    slot = cache_dir / _source_cache_key(source_url)
    temp = cache_dir / f".{slot.name}.tmp"
    if temp.exists():
        shutil.rmtree(temp)
    shutil.copytree(source_dir, temp)
    if slot.exists():
        shutil.rmtree(slot)
    temp.replace(slot)


def capture_product_source(
    url: str,
    *,
    output_dir: str | Path,
    profile_dir: str | Path = "browser_profiles/source-edge",
    cdp_port: int = DEFAULT_SOURCE_CDP_PORT,
    initial_wait_ms: int = 1800,
    scroll_wait_ms: int = 180,
    max_scroll_steps: int = 120,
    max_visible_text_chars: int = 120_000,
    use_current_page: bool = False,
    cache_dir: str | Path | None = None,
    cache_ttl_seconds: int = 900,
    force_refresh: bool = False,
) -> CapturedProductSource:
    """Capture one exact supplier page and its automatically discovered large images.

    A short-lived byte cache makes immediate hot reruns use the exact same source
    universe, so semantic caches can be meaningfully tested. It is transport
    caching only, not a product-fact layer. `force_refresh` or `use_current_page`
    bypasses reuse and performs a fresh capture.
    """

    source_url = validate_source_url(url)
    if initial_wait_ms < 0 or scroll_wait_ms < 0:
        raise ValueError("source wait 参数不能为负数。")
    if max_scroll_steps < 1:
        raise ValueError("max_scroll_steps 必须 >= 1。")
    if max_visible_text_chars < 1_000:
        raise ValueError("max_visible_text_chars 不能小于 1000。")
    if cache_ttl_seconds < 0:
        raise ValueError("cache_ttl_seconds 不能为负数。")

    target_dir = Path(output_dir)
    cache_root = Path(cache_dir) if cache_dir is not None else None
    if not force_refresh and not use_current_page:
        cached = _cached_capture(
            source_url,
            output_dir=target_dir,
            cache_dir=cache_root,
            cache_ttl_seconds=int(cache_ttl_seconds),
        )
        if cached is not None:
            return cached

    target_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        _, context, page, launched_now = _connect_source_edge(
            playwright,
            profile_dir=Path(profile_dir).resolve(),
            port=int(cdp_port),
            start_url=source_url,
        )
        page.set_default_timeout(15_000)
        if use_current_page:
            if page.url in {"", "about:blank"}:
                raise RuntimeError("--source-use-current-page 时 source Edge 没有已打开网页。")
        else:
            page.goto(source_url, wait_until="domcontentloaded", timeout=45_000)

        _load_lazy_page(
            page,
            initial_wait_ms=int(initial_wait_ms),
            scroll_wait_ms=int(scroll_wait_ms),
            max_scroll_steps=int(max_scroll_steps),
        )
        snapshot = _capture_snapshot_with_navigation_retry(
            page,
            requested_url=source_url,
            max_visible_text_chars=int(max_visible_text_chars),
            settle_ms=int(scroll_wait_ms),
        )
        detail_documents, detail_images = _discover_detail_images(context, snapshot)
        combined_images: list[str] = []
        seen_images: set[str] = set()
        # Detail-document images are the dense specification/evidence set. Keep
        # them first so the semantic stage can avoid resending duplicate gallery
        # thumbnails and the giant rendered-page screenshot.
        for image_url in [*detail_images, *snapshot.image_urls]:
            if image_url and image_url not in seen_images:
                seen_images.add(image_url)
                combined_images.append(image_url)
        snapshot.image_urls = combined_images
        if detail_documents:
            snapshot.meta["detail_document_urls"] = json.dumps(
                detail_documents,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            snapshot.meta["detail_image_count"] = str(len(detail_images))
        snapshot_path = write_source_snapshot(snapshot, target_dir / "source-snapshot.json")
        screenshot_path = target_dir / "source-page.png"
        _screenshot_with_navigation_retry(
            page,
            screenshot_path,
            settle_ms=int(scroll_wait_ms),
        )
        product_images = _download_page_images(
            context,
            snapshot.image_urls,
            target_dir / "product-images",
        )

    _refresh_capture_cache(source_url, target_dir, cache_root)
    return CapturedProductSource(
        snapshot_path=snapshot_path,
        screenshot_path=screenshot_path,
        snapshot=snapshot,
        launched_now=launched_now,
        product_image_paths=product_images,
        cache_hit=False,
    )


__all__ = [
    "CapturedProductSource",
    "DEFAULT_SOURCE_CDP_PORT",
    "SourceAccessBlocked",
    "SOURCE_CAPTURE_CACHE_VERSION",
    "_detail_document_urls",
    "_detail_image_urls_from_text",
    "_source_cache_key",
    "capture_product_source",
    "validate_source_url",
]
