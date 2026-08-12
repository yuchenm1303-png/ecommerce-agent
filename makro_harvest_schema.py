"""Harvest Makro vertical loadsheets and build a local schema registry.

This utility is deliberately isolated from listing creation. It attaches to the
existing authenticated Makro Edge, opens its own seller-portal tab, navigates
only to Bulk Product Creation, enumerates verticals and downloads official Excel
loadsheets. It never uploads a file, creates a listing, saves a listing, or sends
anything to QC.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from app.browser_session import DEFAULT_CDP_PORT, EdgeHarness, is_cdp_ready
from app.makro.schema_harvest import (
    MAKRO_SELLER_HOME,
    harvest_vertical_loadsheets,
    write_harvest_diagnostics,
    write_harvest_report,
)
from app.makro.schema_registry import (
    build_schema_registry_from_directory,
    write_schema_registry,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="只读枚举 Makro Bulk Product Creation Vertical、下载官方 loadsheet 并构建 schema registry。"
    )
    parser.add_argument("--cdp-port", type=int, default=DEFAULT_CDP_PORT)
    parser.add_argument("--profile-dir", default="browser_profiles/makro-edge")
    parser.add_argument("--output-dir", default="logs/makro-schema-harvest")
    parser.add_argument("--discover-only", action="store_true", help="只枚举 Vertical，不下载 Excel。")
    parser.add_argument(
        "--parse-only",
        metavar="DIR",
        default="",
        help="不连接浏览器，只解析指定目录内已有 xlsx/xlsm 并生成 registry。",
    )
    parser.add_argument("--vertical", action="append", default=[], help="只下载指定 Vertical，可重复。")
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少个 Vertical；0 表示不限制。")
    parser.add_argument("--download-timeout-ms", type=int, default=30_000)
    return parser


def _write_registry(downloads: Path, run_dir: Path) -> tuple[Path, list[dict[str, str]], dict]:
    registry, parse_failures = build_schema_registry_from_directory(downloads)
    path = write_schema_registry(registry, run_dir / "makro-schema-registry.json")
    return path, parse_failures, registry


def main() -> int:
    args = build_parser().parse_args()
    if args.limit < 0:
        raise SystemExit("--limit 不能小于 0。")
    if args.download_timeout_ms <= 0:
        raise SystemExit("--download-timeout-ms 必须大于 0。")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = Path(args.output_dir).resolve() / f"harvest-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    if args.parse_only:
        registry_path, parse_failures, registry = _write_registry(Path(args.parse_only), run_dir)
        payload = {
            "mode": "parse_only",
            "read_only": True,
            "input_dir": str(Path(args.parse_only).resolve()),
            "registry": str(registry_path.resolve()),
            "registry_stats": registry.get("stats", {}),
            "parse_failures": parse_failures,
        }
        write_harvest_report(payload, run_dir / "harvest-report.json")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if not parse_failures else 2

    # Hard policy: unlike general EdgeHarness callers, schema harvesting never
    # starts or restarts the long-lived browser. The user must already have the
    # authenticated Makro Edge/CDP session running.
    if not is_cdp_ready(args.cdp_port):
        raise RuntimeError(
            f"长期 Makro Edge CDP 127.0.0.1:{args.cdp_port} 不可达；schema harvest 不会自动启动/重启浏览器。"
        )

    downloads = run_dir / "downloads"
    report: dict = {}
    with sync_playwright() as playwright:
        harness = EdgeHarness(
            playwright,
            profile_dir=Path(args.profile_dir).resolve(),
            port=args.cdp_port,
            start_url=MAKRO_SELLER_HOME,
        )
        if harness.launched_now:
            raise RuntimeError("schema harvest 禁止启动新的 Makro Edge。")
        if harness.context is None:
            raise RuntimeError("Makro Edge context unavailable")

        # Never navigate or mutate the currently-owned listing tab. Harvesting
        # owns a fresh seller-portal tab and closes only that tab afterwards.
        page = harness.context.new_page()
        try:
            report = harvest_vertical_loadsheets(
                page,
                downloads,
                discover_only=args.discover_only,
                requested_verticals=args.vertical,
                limit=args.limit,
                timeout_ms=args.download_timeout_ms,
            )
            report["page_url"] = page.url
            report["owned_tab_only"] = True
        except Exception as exc:
            report = {
                "mode": "discover" if args.discover_only else "harvest",
                "read_only": True,
                "error": str(exc),
                "owned_tab_only": True,
                "diagnostics": write_harvest_diagnostics(page, run_dir),
            }
            write_harvest_report(report, run_dir / "harvest-report.json")
            raise
        finally:
            try:
                page.close()
            except Exception:
                pass
            harness.detach()

    report["mode"] = "discover" if args.discover_only else "harvest"
    report_path = write_harvest_report(report, run_dir / "harvest-report.json")

    if args.discover_only:
        print(f"Verticals discovered: {report.get('discovered_vertical_count', 0)}")
        print(f"Report: {report_path.resolve()}")
        return 0

    if not report.get("downloads"):
        print(f"没有成功下载任何 loadsheet。Report: {report_path.resolve()}")
        return 2

    registry_path, parse_failures, registry = _write_registry(downloads, run_dir)
    report["registry"] = str(registry_path.resolve())
    report["registry_stats"] = registry.get("stats", {})
    report["parse_failures"] = parse_failures
    write_harvest_report(report, report_path)

    print("===== MAKRO SCHEMA HARVEST COMPLETE =====")
    print(f"verticals_discovered={report.get('discovered_vertical_count', 0)}")
    print(f"loadsheets_downloaded={len(report.get('downloads') or [])}")
    print(f"download_failures={len(report.get('failures') or [])}")
    print(f"parse_failures={len(parse_failures)}")
    print(f"registry={registry_path.resolve()}")
    print(f"report={report_path.resolve()}")
    return 0 if not report.get("failures") and not parse_failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
