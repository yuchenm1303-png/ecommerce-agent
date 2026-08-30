from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import Error as PlaywrightError, sync_playwright

from .browser_session import (
    _connect_browser_resilient,
    acquire_cdp_session_lease,
    is_cdp_ready,
    launch_detached_edge,
)
from .browser_visual_hud import (
    arm_browser_visual_hud,
    browser_visual_hud_status,
    finish_browser_visual_hud,
    set_browser_visual_hud_capture_safe,
)
from .image_media import ImageMediaError, normalize_image_media
from .listing_images import select_listing_images
from .public_resource_fetch import fetch_public_resource
from .source_snapshot import (
    SourceAccessBlocked,
    SourceCaptureError,
    SourceSnapshot,
    capture_page_snapshot,
    source_snapshot_from_json,
    write_source_snapshot,
)
from .supplier_url_identity import supplier_request_identity


DEFAULT_SOURCE_CDP_PORT = 9333
SOURCE_CAPTURE_CACHE_VERSION = 8
_DETAIL_DOCUMENT_MAX_BYTES = 8 * 1024 * 1024
_PRODUCT_IMAGE_MAX_BYTES = 64 * 1024 * 1024
_PRODUCT_IMAGES_TOTAL_MAX_BYTES = 256 * 1024 * 1024

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
    """Validate user-supplied primary navigation syntax only.

    Primary supplier navigation remains intentionally provider/domain agnostic.
    The stricter public-network policy applies only to automatic secondary
    resources discovered by the page itself, not to the user's explicit URL.
    """

    url = value.strip()
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("product URL 必须是完整 http/https URL。")
    return url


def _canonical_source_url(value: str) -> str:
    return supplier_request_identity(validate_source_url(value))


def _source_cache_key(value: str) -> str:
    payload = f"v{SOURCE_CAPTURE_CACHE_VERSION}|{_canonical_source_url(value)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _unescape_embedded(value: str) -> str:
    return html.unescape(value).replace(r"\/", "/")


def _detail_document_urls(snapshot: SourceSnapshot, *, max_urls: int = 4) -> list[str]:
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


def _normalize_discovered_image_url(value: object, *, base_url: str) -> str:
    raw = html.unescape(str(value or "")).strip().replace(r"\/", "/")
    if not raw or raw.startswith(("data:", "blob:")):
        return ""
    try:
        absolute = urljoin(base_url, raw)
        return validate_source_url(absolute)
    except (TypeError, ValueError):
        return ""


def _unique_image_urls(values: list[str], *, max_images: int = 32) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        output.append(value)
        if len(output) >= max_images:
            break
    return output


def _structured_product_image_urls(snapshot: SourceSnapshot, *, max_images: int = 24) -> list[str]:
    """Extract only images explicitly owned by JSON-LD Product objects."""

    base_url = snapshot.final_url or snapshot.requested_url
    output: list[str] = []
    seen: set[str] = set()

    def push(raw: object) -> None:
        value = _normalize_discovered_image_url(raw, base_url=base_url)
        if not value or value in seen or len(output) >= max_images:
            return
        seen.add(value)
        output.append(value)

    def collect_image_value(value: object) -> None:
        if len(output) >= max_images:
            return
        if isinstance(value, str):
            push(value)
            return
        if isinstance(value, list):
            for item in value:
                collect_image_value(item)
            return
        if not isinstance(value, dict):
            return
        for key in ("contentUrl", "url", "thumbnailUrl", "src"):
            if key in value:
                collect_image_value(value.get(key))

    def walk(value: object) -> None:
        if len(output) >= max_images:
            return
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if not isinstance(value, dict):
            return

        raw_types = value.get("@type")
        types = raw_types if isinstance(raw_types, list) else [raw_types]
        is_product = any(
            "product" in str(item or "").casefold()
            for item in types
        )
        if is_product:
            for key in ("image", "images"):
                if key in value:
                    collect_image_value(value.get(key))

        for key in ("@graph", "itemListElement", "mainEntity", "mainEntityOfPage"):
            if key in value:
                walk(value.get(key))

    walk(snapshot.json_ld)
    return output


