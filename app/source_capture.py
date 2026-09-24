from __future__ import annotations

import json
from pathlib import Path

from . import source_capture_engine as _engine
from .browser_session import launch_detached_edge
from .cdp_automation_health import (
    clear_cdp_poison,
    looks_like_cdp_transport_failure,
    mark_cdp_poisoned,
    probe_cdp_automation,
)
from .cdp_transport_lane import exclusive_cdp_transport_lane
from .source_capture_acceptance import SourceCaptureAcceptance, assess_source_capture
from .source_capture_cache import (
    SourceCachePublishResult,
    publish_source_capture_cache,
    read_source_capture_cache,
)
from .source_image_recovery import recover_rendered_product_images
from .source_snapshot import SourceInteractionRequired, write_source_snapshot
from .update_browser_gate import close_managed_browser


CapturedProductSource = _engine.CapturedProductSource
DEFAULT_SOURCE_CDP_PORT = _engine.DEFAULT_SOURCE_CDP_PORT
SourceAccessBlocked = _engine.SourceAccessBlocked
SOURCE_CAPTURE_CACHE_VERSION = _engine.SOURCE_CAPTURE_CACHE_VERSION
_canonical_source_url = _engine._canonical_source_url
_detail_document_urls = _engine._detail_document_urls
_detail_image_urls_from_text = _engine._detail_image_urls_from_text
_source_cache_key = _engine._source_cache_key
validate_source_url = _engine.validate_source_url

_SOURCE_PARTIAL_RECOVERY_ATTEMPTS = 2
_SOURCE_PARTIAL_CURRENT_PAGE_WAIT_MS = 2_600
_SOURCE_PARTIAL_FRESH_NAV_WAIT_MS = 3_600
_SOURCE_PARTIAL_SCROLL_WAIT_MS = 250


def __getattr__(name: str):
    return getattr(_engine, name)


def _acceptance(captured: CapturedProductSource) -> SourceCaptureAcceptance:
    return assess_source_capture(
        captured.snapshot,
        product_image_count=len(captured.product_image_paths),
    )


def _cached_capture(
    source_url: str,
    *,
    output_dir: Path,
    cache_dir: Path | None,
    cache_ttl_seconds: int,
) -> CapturedProductSource | None:
    """Read the optional source cache without letting cache I/O own task success."""

    materialized = read_source_capture_cache(
        source_url,
        cache_key=_source_cache_key(source_url),
        output_dir=Path(output_dir),
        cache_dir=Path(cache_dir) if cache_dir is not None else None,
        cache_ttl_seconds=int(cache_ttl_seconds),
    )
    if materialized is None:
        return None
    return CapturedProductSource(
        snapshot_path=materialized.snapshot_path,
        screenshot_path=materialized.screenshot_path,
        snapshot=materialized.snapshot,
        launched_now=False,
        product_image_paths=materialized.product_image_paths,
        cache_hit=True,
    )


def _refresh_capture_cache(
    source_url: str,
    source_dir: Path,
    cache_dir: Path | None,
) -> SourceCachePublishResult:
    """Publish cache best-effort; a failed cache write is never a capture failure."""

    return publish_source_capture_cache(
        source_url,
        cache_key=_source_cache_key(source_url),
        source_dir=Path(source_dir),
        cache_dir=Path(cache_dir) if cache_dir is not None else None,
    )


