from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .image_media import ImageMediaError
from .public_resource_fetch import PublicResourceFetchError


def _host(value: str) -> str:
    try:
        return str(urlsplit(str(value or "")).hostname or "").casefold()
    except ValueError:
        return ""


def _safe_url(value: str) -> str:
    """Keep host/path diagnostics while stripping query, fragment and user info."""

    try:
        parsed = urlsplit(str(value or ""))
    except ValueError:
        return ""
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port:
        host = f"{host}:{port}"
    return urlunsplit((scheme, host, parsed.path or "/", "", ""))


def _supplier_referer(context) -> str:
    """Return the current supplier page URL without performing any navigation."""

    pages = [page for page in getattr(context, "pages", ()) if str(getattr(page, "url", "") or "") not in {"", "about:blank"}]
    return str(getattr(pages[-1], "url", "") or "") if pages else ""


def _classify_error(exc: BaseException) -> str:
    if isinstance(exc, ImageMediaError):
        return "image_decode"
    if isinstance(exc, PublicResourceFetchError):
        text = str(exc).casefold()
        if "http 401" in text:
            return "http_401"
        if "http 403" in text:
            return "http_403"
        if "http 404" in text:
            return "http_404"
        if "http 429" in text:
            return "http_429"
        if "hostname could not be resolved" in text or "no usable public address" in text:
            return "dns"
        if "non-public address" in text or "targets localhost" in text:
            return "network_policy"
        if "connection failed" in text:
            return "connection"
        if "redirect" in text:
            return "redirect"
        if "exceeded" in text and "byte" in text:
            return "size_limit"
        return "public_fetch"
    return type(exc).__name__ or "unknown"


def install_source_image_downloader(engine_module) -> None:
    """Install the production image downloader with bounded non-secret diagnostics.

    The underlying public-resource transport stays SSRF-bounded. The only request
    behaviour change is sending the current supplier page as Referer, which many
    image CDNs require. Failures remain aggregated by host/reason, while a bounded
    list of rejected candidates records sanitized URL paths plus rejection reasons.
    Cookies, query strings and fragments are never logged.
    """

    if bool(getattr(engine_module, "_source_image_downloader_hardened", False)):
        return

    def download_page_images(context, image_urls, output_dir: Path, *, max_images=None):
        limit = int(
            engine_module._PRODUCT_IMAGE_CANDIDATE_LIMIT
            if max_images is None
            else max_images
        )
        if not image_urls or limit <= 0:
            return ()

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        saved: list[Path] = []
        seen_hashes: set[str] = set()
        total_source_bytes = 0
        attempted = 0
        failures: Counter[str] = Counter()
        failure_hosts: Counter[str] = Counter()
        rejected_candidates: list[dict[str, str]] = []
        referer = _supplier_referer(context)

        def reject(url: str, reason: str) -> None:
            if len(rejected_candidates) >= 16:
                return
            safe = _safe_url(url)
            rejected_candidates.append(
                {
                    "url": safe or "<invalid-or-redacted>",
                    "reason": str(reason or "unknown")[:80],
                }
            )

        for raw_url in image_urls:
            if len(saved) >= limit or total_source_bytes >= engine_module._PRODUCT_IMAGES_TOTAL_MAX_BYTES:
                break
            url = str(raw_url or "").strip()
            if not url:
                continue
            attempted += 1
            try:
                response = engine_module.fetch_public_resource(
                    url,
                    max_bytes=engine_module._PRODUCT_IMAGE_MAX_BYTES,
                    timeout_seconds=15.0,
                    cookies=engine_module._context_cookies(context, url),
                    referer=referer,
                )
                body = response.body
                if total_source_bytes + len(body) > engine_module._PRODUCT_IMAGES_TOTAL_MAX_BYTES:
                    failures["total_size_limit"] += 1
                    reject(url, "total_size_limit")
                    break
                normalized, media = engine_module.normalize_image_media(
                    body,
                    source=response.final_url or url,
                )
                digest = hashlib.sha256(normalized).hexdigest()
                if digest in seen_hashes:
                    reject(url, "duplicate_content")
                    continue
                seen_hashes.add(digest)
                path = output_dir / f"source-image-{len(saved) + 1:02d}-{digest[:10]}{media.extension}"
                path.write_bytes(normalized)
                saved.append(path)
                total_source_bytes += len(body)
            except Exception as exc:
                reason = _classify_error(exc)
                failures[reason] += 1
                reject(url, reason)
                host = _host(url)
                if host:
                    failure_hosts[host] += 1

        diagnostic = {
            "attempted": attempted,
            "saved": len(saved),
            "failures": dict(sorted(failures.items())),
            "failure_hosts": dict(failure_hosts.most_common(8)),
            "rejected_candidates": rejected_candidates,
            "referer_host": _host(referer),
        }
        print(
            "SOURCE_IMAGE_DOWNLOAD "
            + json.dumps(diagnostic, ensure_ascii=False, separators=(",", ":")),
            flush=True,
        )
        return tuple(saved)

    engine_module._download_page_images = download_page_images
    engine_module._source_image_downloader_hardened = True


__all__ = ["install_source_image_downloader"]
