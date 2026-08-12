from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from app.browser_session import cdp_endpoint, is_cdp_ready, select_listing_page
from app.makro.network_schema_probe import (
    MakroNetworkProbeError,
    MakroNetworkSchemaProbe,
    assert_safe_makro_listing_url,
    write_probe_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "只读监听 Makro Seller Portal 当前 Step 3 在新标签页加载时发出的 fetch/XHR，"
            "用于发现 Vertical/schema/attribute 内部接口。原 listing 标签页不 reload、不点击、不写字段。"
        )
    )
    parser.add_argument("--cdp-port", type=int, default=9222)
    parser.add_argument("--wait-seconds", type=float, default=12.0)
    parser.add_argument(
        "--output-dir",
        default="logs/makro-schema-network-probe",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.wait_seconds <= 0 or args.wait_seconds > 120:
        raise SystemExit("--wait-seconds 必须在 0..120 秒之间。")
    if not is_cdp_ready(args.cdp_port):
        raise MakroNetworkProbeError(
            f"Makro 长期 Edge CDP 127.0.0.1:{args.cdp_port} 不可达；"
            "probe 不会自动启动或重启浏览器。"
        )

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = Path(args.output_dir) / f"probe-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(cdp_endpoint(args.cdp_port))
        contexts = list(browser.contexts)
        if not contexts:
            raise MakroNetworkProbeError("已连接 Makro Edge，但没有可用 browser context。")
        context = contexts[0]
        source_page = select_listing_page(context)
        source_url = source_page.url
        assert_safe_makro_listing_url(source_url)

        # The probe owns only this newly-created tab. The original listing page
        # is never navigated/reloaded/clicked by this script.
        probe_page = context.new_page()
        probe = MakroNetworkSchemaProbe(probe_page)
        probe.start()
        try:
            probe_page.goto(
                source_url,
                wait_until="domcontentloaded",
                timeout=45_000,
            )
            probe_page.wait_for_timeout(int(args.wait_seconds * 1000))
            report = probe.report(
                source_url=source_url,
                probe_url=probe_page.url,
            )
            report_path = write_probe_report(report, run_dir / "network-schema-report.json")
            probe_page.screenshot(
                path=str(run_dir / "probe-page.png"),
                full_page=True,
            )
            print("===== MAKRO NETWORK SCHEMA PROBE =====")
            print(f"source_page={source_url}")
            print(f"probe_page={probe_page.url}")
            print(f"responses={report['response_count']}")
            print(f"candidates={report['candidate_count']}")
            print(f"report={report_path.resolve()}")
            print("top_endpoints:")
            for item in report["endpoint_groups"][:15]:
                print(
                    f"  score={item['max_schema_score']:>3} "
                    f"count={item['response_count']:>3} "
                    f"{item['method']} {item['endpoint']}"
                )
            print("safety=original page untouched; writes=0; Save=False; Send to QC=False")
        finally:
            probe.stop()
            probe_page.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
