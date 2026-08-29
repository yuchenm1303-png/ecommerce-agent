from __future__ import annotations

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
from .update_browser_gate import close_managed_browser


# Keep the historical public import surface stable while the large page-capture
# engine remains focused on one capture attempt. This module owns only the
# dedicated Source Edge lifecycle around that engine.
CapturedProductSource = _engine.CapturedProductSource
DEFAULT_SOURCE_CDP_PORT = _engine.DEFAULT_SOURCE_CDP_PORT
SourceAccessBlocked = _engine.SourceAccessBlocked
SOURCE_CAPTURE_CACHE_VERSION = _engine.SOURCE_CAPTURE_CACHE_VERSION
_canonical_source_url = _engine._canonical_source_url
_detail_document_urls = _engine._detail_document_urls
_detail_image_urls_from_text = _engine._detail_image_urls_from_text
_source_cache_key = _engine._source_cache_key
validate_source_url = _engine.validate_source_url


def __getattr__(name: str):
    return getattr(_engine, name)


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
) -> CapturedProductSource:
    return _engine.capture_product_source(
        url,
        output_dir=output_dir,
        profile_dir=profile_dir,
        cdp_port=cdp_port,
        initial_wait_ms=initial_wait_ms,
        scroll_wait_ms=scroll_wait_ms,
        max_scroll_steps=max_scroll_steps,
        max_visible_text_chars=max_visible_text_chars,
        use_current_page=use_current_page,
        cache_dir=cache_dir,
        cache_ttl_seconds=cache_ttl_seconds,
        force_refresh=force_refresh,
    )


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
    """Capture through one exclusive Source Edge transport generation.

    The entire first attempt and any proven-CDP recovery are one 9333 transport
    ownership transaction. Another process cannot attach to the same Source Edge
    between a failed transport and its safe generation replacement. Ordinary page
    navigation/rendering failures still never trigger a browser restart.
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
            probe = probe_cdp_automation(port, timeout_ms=8_000)
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
    "SOURCE_CAPTURE_CACHE_VERSION",
    "_canonical_source_url",
    "_detail_document_urls",
    "_detail_image_urls_from_text",
    "_source_cache_key",
    "capture_product_source",
    "validate_source_url",
]