def _context_cookies(context, url: str) -> tuple[dict[str, object], ...]:
    try:
        raw = context.cookies(url)
    except Exception:
        return ()
    return tuple(item for item in raw if isinstance(item, dict))


def _discover_detail_images(
    context,
    snapshot: SourceSnapshot,
    *,
    max_documents: int = 4,
    max_images: int = 32,
) -> tuple[list[str], list[str]]:
    """Read bounded public detail documents exposed by the exact supplier page.

    Detail-document images are deliberately a fallback source. They are useful on
    marketplaces that keep high-resolution media in a detail payload, but the
    document may also contain recommendation and decoration assets and therefore
    must never outrank explicit Product/gallery ownership.
    """

    documents = _detail_document_urls(snapshot, max_urls=max_documents)
    images: list[str] = []
    seen: set[str] = set()
    referer = snapshot.final_url or snapshot.requested_url
    for document_url in documents:
        try:
            response = fetch_public_resource(
                document_url,
                max_bytes=_DETAIL_DOCUMENT_MAX_BYTES,
                timeout_seconds=15.0,
                cookies=_context_cookies(context, document_url),
                referer=referer,
            )
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
    return documents, images


def _connect_source_edge(playwright, *, profile_dir: Path, port: int, start_url: str):
    session_lease = acquire_cdp_session_lease(port)
    try:
        launched_now = not is_cdp_ready(port)
        if launched_now:
            launch_detached_edge(profile_dir=profile_dir, port=port, start_url=start_url)
        browser = _connect_browser_resilient(playwright, port)
        contexts = list(browser.contexts)
        if not contexts:
            raise RuntimeError("已连接 source Edge，但没有 browser context。")
        context = contexts[0]
        pages = list(context.pages)
        page = pages[-1] if pages else context.new_page()
        arm_browser_visual_hud(
            page,
            title="正在读取商品页面",
            thought="Listing Studio 正在连接供应商商品页并准备采集页面证据。",
            phase=0,
        )
        return browser, context, page, launched_now, session_lease
    except Exception:
        session_lease.release()
        raise


def _is_navigation_context_error(exc: BaseException) -> bool:
    text = str(exc).casefold()
    return "execution context was destroyed" in text and "navigation" in text


def _wait_for_navigation_recovery(page, *, settle_ms: int) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15_000)
    except PlaywrightError:
        pass
    page.wait_for_timeout(max(250, min(1_500, int(settle_ms) or 250)))
    browser_visual_hud_status(
        page,
        "正在跟随商品页跳转",
        "供应商页面发生正常跳转，正在重新绑定当前页面并继续采集。",
        phase=1,
    )


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
    browser_visual_hud_status(
        page,
        "正在展开商品页面",
        "正在滚动页面，加载懒加载商品文字、规格和图片。",
        phase=2,
    )
    if initial_wait_ms:
        page.wait_for_timeout(initial_wait_ms)
    stable_rounds = 0
    previous_height = 0
    for step_index in range(max_scroll_steps):
        if step_index and step_index % 12 == 0:
            browser_visual_hud_status(
                page,
                "正在扫描商品页面",
                f"已继续滚动加载页面内容 · pass {step_index + 1}",
                phase=2,
            )
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
    browser_visual_hud_status(
        page,
        "商品页面已展开",
        "可见页面内容已经完成滚动加载，准备提取结构化商品信息。",
        phase=2,
    )


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


