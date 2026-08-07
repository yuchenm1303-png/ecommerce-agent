from __future__ import annotations

import argparse
from pathlib import Path

from app.data_loader import load_products
from app.platforms.mock import MockPlatformAdapter
from app.runner import AutomationRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="电商卖家后台批量自动填写原型")
    parser.add_argument(
        "--data",
        default="data/products.csv",
        help="商品数据表路径，支持 CSV/XLSX/XLSM",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="卖家后台地址；当前默认连接本地 mock 站点",
    )
    parser.add_argument("--headless", action="store_true", help="无头模式运行浏览器")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="填写并校验，但不点击最终保存按钮",
    )
    parser.add_argument("--limit", type=int, default=None, help="仅处理前 N 个商品")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    products = load_products(Path(args.data))
    if args.limit is not None:
        if args.limit <= 0:
            raise ValueError("--limit 必须大于 0")
        products = products[: args.limit]

    print(f"已读取 {len(products)} 个商品。")
    print("安全策略：商品身份不一致、必填字段未匹配、填写后校验失败时，均不会保存。")

    adapter = MockPlatformAdapter(args.base_url)
    runner = AutomationRunner(
        adapter,
        headless=args.headless,
        dry_run=args.dry_run,
    )
    summary = runner.run(products)

    print("\n执行完成：")
    for key, value in summary.items():
        print(f"  {key}: {value}")
    print(f"日志：{runner.log_path}")
    return 0 if summary.get("error", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
