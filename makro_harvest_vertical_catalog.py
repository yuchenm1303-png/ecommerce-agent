from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from app.browser_session import cdp_endpoint, is_cdp_ready
from app.makro.vertical_catalog import harvest_vertical_catalog


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Harvest the full live Makro Step 1 Vertical taxonomy into a resumable local catalog. "
            "Uses a dedicated probe tab only; never selects a brand, creates a listing, writes Step 3, "
            "saves, or clicks Send to QC."
        )
    )
    parser.add_argument("--cdp-port", type=int, default=9222)
    parser.add_argument(
        "--output-dir",
        default="logs/makro-vertical-catalog",
        help="Directory for checkpoint, JSON catalog and CSV leaf list.",
    )
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Resume the existing checkpoint in output-dir (default: true).",
    )
    parser.add_argument(
        "--max-paths",
        type=int,
        default=0,
        help="Process at most N taxonomy paths this run; 0 means until complete.",
    )
    parser.add_argument("--step1-ready-timeout", type=float, default=30.0)
    parser.add_argument("--transition-timeout", type=float, default=8.0)
    parser.add_argument(
        "--retries",
        type=int,
        default=2,
        help="Retries after the first attempt for each taxonomy path.",
    )
    parser.add_argument("--max-items-per-level", type=int, default=200)
    return parser


def _progress(event: dict) -> None:
    path = " / ".join(event.get("path") or [])
    line = (
        "CATALOG "
        f"{str(event.get('status') or '').upper()} "
        f"processed={event.get('processed_total')} "
        f"pending={event.get('pending')} "
        f"leaves={event.get('leaves')} "
        f"branches={event.get('branches')} "
        f"failed={event.get('failed')} "
        f"path={path}"
    )
    if event.get("error"):
        line += f" error={event['error']}"
    print(line, flush=True)


def main() -> int:
    args = build_parser().parse_args()
    if args.max_paths < 0:
        raise SystemExit("--max-paths cannot be negative")
    if args.step1_ready_timeout <= 0 or args.step1_ready_timeout > 180:
        raise SystemExit("--step1-ready-timeout must be in 0..180 seconds")
    if args.transition_timeout <= 0 or args.transition_timeout > 60:
        raise SystemExit("--transition-timeout must be in 0..60 seconds")
    if args.retries < 0 or args.retries > 8:
        raise SystemExit("--retries must be in 0..8")
    if args.max_items_per_level < 20 or args.max_items_per_level > 1000:
        raise SystemExit("--max-items-per-level must be in 20..1000")

    if not is_cdp_ready(args.cdp_port):
        raise SystemExit(
            f"Makro long-lived Edge CDP 127.0.0.1:{args.cdp_port} is not reachable. "
            "The harvester will not start or restart the browser."
        )

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(cdp_endpoint(args.cdp_port))
        contexts = list(browser.contexts)
        if not contexts:
            raise SystemExit("Connected to Makro Edge but no browser context is available.")
        context = contexts[0]

        # Ownership boundary: never navigate or reuse the user's listing tab.
        probe_page = context.new_page()
        try:
            result = harvest_vertical_catalog(
                probe_page,
                output_dir,
                resume=bool(args.resume),
                max_paths=int(args.max_paths),
                step1_timeout_s=float(args.step1_ready_timeout),
                transition_timeout_s=float(args.transition_timeout),
                retries=int(args.retries),
                max_items_per_level=int(args.max_items_per_level),
                progress=_progress,
            )
            try:
                probe_page.screenshot(
                    path=str(output_dir / "vertical-catalog-final.png"),
                    full_page=True,
                )
            except Exception:
                pass
        finally:
            # Only the dedicated probe tab belongs to this process.
            try:
                probe_page.close()
            except Exception:
                pass

    print("===== MAKRO VERTICAL CATALOG HARVEST =====")
    print(f"complete={result['complete']}")
    print(f"stats={json.dumps(result['stats'], ensure_ascii=False)}")
    print(f"catalog={result['catalog_path']}")
    print(f"csv={result['csv_path']}")
    print(f"checkpoint={result['checkpoint_path']}")
    print(
        "safety=dedicated probe tab only; brand_selected=False; listing_created=False; "
        "Step3 writes=0; Save=False; Send to QC=False; long-lived Makro Edge left running"
    )

    if result["complete"]:
        return 0
    if args.max_paths > 0:
        print("status=partial_by_request; rerun the same command with --resume to continue")
        return 0
    print("status=incomplete; unresolved paths remain in checkpoint and are resumable")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