def _discover_product_gallery_images(
    page,
    snapshot: SourceSnapshot,
    *,
    settle_ms: int,
    max_images: int = 24,
) -> list[str]:
    """Discover the complete current-product media gallery without requiring manual clicks.

    Explicit gallery ownership is stronger evidence than current visibility or thumbnail
    dimensions. Static DOM references are harvested first, then bounded non-navigating
    thumbnail controls are activated only to hydrate high-resolution media that the page
    loads lazily after selection. No product meaning is inferred here; downstream image
    quality and semantic ranking remain the final upload gates.
    """

    raw = _evaluate_with_navigation_retry(
        page,
        r"""async () => {
          const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
          const visible = (el) => {
            const style = getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return style.display !== 'none' && style.visibility !== 'hidden'
              && rect.width > 0 && rect.height > 0;
          };
          const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
          const positive = /(gallery|carousel|swiper|product[-_\s]*(image|media)|pdp[-_\s]*(image|media)|image[-_\s]*gallery|media[-_\s]*gallery|thumbnail|thumbs)/i;
          const negative = /(recommend|related|similar|sponsor|banner|logo|header|footer|navigation|\bnav\b|advert|promo|cross[-_\s]*sell|recently)/i;
          const sourceAttr = /(src|image|media|zoom|large|full|original|hero)/i;
          const titleTokens = new Set(
            clean(document.querySelector('h1')?.innerText || document.title)
              .toLowerCase().split(/[^a-z0-9]+/).filter((token) => token.length >= 4)
          );
          const output = [];
          const seen = new Set();
          const controls = [];
          const controlSeen = new Set();

          const push = (raw) => {
            let value = clean(raw).replace(/\\\//g, '/');
            if (!value || value.startsWith('data:') || value.startsWith('blob:')) return;
            const embedded = value.match(/https?:\/\/[^\s"'<>\\]+/g);
            if (embedded && embedded.length && embedded[0] !== value) {
              for (const item of embedded) push(item);
              return;
            }
            try {
              const absolute = new URL(value, document.baseURI).href;
              if (!/^https?:/i.test(absolute) || seen.has(absolute)) return;
              seen.add(absolute);
              output.push(absolute);
            } catch (_) {}
          };

          const pushSrcset = (srcset) => {
            const candidates = clean(srcset)
              .split(',')
              .map((part) => clean(part).split(/\s+/)[0])
              .filter(Boolean);
            for (let index = candidates.length - 1; index >= 0; index -= 1) {
              push(candidates[index]);
            }
          };

          const pushElementSources = (element) => {
            if (!element) return;
            push(element.currentSrc);
            for (const attr of [...(element.attributes || [])]) {
              if (!sourceAttr.test(attr.name)) continue;
              push(attr.value);
            }
            pushSrcset(element.getAttribute?.('srcset'));
            const picture = element.closest?.('picture');
            if (picture) {
              for (const source of picture.querySelectorAll('source[srcset]')) {
                pushSrcset(source.getAttribute('srcset'));
              }
            }
          };

          const contextFor = (img) => {
            const parts = [];
            let node = img;
            for (let depth = 0; node && depth < 7; depth += 1, node = node.parentElement) {
              parts.push(clean([
                node.id,
                node.className,
                node.getAttribute?.('role'),
                node.getAttribute?.('aria-label'),
                node.getAttribute?.('data-testid'),
                node.getAttribute?.('data-component'),
              ].filter(Boolean).join(' ')));
            }
            return parts.join(' ');
          };

          const pushOwnedSources = (img) => {
            pushElementSources(img);
            let node = img.parentElement;
            for (let depth = 0; node && depth < 5; depth += 1, node = node.parentElement) {
              if (node.tagName === 'A') push(node.getAttribute('href'));
              for (const attr of [...(node.attributes || [])]) {
                if (!sourceAttr.test(attr.name)) continue;
                push(attr.value);
              }
              for (const source of node.querySelectorAll?.(':scope > picture source[srcset], :scope > source[srcset]') || []) {
                pushSrcset(source.getAttribute('srcset'));
              }
            }
          };

          const rememberControl = (img) => {
            const control = img.closest('button,[role="button"],[role="option"],[aria-selected],li');
            if (!control || controlSeen.has(control)) return;
            if (control.tagName === 'A' || control.getAttribute('href')) return;
            controlSeen.add(control);
            controls.push(control);
          };

          const collect = () => {
            for (const img of document.images) {
              const contextText = contextFor(img);
              if (negative.test(contextText)) continue;
              const galleryOwned = positive.test(contextText);
              if (galleryOwned) {
                pushOwnedSources(img);
                rememberControl(img);
                continue;
              }

              if (!visible(img)) continue;
              const nw = Number(img.naturalWidth || 0);
              const nh = Number(img.naturalHeight || 0);
              if (Math.max(nw, nh) < 160) continue;

              const rect = img.getBoundingClientRect();
              const altTokens = new Set(
                clean(img.alt || img.title).toLowerCase().split(/[^a-z0-9]+/).filter((token) => token.length >= 4)
              );
              let titleMatches = 0;
              for (const token of altTokens) if (titleTokens.has(token)) titleMatches += 1;

              let score = 0;
              if (titleMatches >= 2) score += 3;
              else if (titleMatches === 1) score += 1;
              if (rect.width >= 180 && rect.height >= 180) score += 2;
              if (nw * nh >= 80000) score += 1;
              if (img.closest('main')) score += 1;
              if (score >= 6) pushElementSources(img);
            }
          };

          collect();
          const initiallySelected = controls.find((control) => {
            const selected = clean(control.getAttribute('aria-selected')).toLowerCase();
            const current = clean(control.getAttribute('aria-current')).toLowerCase();
            const classes = clean(control.className);
            return selected === 'true' || current === 'true' || /(^|\s)(active|selected)(\s|$)/i.test(classes);
          }) || null;

          for (const control of controls.slice(0, 12)) {
            if (!document.contains(control)) continue;
            try {
              control.click();
              await wait(120);
              collect();
            } catch (_) {}
          }

          if (initiallySelected && document.contains(initiallySelected)) {
            try {
              initiallySelected.click();
              await wait(80);
              collect();
            } catch (_) {}
          }

          return output.slice(0, 48);
        }""",
        settle_ms=settle_ms,
    )
    base_url = snapshot.final_url or snapshot.requested_url
    normalized = [
        _normalize_discovered_image_url(item, base_url=base_url)
        for item in (raw if isinstance(raw, list) else [])
    ]
    return _unique_image_urls([item for item in normalized if item], max_images=max_images)


