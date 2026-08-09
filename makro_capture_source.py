"""Capture one supplier/official product page into resolver-ready local evidence.

Uses a dedicated source Edge profile/port and never touches Makro. CAPTCHA/risk
controls are not bypassed; if verification appears the source browser is left for
legitimate manual completion.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from app.source_capture import (
    DEFAULT_SOURCE_CDP_PORT,
    SourceAccessBlocked,
    capture_product_source,
    validate_source_url,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="用独立 Edge 自动滚动并捕获供应商/官网商品页的文本、表格和整页图片。"
    )
    parser.add_argument("--url", required=True, help="供应商/官网商品 URL")
    parser.add_argument("--profile-dir", default="browser_profiles/source-edge")
    parser.add_argument("--cdp-port", type=int, default=DEFAULT_SOURCE_CDP_PORT)
    parser.add_argument("--wait-ms", type=int, default=1800)
    parser.add_argument("--scroll-wait-ms", type=int, default=180)
    parser.add_argument("--max-scroll-steps", type=int, default=120)
    parser.add_argument("--max-visible-text-chars", type=int, default=120_000)
    parser.add_argument("--output-dir", default="logs/source-capture")
    parser.add_argument(
        "--use-current-page",
        action="store_true",
        help="不重新导航；用于 source Edge 中已经人工完成合法登录/验证后重新采集。",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    url = validate_source_url(args.url)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.output_dir) / f"capture-{stamp}"

    try:
        captured = capture_product_source(
            url,
            output_dir=output_dir,
            profile_dir=args.profile_dir,
            cdp_port=args.cdp_port,
            initial_wait_ms=args.wait_ms,
            scroll_wait_ms=args.scroll_wait_ms,
            max_scroll_steps=args.max_scroll_steps,
            max_visible_text_chars=args.max_visible_text_chars,
            use_current_page=args.use_current_page,
        )
    except SourceAccessBlocked as exc:
        print("===== SOURCE ACCESS PAUSED =====")
        print(str(exc))
        print(f"source Edge CDP=127.0.0.1:{args.cdp_port}")
        print("请只在网站正常界面中人工完成合法验证，之后用 --use-current-page 重试。")
        return 2

    snapshot = captured.snapshot
    print("===== SOURCE CAPTURE READY =====")
    print(f"source_edge={'new' if captured.launched_now else 'reused'}")
    print(f"final_url={snapshot.final_url}")
    print(f"table_rows={len(snapshot.table_rows)}")
    print(f"json_ld_blocks={len(snapshot.json_ld)}")
    print(f"visible_text_chars={len(snapshot.visible_text)}")
    print(f"snapshot={captured.snapshot_path.resolve()}")
    print(f"screenshot={captured.screenshot_path.resolve()}")
    print("没有绕过 CAPTCHA/风控；source Edge 保持打开。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
