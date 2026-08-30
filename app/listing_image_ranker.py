from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .image_media import has_decodable_image_pixels
from .providers.usage_telemetry import usage_request_context
from .source_snapshot import source_snapshot_from_json


LISTING_IMAGE_RANKING_VERSION = 4
MAX_AUTO_LISTING_IMAGES = 5


class ListingImageRankingError(RuntimeError):
    pass


class JSONTaskProvider(Protocol):
    name: str

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass(slots=True, frozen=True)
class _Candidate:
    image_id: str
    path: Path
    sha256: str
    source_index: int

    def as_source(self) -> dict[str, str]:
        return {
            "source_id": self.image_id,
            "source_type": "supplier_listing_image_candidate",
            "kind": "image",
            "origin": str(self.path),
            "image_path": str(self.path),
            "sha256": self.sha256,
        }


@dataclass(slots=True, frozen=True)
class RankedImageDecision:
    image_id: str
    selected: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "image_id": self.image_id,
            "selected": self.selected,
            "reason": self.reason,
        }


@dataclass(slots=True, frozen=True)
class ListingImageRankingResult:
    status: str
    selected: tuple[Path, ...]
    model_calls: int
    mechanical_candidate_count: int
    semantically_rejected_count: int
    selection_report: Path | None


@dataclass(slots=True, frozen=True)
class _ParsedRanking:
    selected_ids: tuple[str, ...]
    decisions: dict[str, RankedImageDecision]
    summary: str


