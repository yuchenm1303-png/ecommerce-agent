"""Shared CLI contract for Makro listing workflows.

This module owns parser/config construction that is shared by Single, GUI and
Batch entrypoints. It contains no browser workflow or product semantics.
"""

from __future__ import annotations

import argparse

from app.browser_session import DEFAULT_CDP_PORT
from app.providers.registry import (
    ProviderConfig,
    SUPPORTED_PROVIDERS,
    default_api_key_env,
    validate_provider_config,
)
from app.source_capture import DEFAULT_SOURCE_CDP_PORT


WORKFLOW_MODES = ("step1", "step2", "step3", "full")


def build_one_link_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "只给一个供应商商品链接：自动完成 Makro Step 1 Vertical、Step 2 Brand，"
            "动态扫描 Step 3 schema，再复用现有 Resolver/Executor 自动填写。永不 Send to QC。"
        )
    )
    parser.add_argument("--product-url", required=True)

    parser.add_argument("--provider", choices=SUPPORTED_PROVIDERS, default="openai-compatible")
    parser.add_argument("--model", default="qwen3.7-plus")
    parser.add_argument("--fact-model", default="qwen3.7-max")
    parser.add_argument("--api-key-env", default=None)
    parser.add_argument("--base-url", default="")
    parser.add_argument(
        "--structured-mode",
        choices=("auto", "prompt_only", "json_object"),
        default="json_object",
    )
    thinking = parser.add_mutually_exclusive_group()
    thinking.add_argument("--enable-thinking", dest="enable_thinking", action="store_true")
    thinking.add_argument("--disable-thinking", dest="enable_thinking", action="store_false")
    parser.set_defaults(enable_thinking=False)
    parser.add_argument("--request-timeout-seconds", type=float, default=120.0)

    parser.add_argument(
        "--vertical",
        default="",
        help="诊断覆盖；默认由供应商资料 + Makro live candidates 自动确定。",
    )
    parser.add_argument(
        "--brand",
        default="",
        help="诊断覆盖；默认使用统一生产 Brand 策略（当前 KEAI），覆盖值仍必须通过 Makro Check Brand 验证。",
    )

    parser.add_argument("--profile-dir", default="browser_profiles/makro-edge")
    parser.add_argument("--cdp-port", type=int, default=DEFAULT_CDP_PORT)
    parser.add_argument("--scroll-wait-ms", type=int, default=250)
    parser.add_argument("--max-scroll-steps", type=int, default=200)

    parser.add_argument("--source-profile-dir", default="browser_profiles/source-edge")
    parser.add_argument("--source-cdp-port", type=int, default=DEFAULT_SOURCE_CDP_PORT)
    parser.add_argument("--source-wait-ms", type=int, default=1800)
    parser.add_argument("--source-scroll-wait-ms", type=int, default=180)
    parser.add_argument("--source-max-scroll-steps", type=int, default=120)
    parser.add_argument("--source-max-visible-text-chars", type=int, default=120_000)
    parser.add_argument("--source-use-current-page", action="store_true")
    parser.add_argument("--source-cache-dir", default="logs/source-cache")
    parser.add_argument("--source-cache-ttl-seconds", type=int, default=900)
    parser.add_argument("--refresh-source", action="store_true")

    parser.add_argument("--image-batch-size", type=int, default=3)
    parser.add_argument("--image-concurrency", type=int, default=4)
    parser.add_argument("--local-batch-size", type=int, default=12)
    parser.add_argument("--local-concurrency", type=int, default=4)
    parser.add_argument("--web-enrich", choices=("auto", "off"), default="auto")
    parser.add_argument("--web-search-model", default="qwen3.7-max")
    parser.add_argument("--web-base-url", default="")
    parser.add_argument("--web-batch-size", type=int, default=5)
    parser.add_argument("--web-concurrency", type=int, default=3)
    parser.add_argument("--semantic-cache-dir", default="logs/semantic-cache")
    parser.add_argument("--no-semantic-cache", action="store_true")

    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="只完成 Step 1/2 + live schema + Resolver；不执行 Step 3 浏览器写入。",
    )
    parser.add_argument(
        "--allow-section-save",
        action="store_true",
        help="真实 Step 3 执行必需安全开关；允许现有 executor 逐 section Save。",
    )
    parser.add_argument("--include-review-candidates", action="store_true")
    parser.add_argument(
        "--upload-source-photos",
        action="store_true",
        help="把 Resolver 捕获的前 5 张 source product images 交给现有 Product Photos uploader。默认关闭。",
    )
    parser.add_argument("--upload-image", action="append", default=[])
    parser.add_argument("--output-dir", default="logs/makro-one-link")
    return parser


def build_staged_workflow_parser() -> argparse.ArgumentParser:
    parser = build_one_link_parser()
    parser.description = "GUI staged acceptance runner backed by the current Makro one-link implementation."
    parser.add_argument("--mode", choices=WORKFLOW_MODES, required=True)
    parser.add_argument(
        "--resume-current-url",
        default="",
        help=(
            "GUI immediate-retry token. Full mode may resume only the exact unique Step 2/3 "
            "page URL recorded by the immediately preceding failed run."
        ),
    )
    return parser


def provider_config(args: argparse.Namespace) -> ProviderConfig:
    api_key_env = args.api_key_env or default_api_key_env(args.provider)
    thinking = None if args.provider == "openai" else args.enable_thinking
    return validate_provider_config(
        ProviderConfig(
            provider=args.provider,
            model=args.model,
            api_key_env=api_key_env,
            base_url=args.base_url,
            structured_mode=args.structured_mode,
            request_timeout_seconds=args.request_timeout_seconds,
            enable_thinking=thinking,
        )
    )


__all__ = [
    "WORKFLOW_MODES",
    "build_one_link_parser",
    "build_staged_workflow_parser",
    "provider_config",
]
