"""Capture a supplier/official product page into a deterministic source snapshot.

A separate long-lived Edge profile/port is used so supplier browsing can never
navigate the Makro seller session. The command does not bypass CAPTCHA or site
risk controls. If verification is detected, it stops and leaves Edge open for
legitimate manual completion.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from app.browser_session import cdp_endpoint, is_cdp_ready, launch_detached_edge
from app.source_snapshot import SourceAccessBlocked, capture_page_snapshot, write_source_snapshot


DEFAULT_SOURCE_CDP_PORT = 9333


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="用独立 Edge 捕获供应商/官网页面的可见文本、表格和 JSON-LD；不绕过验证。"
    )
    parser.add_argument("--url", required=True, help="供应商/官网商品 URL")
    parser.add_argument("--profile-dir", default="browser_profiles/source-edge")
    parser.add_argument("--cdp-port", type=int, default=DEFAULT_SOURCE_CDP_PORT)
    parser.add_argument("--wait-ms", type=int, default=1800)
    parser.add_argument("--max-visible-text-chars", type=int, default=120_000)
    parser.add_argument("--output-dir", default="logs/source-capture")
    parser.add_argument(
        "--use-current-page",
        action="store_true",
        help="不重新导航；用于你已在同一 source Edge 中人工完成合法登录/验证后重新采集。",
    )
    return parser


def _validate_url(value: str) -> str:
    url = value.strip()
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("--url 必须是完整 http/https URL。")
    return url


def _connect_source_edge(playwright, *, profile_dir: Path, port: int, start_url: str):
    launched_now = not is_cdp_ready(port)
    if launched_now:
        launch_detached_edge(
            profile_dir=profile_dir,
            port=port,
            start_url=start_url,
        )
    browser = playwright.chromium.connect_over_cdp(cdp_endpoint(port))
    contexts = list(browser.contexts)
    if not contexts:
        raise RuntimeError("已连接 source Edge，但没有 browser context。")
    context = contexts[0]
    pages = list(context.pages)
    page = pages[-1] if pages else context.new_page()
    return browser, context, page, launched_now


def main() -> int:
    args = build_parser().parse_args()
    url = _validate_url(args.url)
    if args.wait_ms < 0:
        raise SystemExit("--wait-ms 不能为负数。")
    if args.max_visible_text_chars < 1_000:
        raise SystemExit("--max-visible-text-chars 不能小于 1000。")

    output_root = Path(args.output_dir)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = output_root / f"capture-{stamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        _, _, page, launched_now = _connect_source_edge(
            playwright,
            profile_dir=Path(args.profile_dir).resolve(),
            port=args.cdp_port,
            start_url=url,
        )
        page.set_default_timeout(15_000)

        if args.use_current_page:
            if page.url in {"", "about:blank"}:
                raise RuntimeError("--use-current-page 时当前 source Edge 没有已打开的网页。")
        else:
            # This browser/profile is dedicated to source research, not Makro.
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)

        page.wait_for_timeout(args.wait_ms)
        requested_url = url
        try:
            snapshot = capture_page_snapshot(
                page,
                requested_url=requested_url,
                max_visible_text_chars=args.max_visible_text_chars,
            )
        except SourceAccessBlocked as exc:
            print("===== SOURCE ACCESS PAUSED =====")
            print(str(exc))
            print(f"source Edge CDP=127.0.0.1:{args.cdp_port}")
            print("浏览器保持打开。请只在网站正常界面中人工完成合法验证，之后用 --use-current-page 重试。")
            return 2

        snapshot_path = write_source_snapshot(snapshot, output_dir / "source-snapshot.json")
        screenshot_path = output_dir / "source-page.png"
        page.screenshot(path=str(screenshot_path), full_page=True)

        print("===== SOURCE CAPTURE READY =====")
        print(f"source_edge={'new' if launched_now else 'reused'}")
        print(f"final_url={snapshot.final_url}")
        print(f"table_rows={len(snapshot.table_rows)}")
        print(f"json_ld_blocks={len(snapshot.json_ld)}")
        print(f"visible_text_chars={len(snapshot.visible_text)}")
        print(f"snapshot={snapshot_path.resolve()}")
        print(f"screenshot={screenshot_path.resolve()}")
        print("没有绕过 CAPTCHA/风控；source Edge 保持打开。")

        # CDP connection is released when Python exits; do not close the external
        # browser/context because subsequent captures should reuse login state.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