def _compact_playwright_error(exc: BaseException) -> str:
    return re.sub(r"\s+", " ", str(exc or "")).strip()[:500]


def _screenshot_with_navigation_retry(
    page,
    path: Path,
    *,
    settle_ms: int,
    attempts: int = 4,
) -> tuple[bool, str]:
    """Capture optional screenshot evidence without changing source outcome."""

    failures: list[str] = []
    for full_page, mode in ((True, "full-page"), (False, "viewport")):
        for attempt in range(max(1, attempts)):
            try:
                page.screenshot(path=str(path), full_page=full_page)
                if failures:
                    return True, f"{failures[-1]}; {mode} fallback succeeded"
                return True, ""
            except PlaywrightError as exc:
                summary = _compact_playwright_error(exc)
                if _is_navigation_context_error(exc) and attempt + 1 < attempts:
                    _wait_for_navigation_recovery(page, settle_ms=settle_ms)
                    continue
                failures.append(f"{mode} screenshot failed: {summary}")
                break
    return False, " | ".join(failures)


def _download_page_images(
    context,
    image_urls: list[str],
    output_dir: Path,
    *,
    max_images: int = 32,
) -> tuple[Path, ...]:
    """Download only technically decodable raster images and normalize to JPEG.

    Response headers and URL suffixes are advisory at best. The image decoder is
    the transport authority, so HTML/error payloads and unsupported pseudo-images
    never enter product evidence or reach the multimodal provider.
    """

    if not image_urls:
        return ()
    output_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    seen_hashes: set[str] = set()
    total_source_bytes = 0
    for url in image_urls:
        if len(saved) >= max_images or total_source_bytes >= _PRODUCT_IMAGES_TOTAL_MAX_BYTES:
            break
        try:
            response = fetch_public_resource(
                url,
                max_bytes=_PRODUCT_IMAGE_MAX_BYTES,
                timeout_seconds=15.0,
                cookies=_context_cookies(context, url),
            )
            body = response.body
            if total_source_bytes + len(body) > _PRODUCT_IMAGES_TOTAL_MAX_BYTES:
                break
            normalized, media = normalize_image_media(body, source=response.final_url or url)
            digest = hashlib.sha256(normalized).hexdigest()
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)
            path = output_dir / f"source-image-{len(saved) + 1:02d}-{digest[:10]}{media.extension}"
            path.write_bytes(normalized)
            saved.append(path)
            total_source_bytes += len(body)
        except ImageMediaError:
            continue
        except Exception:
            continue
    return tuple(saved)


