from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import sync_playwright

from .browser_session import _connect_browser_resilient, acquire_cdp_session_lease
from .image_media import ImageMediaError, normalize_image_media
from .source_snapshot import SourceSnapshot


_RECOVERY_MAX_IMAGES = 8
_RECOVERY_MIN_NATURAL_EDGE = 280
_RECOVERY_MIN_RENDERED_EDGE = 96
_RECOVERY_SCREENSHOT_TIMEOUT_MS = 5_000


@dataclass(slots=True, frozen=True)
class SourceImageRecovery:
    attempted: int
    saved: tuple[Path, ...]
    failures: tuple[dict[str, str], ...]
    page_url: str

    @property
    def succeeded(self) -> bool:
        return bool(self.saved)

    def summary(self) -> dict[str, object]:
        return {
            "attempted": self.attempted,
            "saved": len(self.saved),
            "failures": list(self.failures),
            "page_url": _safe_url(self.page_url),
        }


def _safe_url(value: str) -> str:
    """Drop query/fragment secrets while keeping enough URL shape for diagnostics."""

    try:
        parsed = urlsplit(str(value or ""))
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    host = parsed.hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path or "/", "", ""))


def _host(value: str) -> str:
    try:
        return str(urlsplit(str(value or "")).hostname or "").casefold()
    except ValueError:
        return ""


def _choose_page(context, snapshot: SourceSnapshot):
    expected_hosts = {
        host
        for host in (_host(snapshot.final_url), _host(snapshot.requested_url))
        if host
    }
    pages = [page for page in context.pages if str(page.url or "") not in {"", "about:blank"}]
    for page in reversed(pages):
        if _host(page.url) in expected_hosts:
            return page
    return pages[-1] if pages else None


def _rendered_candidates(page, candidate_urls: set[str]) -> list[dict[str, object]]:
    rows = page.evaluate(
        """(wanted) => {
          const wantedSet = new Set(wanted || []);
          const out = [];
          [...document.images].forEach((img, index) => {
            const current = String(img.currentSrc || img.src || '').trim();
            const fallback = String(img.src || '').trim();
            if (!wantedSet.has(current) && !wantedSet.has(fallback)) return;
            const rect = img.getBoundingClientRect();
            const style = getComputedStyle(img);
            out.push({
              index,
              current_src: current,
              natural_width: Number(img.naturalWidth || 0),
              natural_height: Number(img.naturalHeight || 0),
              rendered_width: Number(rect.width || 0),
              rendered_height: Number(rect.height || 0),
              visible: style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0,
            });
          });
          return out;
        }""",
        sorted(candidate_urls),
    )
    output: list[dict[str, object]] = []
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        if not bool(item.get("visible")):
            continue
        if max(int(item.get("natural_width") or 0), int(item.get("natural_height") or 0)) < _RECOVERY_MIN_NATURAL_EDGE:
            continue
        if min(float(item.get("rendered_width") or 0), float(item.get("rendered_height") or 0)) < _RECOVERY_MIN_RENDERED_EDGE:
            continue
        output.append(item)
    output.sort(
        key=lambda item: (
            int(item.get("natural_width") or 0) * int(item.get("natural_height") or 0),
            float(item.get("rendered_width") or 0) * float(item.get("rendered_height") or 0),
        ),
        reverse=True,
    )
    return output


def recover_rendered_product_images(
    snapshot: SourceSnapshot,
    *,
    output_dir: str | Path,
    cdp_port: int,
    max_images: int = _RECOVERY_MAX_IMAGES,
) -> SourceImageRecovery:
    """Recover product pixels already rendered by the trusted Source Edge page.

    The normal source downloader remains the primary path. This bounded fallback is
    used only for image URLs mechanically observed on the supplier page when the
    independent secondary-resource transport produced no local product image. It
    does not navigate, solve challenges, select variants, or fetch arbitrary model-
    supplied URLs. Locator screenshots reuse pixels that Edge has already rendered,
    which preserves browser proxy/cookie/CDN behaviour without weakening the SSRF
    boundary of ``fetch_public_resource``.
    """

    candidates = {
        str(value or "").strip()
        for value in snapshot.image_urls
        if str(value or "").strip().startswith(("http://", "https://"))
    }
    if not candidates or max_images <= 0:
        return SourceImageRecovery(0, (), (), "")

    target = Path(output_dir).resolve() / "product-images"
    target.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    failures: list[dict[str, str]] = []
    seen_hashes: set[str] = set()
    attempted = 0
    page_url = ""

    lease = acquire_cdp_session_lease(int(cdp_port))
    try:
        with sync_playwright() as playwright:
            browser = _connect_browser_resilient(playwright, int(cdp_port))
            contexts = list(browser.contexts)
            if not contexts:
                return SourceImageRecovery(0, (), ({"stage": "attach", "error": "no_browser_context"},), "")
            page = _choose_page(contexts[0], snapshot)
            if page is None:
                return SourceImageRecovery(0, (), ({"stage": "attach", "error": "no_supplier_page"},), "")
            page_url = str(page.url or "")
            rendered = _rendered_candidates(page, candidates)
            images = page.locator("img")
            for item in rendered:
                if len(saved) >= int(max_images):
                    break
                attempted += 1
                source_url = _safe_url(str(item.get("current_src") or ""))
                try:
                    locator = images.nth(int(item["index"]))
                    locator.scroll_into_view_if_needed(timeout=_RECOVERY_SCREENSHOT_TIMEOUT_MS)
                    raw = locator.screenshot(
                        type="jpeg",
                        quality=92,
                        timeout=_RECOVERY_SCREENSHOT_TIMEOUT_MS,
                    )
                    normalized, media = normalize_image_media(
                        raw,
                        source=source_url or "rendered supplier image",
                    )
                    digest = hashlib.sha256(normalized).hexdigest()
                    if digest in seen_hashes:
                        continue
                    seen_hashes.add(digest)
                    path = target / f"source-image-recovered-{len(saved) + 1:02d}-{digest[:10]}{media.extension}"
                    path.write_bytes(normalized)
                    saved.append(path)
                except ImageMediaError as exc:
                    failures.append({
                        "stage": "normalize",
                        "url": source_url,
                        "error": f"{type(exc).__name__}: {exc}"[:320],
                    })
                except Exception as exc:
                    failures.append({
                        "stage": "screenshot",
                        "url": source_url,
                        "error": f"{type(exc).__name__}: {exc}"[:320],
                    })
    except Exception as exc:
        failures.append({
            "stage": "browser_recovery",
            "error": f"{type(exc).__name__}: {exc}"[:320],
        })
    finally:
        lease.release()

    result = SourceImageRecovery(attempted, tuple(saved), tuple(failures[:24]), page_url)
    print(
        "SOURCE_IMAGE_RECOVERY " + json.dumps(result.summary(), ensure_ascii=False, separators=(",", ":")),
        flush=True,
    )
    return result


__all__ = ["SourceImageRecovery", "recover_rendered_product_images"]
