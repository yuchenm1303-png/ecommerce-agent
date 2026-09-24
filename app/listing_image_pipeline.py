from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .listing_image_ranker import ListingImageRankingResult, finalize_supplier_listing_images
from .makro.image_requirements import inspect_listing_image, listing_image_resolution_error


LISTING_IMAGE_PIPELINE_VERSION = 1
LISTING_IMAGE_AI_CACHE_VERSION = 1


@dataclass(slots=True, frozen=True)
class ListingImagePipelineResult:
    status: str
    selected: tuple[Path, ...]
    model_calls: int
    cache_hits: int
    request_count: int
    mechanical_candidate_count: int
    mechanically_rejected_count: int
    semantically_rejected_count: int
    selection_report: Path | None


class _ContentAddressedJSONProvider:
    """Cache semantic image requests by meaning/content rather than temp paths."""

    def __init__(self, provider: Any, cache_dir: str | Path | None) -> None:
        self._provider = provider
        self.name = str(getattr(provider, "name", ""))
        self.model = str(getattr(provider, "model", ""))
        self._cache_dir = Path(cache_dir).expanduser().resolve() if cache_dir else None
        self.model_calls = 0
        self.cache_hits = 0
        self.request_count = 0

    @staticmethod
    def _normalize(value: Any) -> Any:
        if isinstance(value, list):
            return [_ContentAddressedJSONProvider._normalize(item) for item in value]
        if not isinstance(value, dict):
            return value

        normalized: dict[str, Any] = {}
        image_digest = str(value.get("sha256") or "").strip().casefold()
        is_image = str(value.get("kind") or "").strip().casefold() == "image"
        for key, item in value.items():
            if is_image and image_digest and key in {"origin", "image_path"}:
                normalized[key] = f"sha256:{image_digest}"
            else:
                normalized[key] = _ContentAddressedJSONProvider._normalize(item)
        return normalized

    def _cache_path(self, request_payload: dict[str, Any]) -> Path | None:
        if self._cache_dir is None:
            return None
        canonical = {
            "cache_version": LISTING_IMAGE_AI_CACHE_VERSION,
            "provider": self.name,
            "model": self.model,
            "request": self._normalize(request_payload),
        }
        raw = json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        key = hashlib.sha256(raw).hexdigest()
        return self._cache_dir / "listing-images" / f"{key}.json"

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        self.request_count += 1
        cache_path = self._cache_path(request_payload)
        if cache_path is not None and cache_path.is_file():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if isinstance(cached, dict):
                    self.cache_hits += 1
                    return cached
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass

        self.model_calls += 1
        raw = self._provider.extract_json(request_payload)
        if not isinstance(raw, dict):
            return raw
        if cache_path is not None:
            self._write_json_atomic(cache_path, raw)
        return raw


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _filter_marketplace_images(raw_images: list[object]) -> tuple[list[str], list[dict[str, Any]]]:
    eligible: list[str] = []
    rejected: list[dict[str, Any]] = []
    for source_index, raw in enumerate(raw_images, start=1):
        path = Path(str(raw or "")).expanduser().resolve()
        try:
            inspection = inspect_listing_image(path)
        except ValueError as exc:
            # Decodability/missing-file ownership remains visible here so failures
            # never reach semantic AI merely because a source manifest listed them.
            rejected.append(
                {
                    "source_index": source_index,
                    "path": str(path),
                    "reason": "marketplace_image_unreadable",
                    "detail": str(exc),
                }
            )
            continue
        resolution_error = listing_image_resolution_error(inspection)
        if resolution_error:
            rejected.append(
                {
                    "source_index": source_index,
                    "path": str(path),
                    "reason": "below_marketplace_minimum_resolution",
                    "width": inspection.width,
                    "height": inspection.height,
                    "detail": resolution_error,
                }
            )
            continue
        eligible.append(str(path))
    return eligible, rejected