def _recover_missing_product_media(
    captured: CapturedProductSource,
    *,
    target_dir: Path,
    cdp_port: int,
) -> CapturedProductSource:
    """Use already-rendered Source Edge pixels when secondary image HTTP failed.

    The source engine deliberately keeps its independent SSRF-bounded HTTP fetcher
    as the primary path. If that transport returns zero product files while Edge
    visibly rendered supplier images, recover only the exact image URLs already
    observed in the canonical snapshot. This keeps product semantics untouched and
    avoids turning arbitrary URLs into browser fetches.
    """

    if captured.product_image_paths:
        return captured

    recovery = recover_rendered_product_images(
        captured.snapshot,
        output_dir=target_dir,
        cdp_port=int(cdp_port),
    )
    snapshot = captured.snapshot
    snapshot.meta["product_image_browser_recovery_attempted"] = "true"
    snapshot.meta["product_image_browser_recovery_saved"] = str(len(recovery.saved))
    snapshot.meta["product_image_browser_recovery_failures"] = str(len(recovery.failures))
    snapshot.meta["listing_media_ready"] = "true" if recovery.saved else "false"
    if recovery.failures:
        snapshot.meta["product_image_browser_recovery_diagnostics"] = json.dumps(
            recovery.summary(),
            ensure_ascii=False,
            separators=(",", ":"),
        )[:8_000]

    if recovery.saved:
        snapshot.meta["product_images_downloaded"] = str(len(recovery.saved))
        snapshot.meta["visual_evidence_available"] = "true"
        snapshot.warnings.append(
            "secondary image HTTP returned no usable product files; recovered rendered supplier images from Source Edge"
        )
    else:
        warning = (
            "no local product images captured after bounded browser-pixel recovery; "
            "semantic source evidence may remain usable but Makro Product Photos is not yet satisfiable"
        )
        if warning not in snapshot.warnings:
            snapshot.warnings.append(warning)

    write_source_snapshot(snapshot, captured.snapshot_path)
    return CapturedProductSource(
        snapshot_path=captured.snapshot_path,
        screenshot_path=captured.screenshot_path,
        snapshot=snapshot,
        launched_now=captured.launched_now,
        product_image_paths=tuple(recovery.saved),
        cache_hit=captured.cache_hit,
    )


def _browser_capture(
    source_url: str,
    *,
    target_dir: Path,
    profile_dir: str | Path,
    cdp_port: int,
    initial_wait_ms: int,
    scroll_wait_ms: int,
    max_scroll_steps: int,
    max_visible_text_chars: int,
    use_current_page: bool,
) -> CapturedProductSource:
    """Run one cache-blind browser acquisition attempt with bounded media recovery."""

    captured = _engine.capture_product_source(
        source_url,
        output_dir=target_dir,
        profile_dir=profile_dir,
        cdp_port=cdp_port,
        initial_wait_ms=initial_wait_ms,
        scroll_wait_ms=scroll_wait_ms,
        max_scroll_steps=max_scroll_steps,
        max_visible_text_chars=max_visible_text_chars,
        use_current_page=use_current_page,
        cache_dir=None,
        cache_ttl_seconds=0,
        force_refresh=True,
    )
    return _recover_missing_product_media(
        captured,
        target_dir=target_dir,
        cdp_port=cdp_port,
    )


