"""Resolve Makro fields from one normalized product input.

The input is either one supplier URL or one customer product pack. Both routes
produce the same grounded evidence catalog before Product Facts, Web Fill and
best-effort inference. This command never opens or modifies Makro.
"""

from __future__ import annotations

import argparse

from app.providers.registry import (
    ProviderConfig,
    ProviderConfigurationError,
    SUPPORTED_PROVIDERS,
)
from app.resolver_pipeline import (
    SUPPLIER_EXECUTION_MODEL,
    cache_namespace,
    dashscope_web_provider,
    decision_summary,
    empty_web_result,
    provider_config,
    run_resolver,
    search_requests,
    set_progress,
)
from app.source_capture import DEFAULT_SOURCE_CDP_PORT


# Compatibility aliases retained for existing tests/tools that imported the old
# CLI helpers directly. Business behavior now lives in app.resolver_pipeline.
EXECUTION_MODEL = SUPPLIER_EXECUTION_MODEL
_provider_config = provider_config
_cache_namespace = cache_namespace
_decision_summary = decision_summary
_search_requests = search_requests
_dashscope_web_provider = dashscope_web_provider
_empty_web_result = empty_web_result
_set_progress = set_progress


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "输入一个供应商商品 URL，或一组客户商品文档/表格/图片；统一形成 grounded evidence，"
            "AI 解析 Makro live fields，仅对仍为空的字段联网补充。不会打开或修改 Makro。"
        )
    )
    parser.add_argument("--provider", choices=SUPPORTED_PROVIDERS, default="openai-compatible")
    parser.add_argument("--model", default="qwen3.7-plus", help="本地商品字段填写模型。")
    parser.add_argument(
        "--fact-model",
        default="qwen3.7-max",
        help="只处理 compact text 的全局事实模型；不接收图片。",
    )
    parser.add_argument("--api-key-env", default=None)
    parser.add_argument("--base-url", default="")
    parser.add_argument(
        "--structured-mode",
        choices=("auto", "prompt_only", "json_object"),
        default="json_object",
        help="本地 Qwen 默认使用原生 JSON mode。",
    )
    thinking = parser.add_mutually_exclusive_group()
    thinking.add_argument("--enable-thinking", dest="enable_thinking", action="store_true")
    thinking.add_argument("--disable-thinking", dest="enable_thinking", action="store_false")
    parser.set_defaults(enable_thinking=False)

    parser.add_argument("--live-schema", required=True, help="read-only Makro planner 导出的 live-schema.json")
    parser.add_argument(
        "--product-url",
        default="",
        help="商品输入模式一：1688 / supplier http(s) 商品 URL。",
    )
    parser.add_argument(
        "--product-file",
        action="append",
        default=[],
        help="商品输入模式二：客户文档/表格/图片，可重复传入。",
    )
    parser.add_argument(
        "--product-pack-manifest",
        default="",
        help="内部复用：已经机械解析完成的 customer product-pack.json。",
    )

    parser.add_argument("--source-profile-dir", default="browser_profiles/source-edge")
    parser.add_argument("--source-cdp-port", type=int, default=DEFAULT_SOURCE_CDP_PORT)
    parser.add_argument("--source-wait-ms", type=int, default=1800)
    parser.add_argument("--source-scroll-wait-ms", type=int, default=180)
    parser.add_argument("--source-max-scroll-steps", type=int, default=120)
    parser.add_argument("--source-max-visible-text-chars", type=int, default=120_000)
    parser.add_argument(
        "--source-use-current-page",
        action="store_true",
        help="URL 模式：source Edge 已人工完成合法登录/验证后，直接采集当前页。",
    )
    parser.add_argument(
        "--source-cache-dir",
        default="logs/source-cache",
        help="URL 模式 source byte cache。",
    )
    parser.add_argument("--source-cache-ttl-seconds", type=int, default=900)
    parser.add_argument("--refresh-source", action="store_true")

    # Optional diagnostics/evidence appended to the selected primary input.
    parser.add_argument("--image", action="append", default=[])
    parser.add_argument("--supplier-snapshot", action="append", default=[])
    parser.add_argument("--official-snapshot", action="append", default=[])

    parser.add_argument("--image-detail", choices=("auto", "low", "high"), default="auto")
    parser.add_argument("--max-output-tokens", type=int, default=12000)
    parser.add_argument("--request-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--max-text-chars", type=int, default=5000)
    parser.add_argument("--overlap-chars", type=int, default=250)

    parser.add_argument("--image-batch-size", type=int, default=3)
    parser.add_argument("--image-concurrency", type=int, default=4)
    parser.add_argument("--local-batch-size", type=int, default=12)
    parser.add_argument("--local-concurrency", type=int, default=4)

    parser.add_argument("--web-enrich", choices=("auto", "off"), default="auto")
    parser.add_argument("--web-search-model", default="qwen3.7-max")
    parser.add_argument("--web-base-url", default="")
    parser.add_argument("--web-batch-size", type=int, default=5)
    parser.add_argument("--web-concurrency", type=int, default=3)

    parser.add_argument(
        "--semantic-cache-dir",
        default="logs/semantic-cache",
        help="本地字段 batch 和 Web Fill batch 的 content-addressed cache。",
    )
    parser.add_argument("--no-semantic-cache", action="store_true")
    parser.add_argument("--output-dir", default="logs/ai-resolver")
    return parser


def main() -> int:
    return run_resolver(build_parser().parse_args())


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProviderConfigurationError as exc:
        raise SystemExit(str(exc)) from exc
