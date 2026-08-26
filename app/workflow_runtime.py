"""Canonical runtime helpers shared by Makro workflow entrypoints.

These helpers own mechanical schema scanning, child-process invocation, and
command construction. They contain no product semantics and no browser stage
state machine.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.live_schema import write_live_schema
from app.makro.direct_visual_hold import is_listing_attribute_field
from app.makro.domain import MakroDomainAdapter
from app.makro.listing_draft_identity import listing_draft_identity_from_url

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def scan_and_write_live_schema(
    adapter: MakroDomainAdapter,
    target: Path,
    *,
    wait_ms: int,
    max_scroll_steps: int,
) -> tuple[Path, dict[str, Any]]:
    sections, controls, scan = adapter.scan_sections(
        include_values=False,
        wait_ms=wait_ms,
        max_scroll_steps=max_scroll_steps,
    )
    all_fields = adapter.build_semantic_fields(controls)
    fields = [field for field in all_fields if is_listing_attribute_field(field)]
    if not fields:
        raise RuntimeError("Step 3 live schema scan returned zero listing attribute fields")
    draft_identity = listing_draft_identity_from_url(adapter.page.url)
    path = write_live_schema(
        fields,
        target,
        listing_draft_identity=draft_identity,
    )
    return path, {
        "listing_attribute_fields": len(fields),
        "semantic_fields_before_filter": len(all_fields),
        "sections": [item.get("title") for item in sections],
        "scan": scan,
        "listing_draft_identity_bound": True,
    }


def append_option(command: list[str], name: str, value: Any) -> None:
    if value is None:
        return
    text = str(value).strip()
    if text:
        command.extend([name, text])


def run_command(command: list[str], label: str) -> None:
    print(f"\n===== {label} =====", flush=True)
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"{label} failed with exit code {result.returncode}")


def single_run_dir(root: Path, prefix: str) -> Path:
    matches = sorted(path for path in root.glob(f"{prefix}*") if path.is_dir())
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one {prefix}* directory under {root}, got {len(matches)}")
    return matches[0]


def build_resolver_command(
    args: Any,
    live_schema: Path,
    resolver_root: Path,
    *,
    script_path: str | Path | None = None,
) -> list[str]:
    resolver_script = Path(script_path) if script_path is not None else _PROJECT_ROOT / "makro_resolve_ai.py"
    command = [
        sys.executable,
        str(resolver_script),
        "--provider",
        args.provider,
        "--model",
        args.model,
        "--fact-model",
        args.fact_model,
        "--structured-mode",
        args.structured_mode,
        "--request-timeout-seconds",
        str(args.request_timeout_seconds),
        "--live-schema",
        str(live_schema),
        "--product-url",
        args.product_url,
        "--source-profile-dir",
        args.source_profile_dir,
        "--source-cdp-port",
        str(args.source_cdp_port),
        "--source-wait-ms",
        str(args.source_wait_ms),
        "--source-scroll-wait-ms",
        str(args.source_scroll_wait_ms),
        "--source-max-scroll-steps",
        str(args.source_max_scroll_steps),
        "--source-max-visible-text-chars",
        str(args.source_max_visible_text_chars),
        "--source-cache-dir",
        args.source_cache_dir,
        "--source-cache-ttl-seconds",
        str(args.source_cache_ttl_seconds),
        "--image-batch-size",
        str(args.image_batch_size),
        "--image-concurrency",
        str(args.image_concurrency),
        "--local-batch-size",
        str(args.local_batch_size),
        "--local-concurrency",
        str(args.local_concurrency),
        "--web-enrich",
        args.web_enrich,
        "--web-search-model",
        args.web_search_model,
        "--web-batch-size",
        str(args.web_batch_size),
        "--web-concurrency",
        str(args.web_concurrency),
        "--semantic-cache-dir",
        args.semantic_cache_dir,
        "--output-dir",
        str(resolver_root),
    ]
    append_option(command, "--api-key-env", args.api_key_env)
    append_option(command, "--base-url", args.base_url)
    append_option(command, "--web-base-url", args.web_base_url)
    if args.provider == "openai-compatible":
        command.append("--enable-thinking" if args.enable_thinking else "--disable-thinking")
    if args.no_semantic_cache:
        command.append("--no-semantic-cache")
    return command


def build_executor_command(
    args: Any,
    *,
    live_schema: Path,
    creation_vertical: str,
    resolver_manifest: dict[str, Any],
    executor_root: Path,
    script_path: str | Path | None = None,
) -> list[str]:
    outputs = resolver_manifest.get("outputs") or {}
    decision_packet = str(outputs.get("final_decisions") or "")
    snapshot = str(outputs.get("primary_source_snapshot") or "")
    product_images = [
        str(item)
        for item in outputs.get("primary_source_product_images") or []
        if str(item)
    ]
    screenshot = str(outputs.get("primary_source_screenshot") or "")
    evidence_images = product_images or ([screenshot] if screenshot else [])
    if not decision_packet or not snapshot or not evidence_images:
        raise RuntimeError("Resolver manifest is missing final decisions / source snapshot / evidence images")

    product_url = str(resolver_manifest.get("primary_product_url") or args.product_url)
    executor_script = Path(script_path) if script_path is not None else _PROJECT_ROOT / "makro_execute_listing.py"
    command = [
        sys.executable,
        str(executor_script),
        "--decision-packet",
        decision_packet,
        "--live-schema",
        str(live_schema),
        "--product-url",
        product_url,
        "--supplier-snapshot",
        snapshot,
        "--expected-vertical",
        creation_vertical,
        "--all-step3",
        "--allow-section-save",
        "--profile-dir",
        args.profile_dir,
        "--cdp-port",
        str(args.cdp_port),
        "--scroll-wait-ms",
        str(args.scroll_wait_ms),
        "--max-scroll-steps",
        str(args.max_scroll_steps),
        "--output-dir",
        str(executor_root),
    ]
    for image in evidence_images:
        command.extend(["--image", image])
    uploads = list(args.upload_image)
    if args.upload_source_photos:
        uploads.extend(product_images[:5])
    seen: set[str] = set()
    for image in uploads:
        normalized = os.path.normcase(os.path.abspath(str(image)))
        if normalized in seen:
            continue
        seen.add(normalized)
        command.extend(["--upload-image", str(image)])
    if args.include_review_candidates:
        command.append("--include-review-candidates")
    return command


__all__ = [
    "append_option",
    "build_executor_command",
    "build_resolver_command",
    "run_command",
    "scan_and_write_live_schema",
    "single_run_dir",
]