def _capture_until_accepted(
    source_url: str,
    *,
    target_dir: Path,
    profile_dir: str | Path,
    cdp_port: int,
    initial_wait_ms: int,
    scroll_wait_ms: int,
    max_scroll_steps: int,
    max_visible_text_chars: int,
    use_current_page: bool,
    resume_after_interaction: bool,
) -> CapturedProductSource:
    """Capture until semantic evidence and, when possible, listing media stabilize.

    Semantic readiness and listing-media readiness are intentionally separate. A
    supplier page with rich text but no local product image receives the same two
    bounded recovery attempts instead of being accepted immediately. If semantic
    evidence is sound but media still cannot be recovered, the capture is returned
    with an explicit media-degraded marker so customer-supplied listing images can
    still rescue the job; downstream persisted acceptance must not call it complete
    until Product Photos is actually satisfied.
    """

    initial_use_current_page = bool(use_current_page or resume_after_interaction)
    captured = _browser_capture(
        source_url,
        target_dir=target_dir,
        profile_dir=profile_dir,
        cdp_port=cdp_port,
        initial_wait_ms=initial_wait_ms,
        scroll_wait_ms=scroll_wait_ms,
        max_scroll_steps=max_scroll_steps,
        max_visible_text_chars=max_visible_text_chars,
        use_current_page=initial_use_current_page,
    )
    verdict = _acceptance(captured)
    if verdict.ready and verdict.listing_media_ready:
        print(f"SOURCE_CAPTURE READINESS ready attempt=initial {verdict.describe()}", flush=True)
        return captured

    issue = "media_missing" if verdict.ready else "partial_source"
    print(
        "SOURCE_CAPTURE RECOVERY_NEEDED "
        f"attempt=initial issue={issue} {verdict.describe()} "
        f"detail={verdict.media_reason if verdict.ready else verdict.reason}",
        flush=True,
    )

    for attempt in range(1, _SOURCE_PARTIAL_RECOVERY_ATTEMPTS + 1):
        fresh_navigation = bool(
            not use_current_page and attempt == _SOURCE_PARTIAL_RECOVERY_ATTEMPTS
        )
        retry_use_current_page = not fresh_navigation
        retry_wait_ms = max(
            int(initial_wait_ms),
            _SOURCE_PARTIAL_FRESH_NAV_WAIT_MS
            if fresh_navigation
            else _SOURCE_PARTIAL_CURRENT_PAGE_WAIT_MS,
        )
        retry_scroll_wait_ms = max(int(scroll_wait_ms), _SOURCE_PARTIAL_SCROLL_WAIT_MS)
        mode = "fresh_navigation" if fresh_navigation else "current_page_settle"
        print(
            "SOURCE_CAPTURE RETRY "
            f"attempt={attempt}/{_SOURCE_PARTIAL_RECOVERY_ATTEMPTS} mode={mode} "
            f"previous={verdict.describe()}",
            flush=True,
        )

        captured = _browser_capture(
            source_url,
            target_dir=target_dir,
            profile_dir=profile_dir,
            cdp_port=cdp_port,
            initial_wait_ms=retry_wait_ms,
            scroll_wait_ms=retry_scroll_wait_ms,
            max_scroll_steps=max_scroll_steps,
            max_visible_text_chars=max_visible_text_chars,
            use_current_page=retry_use_current_page,
        )
        verdict = _acceptance(captured)
        if verdict.ready and verdict.listing_media_ready:
            print(
                f"SOURCE_CAPTURE READINESS ready attempt=recovery-{attempt} "
                f"mode={mode} {verdict.describe()}",
                flush=True,
            )
            return captured

    if verdict.ready:
        print(
            "SOURCE_CAPTURE MEDIA_DEGRADED "
            f"retries={_SOURCE_PARTIAL_RECOVERY_ATTEMPTS} {verdict.describe()} "
            f"detail={verdict.media_reason}",
            flush=True,
        )
        return captured

    print(
        "SOURCE_CAPTURE PARTIAL_SOURCE "
        f"retries={_SOURCE_PARTIAL_RECOVERY_ATTEMPTS} {verdict.describe()} "
        f"detail={verdict.reason}",
        flush=True,
    )
    raise _engine.SourceCaptureError(
        "PARTIAL_SOURCE: supplier product page remained incomplete after bounded recovery; "
        f"{verdict.describe()}. Downstream Resolver/photo selection was not started."
    )


