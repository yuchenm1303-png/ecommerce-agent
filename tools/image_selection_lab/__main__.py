from __future__ import annotations

import argparse
import html
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.providers.registry import (
    ProviderConfig,
    build_semantic_provider,
    default_api_key_env,
    validate_provider_config,
)

from .capture import DEFAULT_SOURCE_CDP_PORT, capture_case_from_url
from .core import (
    ImageSelectionLabError,
    STRATEGIES,
    discover_cases,
    import_case_from_selection,
    load_case,
    run_evaluation,
)
from .ground_truth import apply_ground_truth_labels


DEFAULT_ROOT = Path("evals/image_selection")


def _thinking_value(args: argparse.Namespace) -> bool | None:
    if getattr(args, "enable_thinking", False):
        return True
    if getattr(args, "disable_thinking", False):
        return False
    return None


def _provider_config(args: argparse.Namespace) -> ProviderConfig:
    provider = str(args.provider or os.environ.get("AI_PROVIDER") or "openai-compatible").strip()
    model = str(args.model or os.environ.get("AI_MODEL") or "").strip()
    if not model:
        raise ImageSelectionLabError(
            "--model (or AI_MODEL) is required so live and replay cache keys identify the exact model"
        )
    api_key_env = str(args.api_key_env or "").strip() or default_api_key_env(provider)
    base_url = str(args.base_url or os.environ.get("AI_BASE_URL") or "").strip()
    return ProviderConfig(
        provider=provider,
        model=model,
        api_key_env=api_key_env,
        base_url=base_url,
        image_detail=args.image_detail,
        max_output_tokens=args.max_output_tokens,
        structured_mode=args.structured_mode,
        compat_profile=args.compat_profile,
        request_timeout_seconds=args.request_timeout_seconds,
        enable_thinking=_thinking_value(args),
    )


def _build_provider(args: argparse.Namespace) -> tuple[Any | None, dict[str, Any]]:
    config = validate_provider_config(_provider_config(args))
    identity = config.as_safe_dict()
    if args.mode == "replay":
        return None, identity
    return build_semantic_provider(config), identity


def _case_json_path(case_value: str, cases_root: str | Path) -> Path:
    case_arg = Path(case_value)
    return case_arg if case_arg.is_file() else Path(cases_root) / str(case_value) / "case.json"


def _selected_cases(args: argparse.Namespace):
    root = Path(args.cases_root)
    if args.case:
        return [load_case(_case_json_path(str(args.case), root))]
    return discover_cases(root, suite=args.suite)


def _add_provider_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider", default=None, help="openai or openai-compatible; defaults to AI_PROVIDER/openai-compatible")
    parser.add_argument("--model", default=None, help="model id; defaults to AI_MODEL")
    parser.add_argument("--api-key-env", default=None, help="environment variable containing the API key")
    parser.add_argument("--base-url", default=None, help="OpenAI-compatible API root; defaults to AI_BASE_URL")
    parser.add_argument("--image-detail", choices=("auto", "low", "high"), default="auto")
    parser.add_argument("--max-output-tokens", type=int, default=12000)
    parser.add_argument("--structured-mode", choices=("auto", "prompt_only", "json_object"), default="auto")
    parser.add_argument("--compat-profile", choices=("generic", "qwen-omni"), default="generic")
    parser.add_argument("--request-timeout-seconds", type=float, default=120.0)
    thinking = parser.add_mutually_exclusive_group()
    thinking.add_argument("--enable-thinking", action="store_true")
    thinking.add_argument("--disable-thinking", action="store_true")


def _add_run_args(parser: argparse.ArgumentParser, *, strategy_required: bool = True) -> None:
    parser.add_argument("--case", default=None, help="case id or direct case.json path")
    parser.add_argument("--suite", default="all")
    parser.add_argument("--cases-root", default=str(DEFAULT_ROOT / "cases"))
    parser.add_argument("--cache-dir", default=str(DEFAULT_ROOT / "cache"))
    parser.add_argument("--runs-dir", default=str(DEFAULT_ROOT / "runs"))
    parser.add_argument("--mode", choices=("live", "replay"), required=True)
    if strategy_required:
        parser.add_argument("--strategy", choices=tuple(sorted(STRATEGIES)), default="current-v6")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--shuffle", action="store_true")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--refresh-cache", action="store_true", help="live mode only: ignore an existing semantic response cache")
    parser.add_argument("--input-price-per-million", type=float, default=0.0, help="optional CNY price used only for the run report")
    parser.add_argument("--output-price-per-million", type=float, default=0.0, help="optional CNY price used only for the run report")
    _add_provider_args(parser)