def _augment_selection_report(
    selection_path: Path | None,
    mechanical_rejected: list[dict[str, Any]],
    cache_provider: _ContentAddressedJSONProvider,
) -> None:
    if selection_path is None or not selection_path.is_file():
        return
    try:
        report = _read_json(selection_path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return
    report["marketplace_mechanical_rejected"] = list(mechanical_rejected)
    policy = report.get("policy")
    if isinstance(policy, dict):
        policy["marketplace_resolution_gate"] = "minimum_300x300_before_semantic_ranking"
        report["policy"] = policy
    report["semantic_cache"] = {
        "version": LISTING_IMAGE_AI_CACHE_VERSION,
        "request_count": cache_provider.request_count,
        "cache_hits": cache_provider.cache_hits,
        "model_calls": cache_provider.model_calls,
    }
    _write_json_atomic(selection_path, report)


def run_supplier_listing_image_pipeline(
    run_dir: str | Path,
    provider: Any,
    *,
    cache_dir: str | Path | None = None,
) -> ListingImagePipelineResult:
    """Run mechanical Makro image eligibility, semantic ownership and cached ranking.

    Product semantics remain wholly owned by ``listing_image_ranker``. This layer
    owns only two mechanical concerns that must be shared across cold/hot runs:
    marketplace upload eligibility and content-addressed reuse of identical AI
    requests. A hot resolver therefore reuses the exact cold semantic answers
    instead of paying again or producing a second, inconsistent gallery.
    """

    root = Path(run_dir).expanduser().resolve()
    manifest_path = root / "run-manifest.json"
    manifest = _read_json(manifest_path)
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise ValueError("resolver manifest outputs must be an object")

    raw_images = outputs.get("primary_source_product_images") or []
    if not isinstance(raw_images, list):
        raise ValueError("primary_source_product_images must be an array")
    eligible_images, mechanical_rejected = _filter_marketplace_images(raw_images)

    workspace = root / ".listing-image-ranking"
    workspace.mkdir(parents=True, exist_ok=True)
    workspace_manifest_path = workspace / "run-manifest.json"
    workspace_manifest = json.loads(json.dumps(manifest, ensure_ascii=False))
    workspace_outputs = workspace_manifest.get("outputs") or {}
    workspace_outputs["primary_source_product_images"] = eligible_images
    workspace_manifest["outputs"] = workspace_outputs
    _write_json_atomic(workspace_manifest_path, workspace_manifest)

    cached_provider = _ContentAddressedJSONProvider(provider, cache_dir)
    original_total_calls = int(manifest.get("total_model_calls") or 0)
    rank_result: ListingImageRankingResult = finalize_supplier_listing_images(
        workspace,
        cached_provider,
    )

    ranked_manifest = _read_json(workspace_manifest_path)
    ranked_outputs = ranked_manifest.get("outputs") or {}
    if not isinstance(ranked_outputs, dict):
        raise ValueError("ranked resolver manifest outputs must be an object")

    outputs["primary_source_listing_images"] = list(
        ranked_outputs.get("primary_source_listing_images") or []
    )
    outputs["primary_source_listing_image_selection"] = ranked_outputs.get(
        "primary_source_listing_image_selection"
    )
    manifest["outputs"] = outputs

    ranked_source_capture = ranked_manifest.get("source_capture")
    if isinstance(ranked_source_capture, dict):
        source_capture = manifest.get("source_capture")
        if not isinstance(source_capture, dict):
            source_capture = {}
        for key in (
            "listing_image_policy_version",
            "listing_images_selected",
            "listing_images",
        ):
            if key in ranked_source_capture:
                source_capture[key] = ranked_source_capture[key]
        source_capture["listing_images_rejected"] = max(
            0,
            len(raw_images) - len(outputs.get("primary_source_listing_images") or []),
        )
        manifest["source_capture"] = source_capture

    ranking_state = ranked_manifest.get("listing_image_ranking")
    if isinstance(ranking_state, dict):
        ranking_state = dict(ranking_state)
        ranking_state["pipeline_version"] = LISTING_IMAGE_PIPELINE_VERSION
        ranking_state["model_calls"] = cached_provider.model_calls
        ranking_state["cache_hits"] = cached_provider.cache_hits
        ranking_state["request_count"] = cached_provider.request_count
        ranking_state["marketplace_mechanical_rejected_count"] = len(mechanical_rejected)
        manifest["listing_image_ranking"] = ranking_state

    manifest["total_model_calls"] = original_total_calls + cached_provider.model_calls
    _write_json_atomic(manifest_path, manifest)
    _augment_selection_report(
        rank_result.selection_report,
        mechanical_rejected,
        cached_provider,
    )

    return ListingImagePipelineResult(
        status=rank_result.status,
        selected=rank_result.selected,
        model_calls=cached_provider.model_calls,
        cache_hits=cached_provider.cache_hits,
        request_count=cached_provider.request_count,
        mechanical_candidate_count=rank_result.mechanical_candidate_count,
        mechanically_rejected_count=len(mechanical_rejected),
        semantically_rejected_count=rank_result.semantically_rejected_count,
        selection_report=rank_result.selection_report,
    )


__all__ = [
    "LISTING_IMAGE_AI_CACHE_VERSION",
    "LISTING_IMAGE_PIPELINE_VERSION",
    "ListingImagePipelineResult",
    "run_supplier_listing_image_pipeline",
]