def _capture_once(
    url: str,
    *,
    output_dir: str | Path,
    profile_dir: str | Path,
    cdp_port: int,
    initial_wait_ms: int,
    scroll_wait_ms: int,
    max_scroll_steps: int,
    max_visible_text_chars: int,
    use_current_page: bool,
    cache_dir: str | Path | None,
    cache_ttl_seconds: int,
    force_refresh: bool,
    resume_after_interaction: bool,
) -> CapturedProductSource:
    """Own cache lifecycle above the browser engine.

    Browser acquisition produces the canonical task result. Cache read/write is an
    optional accelerator around it. A semantically valid cache entry with zero
    product images is no longer accepted as fully reusable: it triggers a fresh
    browser capture so transient CDN/network failures cannot be frozen for the
    entire cache TTL.
    """

    source_url = validate_source_url(url)
    target_dir = Path(output_dir)
    cache_root = Path(cache_dir) if cache_dir is not None else None

    if not force_refresh and not use_current_page and not resume_after_interaction:
        cached = _cached_capture(
            source_url,
            output_dir=target_dir,
            cache_dir=cache_root,
            cache_ttl_seconds=int(cache_ttl_seconds),
        )
        if cached is not None:
            cached_verdict = _acceptance(cached)
            if cached_verdict.ready and cached_verdict.listing_media_ready:
                print(
                    f"SOURCE_CACHE ACCEPTED {cached_verdict.describe()}",
                    flush=True,
                )
                return cached
            if cached_verdict.ready:
                print(
                    "SOURCE_CACHE REJECTED_MEDIA_MISSING "
                    f"{cached_verdict.describe()} detail={cached_verdict.media_reason}",
                    flush=True,
                )
            else:
                print(
                    "SOURCE_CACHE REJECTED_PARTIAL "
                    f"{cached_verdict.describe()} detail={cached_verdict.reason}",
                    flush=True,
                )

    captured = _capture_until_accepted(
        source_url,
        target_dir=target_dir,
        profile_dir=profile_dir,
        cdp_port=cdp_port,
        initial_wait_ms=initial_wait_ms,
        scroll_wait_ms=scroll_wait_ms,
        max_scroll_steps=max_scroll_steps,
        max_visible_text_chars=max_visible_text_chars,
        use_current_page=use_current_page,
        resume_after_interaction=resume_after_interaction,
    )

    if cache_root is not None:
        cache_result = _refresh_capture_cache(source_url, target_dir, cache_root)
        if not cache_result.published:
            print(
                "SOURCE_CACHE NON_FATAL "
                f"key={_source_cache_key(source_url)} detail={cache_result.detail}",
                flush=True,
            )
    return captured


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
    resume_after_interaction: bool = False,
) -> CapturedProductSource:
    """Capture through one exclusive Source Edge transport generation.

    A recoverable login/challenge is represented by ``SourceInteractionRequired``.
    It is deliberately not handled inside the transport transaction, so unwinding
    releases both the Source CDP session and the exclusive 9333 lane while leaving
    the long-lived browser/page intact for the user.
    """

    source_url = validate_source_url(url)
    port = int(cdp_port)
    profile = Path(profile_dir).resolve()

    with exclusive_cdp_transport_lane(port):
        try:
            captured = _capture_once(
                source_url,
                output_dir=output_dir,
                profile_dir=profile,
                cdp_port=port,
                initial_wait_ms=initial_wait_ms,
                scroll_wait_ms=scroll_wait_ms,
                max_scroll_steps=max_scroll_steps,
                max_visible_text_chars=max_visible_text_chars,
                use_current_page=use_current_page,
                cache_dir=cache_dir,
                cache_ttl_seconds=cache_ttl_seconds,
                force_refresh=force_refresh,
                resume_after_interaction=resume_after_interaction,
            )
            if not captured.cache_hit:
                clear_cdp_poison(port)
            return captured
        except Exception as exc:
            if not looks_like_cdp_transport_failure(exc):
                raise

            poison = mark_cdp_poisoned(port, reason=str(exc))
            print(
                "SOURCE_CDP POISONED "
                f"port={port} generation={poison.get('endpoint_token', '')}",
                flush=True,
            )

            closed = close_managed_browser(port=port, deadline_s=6.0)
            if not closed.ok:
                raise RuntimeError(
                    "Source Edge automation transport is poisoned, but the dedicated browser "
                    f"could not be safely rotated: {closed.detail}"
                ) from exc

            launch_detached_edge(
                profile_dir=profile,
                port=port,
                start_url=source_url,
            )
            probe = probe_cdp_automation(
                port,
                timeout_ms=8_000,
                transport_lane_owned=True,
            )
            if not probe.automation_ready:
                mark_cdp_poisoned(
                    port,
                    endpoint_token=probe.endpoint_token,
                    reason=probe.error or "Source Edge recovery probe failed",
                )
                raise RuntimeError(
                    "Source Edge was restarted with its existing profile, but Playwright "
                    f"automation is still unavailable: {probe.error or probe.state}"
                ) from exc

            clear_cdp_poison(port)
            print(
                "SOURCE_CDP RECOVERED "
                f"port={port} generation={probe.endpoint_token}",
                flush=True,
            )

            try:
                captured = _capture_once(
                    source_url,
                    output_dir=output_dir,
                    profile_dir=profile,
                    cdp_port=port,
                    initial_wait_ms=initial_wait_ms,
                    scroll_wait_ms=scroll_wait_ms,
                    max_scroll_steps=max_scroll_steps,
                    max_visible_text_chars=max_visible_text_chars,
                    use_current_page=use_current_page,
                    cache_dir=cache_dir,
                    cache_ttl_seconds=cache_ttl_seconds,
                    force_refresh=force_refresh,
                    resume_after_interaction=resume_after_interaction,
                )
            except Exception as retry_exc:
                if looks_like_cdp_transport_failure(retry_exc):
                    mark_cdp_poisoned(port, reason=str(retry_exc))
                raise
            if not captured.cache_hit:
                clear_cdp_poison(port)
            return captured


__all__ = [
    "CapturedProductSource",
    "DEFAULT_SOURCE_CDP_PORT",
    "SourceAccessBlocked",
    "SourceInteractionRequired",
    "SOURCE_CAPTURE_CACHE_VERSION",
    "_cached_capture",
    "_canonical_source_url",
    "_detail_document_urls",
    "_detail_image_urls_from_text",
    "_refresh_capture_cache",
    "_source_cache_key",
    "capture_product_source",
    "validate_source_url",
]