def _compact_text(value: object, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[: max(0, int(limit))]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _customer_owned_image_paths(outputs: dict[str, Any]) -> set[Path]:
    """Return images explicitly owned by the customer's auxiliary Product Pack.

    This is an intent boundary, not an image-quality rule. Auxiliary evidence may be
    inspected for product facts but must never compete with supplier photos for an
    automatic Makro Product Photos slot. Explicit manual Product Photos are handled
    by the separate GUI upload-intent path and therefore never enter this ranker.
    """

    manifest_text = str(outputs.get("product_pack_manifest") or "").strip()
    if not manifest_text:
        return set()
    manifest_path = Path(manifest_text).expanduser().resolve()
    if not manifest_path.is_file():
        return set()
    try:
        payload = _read_json(manifest_path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return set()
    if not isinstance(payload, dict):
        return set()

    values = [
        *(payload.get("evidence_images") or []),
        *(payload.get("listing_images") or []),
    ]
    return {
        Path(str(value)).expanduser().resolve()
        for value in values
        if str(value or "").strip()
    }


def _build_candidates(
    raw_images: list[object],
    *,
    excluded_paths: set[Path] | None = None,
) -> tuple[list[_Candidate], list[dict[str, Any]]]:
    """Build the AI candidate universe with transport and ownership checks only.

    Product meaning, importance, visual similarity, main-image quality and gallery
    ordering deliberately do not exist in this layer. A supplier image only needs
    to contain decodable pixels. Exact byte-identical copies are collapsed because
    sending the exact same payload twice cannot add semantic information.
    """

    excluded = excluded_paths or set()
    candidates: list[_Candidate] = []
    rejected: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()

    for source_index, raw in enumerate(raw_images, start=1):
        path = Path(str(raw or "")).expanduser().resolve()
        if path in excluded:
            rejected.append(
                {
                    "source_index": source_index,
                    "path": str(path),
                    "reason": "customer_auxiliary_not_auto_listing_candidate",
                }
            )
            continue
        if not path.is_file():
            rejected.append(
                {"source_index": source_index, "path": str(path), "reason": "missing_file"}
            )
            continue
        if not has_decodable_image_pixels(path):
            rejected.append(
                {"source_index": source_index, "path": str(path), "reason": "not_decodable"}
            )
            continue
        try:
            digest = _sha256(path)
        except OSError:
            rejected.append(
                {"source_index": source_index, "path": str(path), "reason": "read_error"}
            )
            continue
        if digest in seen_hashes:
            rejected.append(
                {
                    "source_index": source_index,
                    "path": str(path),
                    "reason": "exact_duplicate",
                    "sha256": digest,
                }
            )
            continue
        seen_hashes.add(digest)
        candidates.append(
            _Candidate(
                image_id=f"image_{len(candidates) + 1:02d}",
                path=path,
                sha256=digest,
                source_index=source_index,
            )
        )

    return candidates, rejected


def _product_context(manifest: dict[str, Any]) -> dict[str, Any]:
    outputs = manifest.get("outputs") or {}
    snapshot_path = Path(str(outputs.get("primary_source_snapshot") or "")).expanduser()
    context: dict[str, Any] = {
        "product_url": str(manifest.get("primary_product_url") or "").strip(),
        "page_title": "",
        "specifications": [],
        "page_text_excerpt": "",
    }
    if not snapshot_path.is_file():
        return context
    try:
        snapshot = source_snapshot_from_json(snapshot_path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return context
    context["product_url"] = snapshot.final_url or snapshot.requested_url or context["product_url"]
    context["page_title"] = _compact_text(snapshot.title, 800)
    context["specifications"] = [
        {
            "key": _compact_text(row.key, 100),
            "value": _compact_text(row.value, 220),
        }
        for row in snapshot.table_rows[:40]
        if row.key or row.value
    ]
    context["page_text_excerpt"] = _compact_text(snapshot.visible_text, 6000)
    return context


def _ranking_schema(candidate_ids: list[str]) -> dict[str, Any]:
    decision_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "selected": {"type": "boolean"},
            "reason": {"type": "string", "minLength": 1},
        },
        "required": ["selected", "reason"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "selected_image_ids": {
                "type": "array",
                "maxItems": MAX_AUTO_LISTING_IMAGES,
                "uniqueItems": True,
                "items": {"type": "string", "enum": candidate_ids},
            },
            "decisions": {
                "type": "object",
                "additionalProperties": False,
                "properties": {image_id: decision_schema for image_id in candidate_ids},
                "required": candidate_ids,
            },
            "summary": {"type": "string"},
        },
        "required": ["selected_image_ids", "decisions", "summary"],
    }


def build_listing_image_ranking_request(
    *,
    product_context: dict[str, Any],
    candidates: list[_Candidate],
) -> dict[str, Any]:
    """Ask one multimodal model to make the complete photo decision.

    The model sees every supplier candidate image together. `selected_image_ids` is
    already the final upload order; Python never applies a second role table, score
    formula, visual fingerprint, background heuristic or source-order ranking.
    """

    candidate_ids = [candidate.image_id for candidate in candidates]
    return {
        "task": "select_and_order_supplier_listing_images",
        "system_instruction": (
            "You are the sole semantic decision maker for ecommerce listing photos. "
            "Inspect all supplied candidate images together with the exact target-product context. "
            "Decide which images belong in the final listing, remove unrelated or redundant images, "
            "and return the final upload order. The first selected image is the main product photo. JSON only."
        ),
        "prompt_instruction": (
            "Return selected_image_ids in the exact order they should be uploaded. Choose up to five useful images. "
            "Prefer a strong clear main-product image first, then the most useful non-redundant supporting views. "
            "Do not return zero merely because images are imperfect; return zero only when none are genuinely useful "
            "for the exact target product."
        ),
        "context": {
            "target_product": product_context,
            "candidate_image_ids": candidate_ids,
        },
        "rules": [
            "Inspect the actual pixels of every supplied candidate before deciding.",
            "selected_image_ids is the final gallery order and must contain no more than five image_ids.",
            "Position 1 must be the best available main image of the exact sellable product.",
            "Exclude unrelated products, recommendations, wrong models or variants, seller/page graphics and other images that do not help this exact listing.",
            "Treat visual duplication and near-duplication as a semantic judgment: keep the better or more informative version instead of filling slots with repeated content.",
            "Prefer useful variety such as another product view, detail, dimensions, included items, packaging or in-use context when it adds information.",
            "Do not use input/source order as evidence of importance.",
            "Return one decision for every candidate image_id. selected=true must match membership in selected_image_ids exactly.",
        ],
        "target_fields": [],
        "grounded_sources": [candidate.as_source() for candidate in candidates],
        "json_contract": _ranking_schema(candidate_ids),
        "strict_json_schema": True,
    }


def _parse_ranking(raw: Any, candidates: list[_Candidate]) -> _ParsedRanking:
    if not isinstance(raw, dict):
        raise ListingImageRankingError("listing image AI returned no JSON object")

    expected = [candidate.image_id for candidate in candidates]
    expected_set = set(expected)
    raw_selected = raw.get("selected_image_ids")
    if not isinstance(raw_selected, list):
        raise ListingImageRankingError("selected_image_ids must be an array")
    selected_ids = tuple(str(item or "").strip() for item in raw_selected)
    if any(not image_id for image_id in selected_ids):
        raise ListingImageRankingError("selected_image_ids contains an empty image_id")
    if len(selected_ids) > MAX_AUTO_LISTING_IMAGES:
        raise ListingImageRankingError("listing image AI selected more than the upload limit")
    if len(set(selected_ids)) != len(selected_ids):
        raise ListingImageRankingError("selected_image_ids contains duplicates")
    if not set(selected_ids).issubset(expected_set):
        raise ListingImageRankingError("selected_image_ids contains an unknown image_id")

    raw_decisions = raw.get("decisions")
    if not isinstance(raw_decisions, dict) or set(raw_decisions) != expected_set:
        raise ListingImageRankingError("listing image AI did not return the exact candidate decision partition")

    decisions: dict[str, RankedImageDecision] = {}
    selected_set = set(selected_ids)
    for image_id in expected:
        item = raw_decisions.get(image_id)
        if not isinstance(item, dict):
            raise ListingImageRankingError(f"{image_id} decision must be an object")
        selected = item.get("selected")
        reason = _compact_text(item.get("reason"), 600)
        if type(selected) is not bool:
            raise ListingImageRankingError(f"{image_id}.selected must be boolean")
        if not reason:
            raise ListingImageRankingError(f"{image_id}.reason is required")
        if selected != (image_id in selected_set):
            raise ListingImageRankingError(
                f"{image_id}.selected disagrees with selected_image_ids"
            )
        decisions[image_id] = RankedImageDecision(
            image_id=image_id,
            selected=selected,
            reason=reason,
        )

    return _ParsedRanking(
        selected_ids=selected_ids,
        decisions=decisions,
        summary=_compact_text(raw.get("summary"), 1200),
    )


def _selection_report(
    *,
    candidates: list[_Candidate],
    transport_rejected: list[dict[str, Any]],
    ranking: _ParsedRanking | None,
) -> dict[str, Any]:
    selected_ids = list(ranking.selected_ids) if ranking is not None else []
    selected_set = set(selected_ids)
    return {
        "schema_version": 5,
        "policy": {
            "version": LISTING_IMAGE_RANKING_VERSION,
            "kind": "ai-owned-multimodal-listing-photo-selection",
            "max_auto_listing_images": MAX_AUTO_LISTING_IMAGES,
            "pre_ai_filter": "explicit-ownership-boundary-plus-decodable-image-and-exact-byte-duplicate-only",
            "semantic_owner": "multimodal_ai",
            "duplicate_owner": "multimodal_ai_except_exact_byte_duplicates",
            "ordering": "selected_image_ids_verbatim",
        },
        "candidate_count": len(candidates),
        "transport_rejected": transport_rejected,
        "selected_ids": selected_ids,
        "selected": [
            str(candidate.path)
            for candidate in candidates
            if candidate.image_id in selected_set
        ],
        "summary": ranking.summary if ranking is not None else "",
        "decisions": [
            {
                "image_id": candidate.image_id,
                "path": str(candidate.path),
                "source_index": candidate.source_index,
                "sha256": candidate.sha256,
                **(
                    ranking.decisions[candidate.image_id].as_dict()
                    if ranking is not None
                    else {"selected": False, "reason": "AI ranking not run"}
                ),
            }
            for candidate in candidates
        ],
    }


def finalize_supplier_listing_images(
    run_dir: str | Path,
    provider: JSONTaskProvider,
) -> ListingImageRankingResult:
    """Let multimodal AI own supplier listing-photo relevance and final order.

    This stage intentionally has no Python-side image-role priority, importance
    score, white-background score, entropy score, visual similarity threshold or
    source-order preference. Pre-AI exclusions are limited to explicit customer
    auxiliary ownership, technical failures and exact byte-identical copies. If the
    AI call fails, the caller keeps the prior mechanical compatibility result.
    """

    root = Path(run_dir).expanduser().resolve()
    manifest_path = root / "run-manifest.json"
    if not manifest_path.is_file():
        raise ListingImageRankingError(f"resolver manifest not found: {manifest_path}")
    manifest = _read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ListingImageRankingError("resolver manifest must be an object")
    if str(manifest.get("input_mode") or "") != "supplier_url":
        return ListingImageRankingResult(
            status="not_supplier_input",
            selected=(),
            model_calls=0,
            mechanical_candidate_count=0,
            semantically_rejected_count=0,
            selection_report=None,
        )

    outputs = manifest.get("outputs") or {}
    if not isinstance(outputs, dict):
        raise ListingImageRankingError("resolver manifest outputs must be an object")
    raw_images = outputs.get("primary_source_product_images") or []
    if not isinstance(raw_images, list):
        raise ListingImageRankingError("primary_source_product_images must be an array")

    candidates, transport_rejected = _build_candidates(
        raw_images,
        excluded_paths=_customer_owned_image_paths(outputs),
    )
    selection_path = Path(
        str(outputs.get("primary_source_listing_image_selection") or root / "listing-image-selection.json")
    ).expanduser().resolve()

    if not candidates:
        outputs["primary_source_listing_images"] = []
        outputs["primary_source_listing_image_selection"] = str(selection_path)
        manifest["outputs"] = outputs
        report = _selection_report(
            candidates=candidates,
            transport_rejected=transport_rejected,
            ranking=None,
        )
        _write_json_atomic(selection_path, report)
        manifest["listing_image_ranking"] = {
            "version": LISTING_IMAGE_RANKING_VERSION,
            "status": "no_transport_candidates",
            "strategy": "multimodal_ai_owns_semantics_and_order",
            "model_calls": 0,
            "candidate_count": 0,
            "transport_rejected_count": len(transport_rejected),
            "selected_count": 0,
            "selection_report": str(selection_path),
        }
        _write_json_atomic(manifest_path, manifest)
        return ListingImageRankingResult(
            status="no_transport_candidates",
            selected=(),
            model_calls=0,
            mechanical_candidate_count=0,
            semantically_rejected_count=0,
            selection_report=selection_path,
        )

    request = build_listing_image_ranking_request(
        product_context=_product_context(manifest),
        candidates=candidates,
    )
    with usage_request_context(
        task=str(request.get("task") or ""),
        provider=str(getattr(provider, "name", "")),
        model=str(getattr(provider, "model", "")),
    ):
        raw = provider.extract_json(request)
    ranking = _parse_ranking(raw, candidates)

    by_id = {candidate.image_id: candidate for candidate in candidates}
    selected_candidates = [by_id[image_id] for image_id in ranking.selected_ids]
    selected_paths = tuple(candidate.path for candidate in selected_candidates)
    report = _selection_report(
        candidates=candidates,
        transport_rejected=transport_rejected,
        ranking=ranking,
    )
    report["selected"] = [str(candidate.path) for candidate in selected_candidates]
    _write_json_atomic(selection_path, report)

    outputs["primary_source_listing_images"] = [str(path) for path in selected_paths]
    outputs["primary_source_listing_image_selection"] = str(selection_path)
    manifest["outputs"] = outputs

    source_capture = manifest.get("source_capture") or {}
    if isinstance(source_capture, dict):
        source_capture["listing_image_policy_version"] = LISTING_IMAGE_RANKING_VERSION
        source_capture["listing_images_selected"] = len(selected_paths)
        source_capture["listing_images"] = [str(path) for path in selected_paths]
        source_capture["listing_images_rejected"] = len(candidates) - len(selected_paths)
        manifest["source_capture"] = source_capture

    manifest["listing_image_ranking"] = {
        "version": LISTING_IMAGE_RANKING_VERSION,
        "status": "ai_ranked",
        "strategy": "multimodal_ai_owns_semantics_duplicates_and_order",
        "model_calls": 1,
        "candidate_count": len(candidates),
        "transport_rejected_count": len(transport_rejected),
        "semantic_rejected_count": len(candidates) - len(selected_paths),
        "selected_count": len(selected_paths),
        "selected": [str(path) for path in selected_paths],
        "selected_ids": list(ranking.selected_ids),
        "selection_report": str(selection_path),
    }
    manifest["total_model_calls"] = int(manifest.get("total_model_calls") or 0) + 1
    _write_json_atomic(manifest_path, manifest)

    return ListingImageRankingResult(
        status="ai_ranked",
        selected=selected_paths,
        model_calls=1,
        mechanical_candidate_count=len(candidates),
        semantically_rejected_count=len(candidates) - len(selected_paths),
        selection_report=selection_path,
    )


__all__ = [
    "LISTING_IMAGE_RANKING_VERSION",
    "MAX_AUTO_LISTING_IMAGES",
    "ListingImageRankingError",
    "ListingImageRankingResult",
    "RankedImageDecision",
    "build_listing_image_ranking_request",
    "finalize_supplier_listing_images",
]