def _select_product_image_tier(
    context,
    tiers: list[tuple[str, list[str]]],
    output_dir: Path,
) -> tuple[str, list[str], tuple[Path, ...]]:
    """Choose the first image source tier that yields upload-viable product media.

    Every tier is isolated while being evaluated. This prevents an early banner,
    thumbnail or corrupt resource from blocking a stronger fallback source. If no
    tier reaches the central listing-image quality gate, preserve the first tier
    that at least produced decodable pixels for AI evidence instead of fabricating
    an upload candidate.
    """

    stage_root = output_dir.with_name(f".{output_dir.name}-staging")
    if stage_root.exists():
        shutil.rmtree(stage_root)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    stage_root.mkdir(parents=True, exist_ok=True)

    first_decodable: tuple[str, list[str], tuple[Path, ...]] | None = None
    chosen: tuple[str, list[str], tuple[Path, ...]] | None = None
    try:
        for index, (name, raw_urls) in enumerate(tiers, start=1):
            urls = _unique_image_urls(raw_urls)
            if not urls:
                continue
            tier_dir = stage_root / f"{index:02d}-{name}"
            paths = _download_page_images(context, urls, tier_dir)
            if paths and first_decodable is None:
                first_decodable = (name, urls, paths)
            if paths and select_listing_images(paths).selected:
                chosen = (name, urls, paths)
                break

        if chosen is None:
            chosen = first_decodable
        if chosen is None:
            return "none", [], ()

        name, urls, paths = chosen
        output_dir.mkdir(parents=True, exist_ok=True)
        final_paths: list[Path] = []
        for path in paths:
            target = output_dir / path.name
            shutil.copy2(path, target)
            final_paths.append(target)
        return name, urls, tuple(final_paths)
    finally:
        if stage_root.exists():
            shutil.rmtree(stage_root)


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
    cached_images = slot / "product-images"
    cached_image_files = tuple(
        sorted(path for path in cached_images.glob("*") if path.is_file())
    ) if cached_images.is_dir() else ()
    if not snapshot.is_file():
        return None
    age = time.time() - snapshot.stat().st_mtime
    if age < 0 or age > cache_ttl_seconds:
        return None

    try:
        cached_snapshot = source_snapshot_from_json(snapshot)
        if _canonical_source_url(cached_snapshot.requested_url) != _canonical_source_url(source_url):
            return None
    except Exception:
        return None

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

    product_images = tuple(
        sorted(path for path in output_images.glob("*") if path.is_file())
    ) if output_images.is_dir() else ()
    return CapturedProductSource(
        snapshot_path=output_snapshot,
        screenshot_path=output_screenshot,
        snapshot=cached_snapshot,
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
    """Capture one exact supplier page with independently optional visual evidence."""

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
        _, context, page, launched_now, source_session_lease = _connect_source_edge(
            playwright,
            profile_dir=Path(profile_dir).resolve(),
            port=int(cdp_port),
            start_url=source_url,
        )
        try:
            page.set_default_timeout(15_000)
            if use_current_page:
                if page.url in {"", "about:blank"}:
                    raise RuntimeError("--source-use-current-page 时 source Edge 没有已打开网页。")
            else:
                browser_visual_hud_status(
                    page,
                    "正在打开商品链接",
                    "正在导航到供应商商品页；页面加载后 HUD 会自动续接。",
                    phase=0,
                )
                page.goto(source_url, wait_until="domcontentloaded", timeout=45_000)

            browser_visual_hud_status(
                page,
                "商品页面已打开",
                "正在读取当前页面并准备加载完整商品内容。",
                phase=1,
            )
            _load_lazy_page(
                page,
                initial_wait_ms=int(initial_wait_ms),
                scroll_wait_ms=int(scroll_wait_ms),
                max_scroll_steps=int(max_scroll_steps),
            )

            browser_visual_hud_status(
                page,
                "正在提取商品信息",
                "正在读取页面文字、表格、JSON-LD 与嵌入式商品数据。",
                phase=2,
            )
            snapshot = _capture_snapshot_with_navigation_retry(
                page,
                requested_url=source_url,
                max_visible_text_chars=int(max_visible_text_chars),
                settle_ms=int(scroll_wait_ms),
            )
            generic_images = list(snapshot.image_urls)
            structured_images = _structured_product_image_urls(snapshot)
            gallery_images = _discover_product_gallery_images(
                page,
                snapshot,
                settle_ms=int(scroll_wait_ms),
            )
            owned_images = _unique_image_urls([*structured_images, *gallery_images])

            browser_visual_hud_status(
                page,
                "正在检索详情资源",
                "当前商品图库优先；正在整理仅用于兜底的公开详情图片资源。",
                phase=2,
            )
            detail_documents, detail_images = _discover_detail_images(context, snapshot)
            if detail_documents:
                snapshot.meta["detail_document_urls"] = json.dumps(
                    detail_documents,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            snapshot.meta["structured_product_image_count"] = str(len(structured_images))
            snapshot.meta["gallery_image_count"] = str(len(gallery_images))
            snapshot.meta["generic_visible_image_count"] = str(len(generic_images))
            snapshot.meta["detail_image_count"] = str(len(detail_images))

            browser_visual_hud_status(
                page,
                "正在整理商品图片",
                (
                    f"商品图库 {len(owned_images)} 个候选；"
                    "将逐层验证真实图片，异常资源会直接丢弃。"
                ),
                phase=3,
            )
            product_image_dir = target_dir / "product-images"
            image_source, selected_urls, product_images = _select_product_image_tier(
                context,
                [
                    ("product_structured_gallery", owned_images),
                    ("visible_dom_fallback", generic_images),
                    ("detail_document_fallback", detail_images),
                ],
                product_image_dir,
            )
            snapshot.image_urls = selected_urls
            snapshot.meta["product_image_source"] = image_source
            snapshot.meta["product_image_candidate_count"] = str(len(selected_urls))

            browser_visual_hud_status(
                page,
                "正在生成页面证据",
                "即将截取供应商页面证据；HUD 会在截图瞬间自动隐藏。",
                phase=3,
            )
            screenshot_path = target_dir / "source-page.png"
            if screenshot_path.exists():
                screenshot_path.unlink()
            set_browser_visual_hud_capture_safe(page, True)
            try:
                screenshot_ok, screenshot_note = _screenshot_with_navigation_retry(
                    page,
                    screenshot_path,
                    settle_ms=int(scroll_wait_ms),
                )
            finally:
                set_browser_visual_hud_capture_safe(page, False)

            if screenshot_note:
                snapshot.warnings.append(screenshot_note)
            if not screenshot_ok and not product_images:
                snapshot.warnings.append(
                    "visual evidence unavailable; canonical text/table/structured source snapshot remains usable"
                )
            snapshot.meta["screenshot_available"] = "true" if screenshot_ok else "false"
            snapshot.meta["product_images_downloaded"] = str(len(product_images))
            snapshot.meta["visual_evidence_available"] = (
                "true" if (screenshot_ok or product_images) else "false"
            )
            snapshot_path = write_source_snapshot(
                snapshot,
                target_dir / "source-snapshot.json",
            )

            finish_browser_visual_hud(
                page,
                success=True,
                title="商品页信息提取完成",
                thought=(
                    f"已完成商品页面采集 · 商品图 {len(product_images)} 张 · "
                    f"来源 {image_source} · 结构化证据已写入本次任务。"
                ),
                hold_ms=420,
                destroy=True,
            )
        except Exception:
            finish_browser_visual_hud(
                page,
                success=False,
                title="商品页采集未完成",
                thought="浏览器采集已停止，Listing Studio 会保留当前页面和错误现场。",
                hold_ms=260,
                destroy=True,
            )
            raise
        finally:
            source_session_lease.release()

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
    "_canonical_source_url",
    "_detail_document_urls",
    "_detail_image_urls_from_text",
    "_source_cache_key",
    "_structured_product_image_urls",
    "capture_product_source",
    "validate_source_url",
]