def _run_one(args: argparse.Namespace, strategy: str):
    cases = _selected_cases(args)
    provider, provider_identity = _build_provider(args)
    result = run_evaluation(
        cases=cases,
        strategy_name=strategy,
        mode=args.mode,
        delegate_provider=provider,
        provider_identity=provider_identity,
        cache_dir=args.cache_dir,
        runs_dir=args.runs_dir,
        repeat=args.repeat,
        shuffle=args.shuffle,
        seed=args.seed,
        refresh_cache=args.refresh_cache,
        input_price_per_million=args.input_price_per_million,
        output_price_per_million=args.output_price_per_million,
    )
    print(json.dumps(result.aggregate, ensure_ascii=False, indent=2))
    print(f"run_dir={result.run_dir}")
    print(f"report={result.report_path}")
    return result


def _write_compare_report(results: list[Any], runs_dir: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target_dir = runs_dir.resolve() / f"{timestamp}_compare"
    target_dir.mkdir(parents=True, exist_ok=False)
    keys = [
        "safety_gate_pass",
        "safe_gallery_rate",
        "wrong_product_leak_count",
        "variant_leak_count",
        "all_negative_case_pass_rate",
        "main_image_hit_at_1",
        "precision_at_5_micro",
        "recall_at_5_micro",
        "ndcg_at_5_mean",
        "network_calls",
        "cache_hits",
        "latency_seconds",
    ]
    header = "".join(f"<th>{html.escape(result.strategy)}</th>" for result in results)
    rows = []
    for key in keys:
        cells = "".join(
            f"<td>{html.escape(str(result.aggregate.get(key)))}</td>" for result in results
        )
        rows.append(f"<tr><th>{html.escape(key)}</th>{cells}</tr>")
    links = "".join(
        f"<li>{html.escape(result.strategy)}: <a href='{html.escape(result.report_path.as_uri())}'>report</a></li>"
        for result in results
    )
    document = (
        "<!doctype html><meta charset='utf-8'><title>Image Selection Compare</title>"
        "<style>body{font-family:system-ui;margin:28px}table{border-collapse:collapse}th,td{border:1px solid #ddd;padding:8px 12px}</style>"
        f"<h1>Image Selection Strategy Compare</h1><table><tr><th>metric</th>{header}</tr>{''.join(rows)}</table><ul>{links}</ul>"
    )
    report = target_dir / "compare.html"
    report.write_text(document, encoding="utf-8")
    (target_dir / "compare.json").write_text(
        json.dumps(
            {result.strategy: result.aggregate for result in results},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return report


def _cmd_run(args: argparse.Namespace) -> int:
    _run_one(args, args.strategy)
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    names = [name.strip() for name in args.strategies.split(",") if name.strip()]
    if len(names) < 2:
        raise ImageSelectionLabError("compare requires at least two comma-separated strategies")
    unknown = [name for name in names if name not in STRATEGIES]
    if unknown:
        raise ImageSelectionLabError(f"unknown strategies: {unknown}")
    results = [_run_one(args, name) for name in names]
    report = _write_compare_report(results, Path(args.runs_dir))
    print(f"comparison_report={report}")
    return 0


def _cmd_import_case(args: argparse.Namespace) -> int:
    path = import_case_from_selection(
        selection_json=args.selection_json,
        cases_root=args.cases_root,
        case_id=args.case_id,
    )
    print(f"created={path}")
    print("ground_truth is intentionally blank; label every candidate before running the case")
    return 0


def _cmd_capture_case(args: argparse.Namespace) -> int:
    path = capture_case_from_url(
        url=args.url,
        cases_root=args.cases_root,
        case_id=args.case_id,
        profile_dir=args.profile_dir,
        cdp_port=args.cdp_port,
        initial_wait_ms=args.wait_ms,
        scroll_wait_ms=args.scroll_wait_ms,
        max_scroll_steps=args.max_scroll_steps,
        max_visible_text_chars=args.max_visible_text_chars,
        use_current_page=args.use_current_page,
        target_name=args.target_name,
        target_brand=args.target_brand,
        target_model=args.target_model,
        target_variant=args.target_variant,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    print(f"created={path}")
    print(f"candidate_count={len(payload.get('candidates') or [])}")
    print("ai_calls=0")
    print("listing_actions=0")
    print("ground_truth is intentionally blank; label every candidate before running AI evaluation")
    return 0


def _cmd_apply_ground_truth(args: argparse.Namespace) -> int:
    case_path = _case_json_path(str(args.case), args.cases_root)
    path = apply_ground_truth_labels(case_json=case_path, labels_json=args.labels)
    payload = json.loads(path.read_text(encoding="utf-8"))
    print(f"labelled={path}")
    print(f"candidate_count={len(payload.get('candidates') or [])}")
    print(f"label_status={payload.get('label_status')}")
    print("ai_calls=0")
    print("listing_actions=0")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.image_selection_lab",
        description="Local benchmark harness for supplier Product Photos selection.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="run one strategy against local labelled cases")
    _add_run_args(run_parser)
    run_parser.set_defaults(handler=_cmd_run)

    compare_parser = sub.add_parser("compare", help="run the same cases through multiple strategies")
    _add_run_args(compare_parser, strategy_required=False)
    compare_parser.add_argument("--strategies", required=True, help="comma-separated strategy names")
    compare_parser.set_defaults(handler=_cmd_compare)

    import_parser = sub.add_parser("import-case", help="copy one production selection report into a local unlabelled regression case")
    import_parser.add_argument("--selection-json", required=True)
    import_parser.add_argument("--case-id", required=True)
    import_parser.add_argument("--cases-root", default=str(DEFAULT_ROOT / "cases"))
    import_parser.set_defaults(handler=_cmd_import_case)

    capture_parser = sub.add_parser(
        "capture-case",
        help="capture a real product page with the production source collector, without AI or listing actions",
    )
    capture_parser.add_argument("--url", required=True, help="real product-page URL")
    capture_parser.add_argument("--case-id", required=True)
    capture_parser.add_argument("--cases-root", default=str(DEFAULT_ROOT / "cases"))
    capture_parser.add_argument("--profile-dir", default="browser_profiles/source-edge")
    capture_parser.add_argument("--cdp-port", type=int, default=DEFAULT_SOURCE_CDP_PORT)
    capture_parser.add_argument("--wait-ms", type=int, default=1800)
    capture_parser.add_argument("--scroll-wait-ms", type=int, default=180)
    capture_parser.add_argument("--max-scroll-steps", type=int, default=120)
    capture_parser.add_argument("--max-visible-text-chars", type=int, default=120_000)
    capture_parser.add_argument("--use-current-page", action="store_true")
    capture_parser.add_argument("--target-name", default="", help="optional human-provided exact product name")
    capture_parser.add_argument("--target-brand", default="", help="optional human-provided exact brand")
    capture_parser.add_argument("--target-model", default="", help="optional human-provided exact model/SKU family")
    capture_parser.add_argument("--target-variant", default="", help="optional human-provided exact variant")
    capture_parser.set_defaults(handler=_cmd_capture_case)

    label_parser = sub.add_parser(
        "apply-ground-truth",
        help="apply one reviewed ground-truth label set to an exact local capture-case without AI",
    )
    label_parser.add_argument("--case", required=True, help="case id or direct case.json path")
    label_parser.add_argument("--labels", required=True, help="reviewed ground-truth JSON")
    label_parser.add_argument("--cases-root", default=str(DEFAULT_ROOT / "cases"))
    label_parser.set_defaults(handler=_cmd_apply_ground_truth)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except ImageSelectionLabError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
