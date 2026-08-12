from __future__ import annotations

import argparse
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

from playwright.sync_api import sync_playwright

from app.browser_session import cdp_endpoint, is_cdp_ready, select_listing_page
from app.makro.listing_creation import _vertical_search_input
from app.makro.network_schema_probe import (
    MakroNetworkProbeError,
    MakroNetworkSchemaProbe,
    assert_safe_makro_listing_url,
    write_probe_report,
)
from app.makro.vertical_selection import is_vertical_interaction_ready


STEP1_URL = "https://seller.makro.co.za/index.html#dashboard/addListings/single"
_VERTICAL_RE = re.compile(r"(?:[?&])vertical=([^&#]+)", re.IGNORECASE)


def extract_canonical_vertical(url: str) -> str:
    match = _VERTICAL_RE.search(str(url or ""))
    return unquote(match.group(1)).strip() if match else ""


def vertical_search_term(canonical_vertical: str) -> str:
    return re.sub(r"\s+", " ", str(canonical_vertical or "").replace("_", " ")).strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "只读探测 Makro Step 1 Vertical Search 的 fetch/XHR。"
            "脚本新开一个 Step 1 标签页，只输入搜索词、不点击任何 Vertical 结果；"
            "原 Step 3 listing 标签页完全不动。"
        )
    )
    parser.add_argument("--cdp-port", type=int, default=9222)
    parser.add_argument("--wait-seconds", type=float, default=5.0)
    parser.add_argument("--step1-ready-timeout", type=float, default=25.0)
    parser.add_argument(
        "--output-dir",
        default="logs/makro-vertical-catalog-probe",
    )
    return parser


def _wait_until_vertical_ready(page, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if is_vertical_interaction_ready(page):
                return True
        except Exception:
            pass
        page.wait_for_timeout(350)
    return False


def main() -> int:
    args = build_parser().parse_args()
    if args.wait_seconds <= 0 or args.wait_seconds > 60:
        raise SystemExit("--wait-seconds 必须在 0..60 秒之间。")
    if args.step1_ready_timeout <= 0 or args.step1_ready_timeout > 120:
        raise SystemExit("--step1-ready-timeout 必须在 0..120 秒之间。")
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

        canonical_vertical = extract_canonical_vertical(source_url)
        if not canonical_vertical:
            raise MakroNetworkProbeError(
                "当前 Step 3 URL 中没有 canonical vertical；拒绝凭空构造 Vertical 搜索词。"
            )
        search_term = vertical_search_term(canonical_vertical)
        if not search_term:
            raise MakroNetworkProbeError("无法从当前 canonical vertical 生成安全搜索词。")

        probe_page = context.new_page()
        probe = MakroNetworkSchemaProbe(probe_page)
        probe.start()
        try:
            probe_page.goto(
                STEP1_URL,
                wait_until="domcontentloaded",
                timeout=45_000,
            )
            if not _wait_until_vertical_ready(probe_page, args.step1_ready_timeout):
                raise MakroNetworkProbeError(
                    "新 Step 1 标签页未在限定时间内进入可操作的 Vertical Search/Taxonomy 状态。"
                )

            search = _vertical_search_input(probe_page)
            search.fill("")
            search.fill(search_term)
            probe_page.wait_for_timeout(int(args.wait_seconds * 1000))
            search.fill("")
            probe_page.wait_for_timeout(1000)

            report = probe.report(
                source_url=source_url,
                probe_url=probe_page.url,
            )
            report["mode"] = "read_only_step1_vertical_search_network_probe"
            report["source_vertical"] = canonical_vertical
            report["search_term"] = search_term
            report["safety"].update(
                {
                    "vertical_search_input_used": True,
                    "vertical_result_clicked": False,
                    "vertical_selected": False,
                    "brand_selected": False,
                    "listing_created": False,
                }
            )
            report_path = write_probe_report(
                report,
                run_dir / "vertical-catalog-network-report.json",
            )
            probe_page.screenshot(
                path=str(run_dir / "step1-probe-page.png"),
                full_page=True,
            )

            print("===== MAKRO VERTICAL CATALOG NETWORK PROBE =====")
            print(f"source_page={source_url}")
            print(f"source_vertical={canonical_vertical}")
            print(f"search_term={search_term}")
            print(f"probe_page={probe_page.url}")
            print(f"responses={report['response_count']}")
            print(f"candidates={report['candidate_count']}")
            print(f"report={report_path.resolve()}")
            print("top_endpoints:")
            for item in report["endpoint_groups"][:20]:
                print(
                    f"  score={item['max_schema_score']:>3} "
                    f"count={item['response_count']:>3} "
                    f"{item['method']} {item['endpoint']}"
                )
            print(
                "safety=original Step3 untouched; Step1 search only; "
                "vertical_result_clicked=False; writes=0; Save=False; Send to QC=False"
            )
        finally:
            probe.stop()
            probe_page.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
