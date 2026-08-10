"""Prefetch one supplier product into a job-local source cache.

Batch preparation serializes this small browser-facing stage so the existing
Source Edge contract remains deterministic. Later batch job workers consume the
exact cached bytes and can run Makro/AI work in parallel without competing for
the Source Edge's current tab.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.source_capture import DEFAULT_SOURCE_CDP_PORT, capture_product_source


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
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output_dir = Path(args.output_dir).resolve()
    cache_dir = Path(args.source_cache_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    print("BATCH_SOURCE START", flush=True)
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
    print(f"BATCH_SOURCE COMPLETE images={len(captured.product_image_paths)}", flush=True)
    print(f"manifest={manifest}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
