from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from app.browser_session import cdp_endpoint, is_cdp_ready
from app.makro.bulk_vertical_catalog import harvest_bulk_vertical_catalog


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read the complete Makro Bulk Product Creation Vertical dropdown into JSON/CSV. "
            "Uses a dedicated temporary tab only; never selects a vertical, downloads/uploads "
            "a loadsheet, creates a listing, saves, or clicks Send to QC."
        )
    )
    parser.add_argument("--cdp-port", type=int, default=9222)
    parser.add_argument(
        "--output-dir",
        default="logs/makro-bulk-vertical-catalog",
    )
    parser.add_argument("--navigation-timeout", type=float, default=20.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.navigation_timeout <= 0 or args.navigation_timeout > 120:
        raise SystemExit("--navigation-timeout must be in 0..120 seconds")
    if not is_cdp_ready(args.cdp_port):
        raise SystemExit(
            f"Makro long-lived Edge CDP 127.0.0.1:{args.cdp_port} is not reachable; "
            "this probe will not launch or restart the browser."
        )

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(cdp_endpoint(args.cdp_port))
        contexts = list(browser.contexts)
        if not contexts:
            raise SystemExit("Connected to Makro Edge but no browser context is available")
        result = harvest_bulk_vertical_catalog(
            contexts[0],
            output_dir,
            navigation_timeout_ms=int(args.navigation_timeout * 1000),
        )

    print("===== MAKRO BULK VERTICAL CATALOG =====")
    print(f"complete={result['complete']}")
    print(f"source_url={result['source_url']}")
    print(f"extraction_mode={result['extraction_mode']}")
    print(f"stats={json.dumps(result['stats'], ensure_ascii=False)}")
    print(f"catalog={result['catalog_path']}")
    print(f"csv={result['csv_path']}")
    print("sample_verticals:")
    for item in (result.get("verticals") or [])[:20]:
        print(f"  {item['vertical']}")
    print(
        "safety=dedicated probe tab only; vertical_selected=False; template_downloaded=False; "
        "file_uploaded=False; listing_created=False; Step3 writes=0; Save=False; "
        "Send to QC=False; long-lived Makro Edge left running"
    )
    return 0 if result["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
