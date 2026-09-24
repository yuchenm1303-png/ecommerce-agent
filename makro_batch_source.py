"""Prefetch one supplier product into a job-local source cache.

Batch preparation serializes this small browser-facing stage so the existing
Source Edge contract remains deterministic. Later batch job workers consume the
exact cached bytes and can run Makro/AI work in parallel without competing for
the Source Edge's current tab.

Recoverable supplier login/human-verification/modal pages are not failures. The
worker persists a waiting checkpoint and exits with the dedicated interaction
code so the GUI can release every browser/process resource while keeping Source
Edge itself and the current page intact for the user. Current page-state semantics
are decided from a fresh AI observation; Python only captures the page and owns
the pause/resume lifecycle.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.providers.registry import (
    ProviderConfig,
    ProviderConfigurationError,
    SUPPORTED_PROVIDERS,
    build_semantic_provider,
    default_api_key_env,
)
from app.source_capture import (
    DEFAULT_SOURCE_CDP_PORT,
    SourceInteractionRequired,
    capture_product_source,
)
from app.source_interaction import (
    SOURCE_INTERACTION_EXIT_CODE,
    SOURCE_OUTCOME_FILENAME,
    configure_source_page_state_provider,
)
from app.workflow_diagnostics import configure_diagnostics, diag_event


_DEFAULT_PAGE_STATE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_DEFAULT_PAGE_STATE_MODEL = "qwen3.7-plus"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prefetch one supplier URL for a batch job.")
    parser.add_argument("--product-url", required=True)
    parser.add_argument("--source-profile-dir", default="browser_profiles/source-edge")
    parser.add_argument("--source-cdp-port", type=int, default=DEFAULT_SOURCE_CDP_PORT)
    parser.add_argument("--source-wait-ms", type=int, default=1800)
    parser.add_argument("--source-scroll-wait-ms", type=int, default=180)
    parser.add_argument("--source-max-scroll-steps", type=int, default=120)
    parser.add_argument("--source-max-visible-text-chars", type=int, default=120_000)
    parser.add_argument("--source-cache-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--page-state-provider",
        choices=SUPPORTED_PROVIDERS,
        default="openai-compatible",
        help="AI provider used only to judge the current Source Edge UI state.",
    )
    parser.add_argument("--page-state-model", default=_DEFAULT_PAGE_STATE_MODEL)
    parser.add_argument("--page-state-api-key-env", default="AI_API_KEY")
    parser.add_argument("--page-state-base-url", default=_DEFAULT_PAGE_STATE_BASE_URL)
    parser.add_argument("--page-state-request-timeout-seconds", type=float, default=120.0)
    parser.add_argument(
        "--resume-source-interaction",
        action="store_true",
        help=(
            "Resume one suspended Source job. The preserved current Source Edge page is freshly "
            "observed by AI before any fresh navigation; only a blocker that exists now stays suspended."
        ),
    )
    return parser


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _write_source_outcome(output_dir: Path, payload: dict[str, Any]) -> Path:
    target = output_dir / SOURCE_OUTCOME_FILENAME
    body = {"schema": 1, "updated_at": _now(), **payload}
    temp = target.with_name(f".{target.name}.tmp")
    temp.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(target)
    return target


def _build_page_state_provider(args: argparse.Namespace):
    provider_name = str(args.page_state_provider or "").strip().casefold()
    api_key_env = str(args.page_state_api_key_env or "").strip() or default_api_key_env(provider_name)
    config = ProviderConfig(
        provider=provider_name,
        model=str(args.page_state_model or "").strip(),
        api_key_env=api_key_env,
        base_url=str(args.page_state_base_url or "").strip(),
        structured_mode="json_object",
        request_timeout_seconds=float(args.page_state_request_timeout_seconds),
        enable_thinking=False if provider_name == "openai-compatible" else None,
    )
    return build_semantic_provider(config)


def main() -> int:
    args = build_parser().parse_args()
    output_dir = Path(args.output_dir).resolve()
    cache_dir = Path(args.source_cache_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    configure_diagnostics(
        output_dir,
        "makro_batch_source",
        product_url=args.product_url,
        source_cdp_port=args.source_cdp_port,
    )

    try:
        page_state_provider = _build_page_state_provider(args)
    except ProviderConfigurationError as exc:
        raise SystemExit(str(exc)) from exc
    configure_source_page_state_provider(page_state_provider)

    print("BATCH_SOURCE START", flush=True)
    diag_event(
        "batch_source_capture",
        "START",
        product_url=args.product_url,
        output_dir=str(output_dir),
        cache_dir=str(cache_dir),
        source_profile_dir=str(Path(args.source_profile_dir).resolve()),
        source_cdp_port=args.source_cdp_port,
        initial_wait_ms=args.source_wait_ms,
        scroll_wait_ms=args.source_scroll_wait_ms,
        max_scroll_steps=args.source_max_scroll_steps,
        max_visible_text_chars=args.source_max_visible_text_chars,
        resume_after_interaction=bool(args.resume_source_interaction),
        page_state_provider=str(args.page_state_provider),
        page_state_model=str(args.page_state_model),
    )
    try:
        captured = capture_product_source(
            args.product_url,
            output_dir=output_dir,
            profile_dir=args.source_profile_dir,
            cdp_port=args.source_cdp_port,
            initial_wait_ms=args.source_wait_ms,
            scroll_wait_ms=args.source_scroll_wait_ms,
            max_scroll_steps=args.source_max_scroll_steps,
            max_visible_text_chars=args.source_max_visible_text_chars,
            cache_dir=cache_dir,
            cache_ttl_seconds=3600,
            force_refresh=False,
            resume_after_interaction=bool(args.resume_source_interaction),
        )
    except SourceInteractionRequired as exc:
        outcome = _write_source_outcome(
            output_dir,
            {
                "outcome": "waiting_user",
                "checkpoint": "source_capture",
                "product_url": args.product_url,
                "interaction_kind": exc.kind,
                "interaction_reason": exc.reason,
                "observed_url": exc.observed_url,
                "observed_title": exc.title,
                "resume_after_interaction": bool(args.resume_source_interaction),
                "browser_closed": False,
            },
        )
        diag_event(
            "batch_source_capture",
            "WAITING",
            product_url=args.product_url,
            interaction_kind=exc.kind,
            interaction_reason=exc.reason,
            observed_url=exc.observed_url,
            observed_title=exc.title,
            checkpoint="source_capture",
            source_outcome=str(outcome.resolve()),
        )
        print(
            "SOURCE_INTERACTION_REQUIRED "
            f"kind={exc.kind} checkpoint=source_capture browser_preserved=true "
            f"detail={exc.reason}",
            flush=True,
        )
        print(f"source_outcome={outcome}", flush=True)
        return SOURCE_INTERACTION_EXIT_CODE

    # Batch execution has no safe reason to advance into Makro Step 3 when the
    # supplier prefetch produced zero local product images. Source capture already
    # performed bounded fresh/re-render recovery. Stop here so a field-rich page
    # can never become a false DONE listing with Product Photos 0/N.
    if not captured.product_image_paths:
        detail = (
            "supplier source is semantically readable but no local product image remained "
            "after bounded image recovery; batch execution was stopped before Makro writes"
        )
        outcome = _write_source_outcome(
            output_dir,
            {
                "outcome": "failed",
                "checkpoint": "source_capture",
                "product_url": args.product_url,
                "failure_kind": "LISTING_MEDIA_UNAVAILABLE",
                "failure_reason": detail,
                "observed_url": captured.snapshot.final_url,
                "observed_title": captured.snapshot.title,
                "resume_after_interaction": bool(args.resume_source_interaction),
                "browser_closed": False,
            },
        )
        diag_event(
            "batch_source_capture",
            "FAILED",
            product_url=args.product_url,
            error_type="ListingMediaUnavailable",
            detail=detail,
            source_outcome=str(outcome.resolve()),
            product_images=0,
            warnings=list(captured.snapshot.warnings),
        )
        print(
            "BATCH_SOURCE FAILED kind=LISTING_MEDIA_UNAVAILABLE images=0 "
            "detail=source image recovery exhausted; Makro execution not started",
            flush=True,
        )
        print(f"source_outcome={outcome}", flush=True)
        return 1

    diag_event(
        "batch_source_capture",
        "COMPLETE",
        snapshot=str(captured.snapshot_path.resolve()),
        screenshot=str(captured.screenshot_path.resolve()),
        product_images=len(captured.product_image_paths),
        cache_hit=bool(captured.cache_hit),
        source_edge_launched=bool(captured.launched_now),
        visible_text_chars=len(captured.snapshot.visible_text),
        table_rows=len(captured.snapshot.table_rows),
        json_ld_items=len(captured.snapshot.json_ld),
        embedded_data_items=len(captured.snapshot.embedded_data),
        page_state=captured.snapshot.meta.get("page_state", ""),
        page_state_confidence=captured.snapshot.meta.get("page_state_confidence", ""),
        warnings=list(captured.snapshot.warnings),
    )
    payload = {
        "product_url": args.product_url,
        "snapshot": str(captured.snapshot_path.resolve()),
        "screenshot": str(captured.screenshot_path.resolve()),
        "product_images": [str(path.resolve()) for path in captured.product_image_paths],
        "cache_hit": bool(captured.cache_hit),
        "browser_closed": False,
    }
    manifest = output_dir / "batch-source.json"
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    outcome = _write_source_outcome(
        output_dir,
        {
            "outcome": "complete",
            "checkpoint": "source_capture",
            "product_url": args.product_url,
            "interaction_kind": "",
            "interaction_reason": "",
            "observed_url": captured.snapshot.final_url,
            "observed_title": captured.snapshot.title,
            "resume_after_interaction": bool(args.resume_source_interaction),
            "browser_closed": False,
            "source_manifest": str(manifest.resolve()),
        },
    )
    diag_event(
        "batch_source_manifest",
        "COMPLETE",
        manifest=str(manifest.resolve()),
        source_outcome=str(outcome.resolve()),
        product_images=len(captured.product_image_paths),
        cache_hit=bool(captured.cache_hit),
    )
    print(f"BATCH_SOURCE COMPLETE images={len(captured.product_image_paths)}", flush=True)
    print(f"manifest={manifest}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
