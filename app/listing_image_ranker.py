from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from PIL import Image, ImageOps, ImageStat

from .listing_images import ListingImageAssessment, select_listing_images
from .providers.usage_telemetry import usage_request_context
from .source_snapshot import source_snapshot_from_json


LISTING_IMAGE_RANKING_VERSION = 2
MAX_AUTO_LISTING_IMAGES = 5
LISTING_IMAGE_ROLES = (
    "hero",
    "product",
    "detail",
    "dimensions",
    "lifestyle",
    "included_item",
    "packaging",
    "unrelated",
    "uncertain",
)
_ROLE_PRIORITY = {
    "hero": 0,
    "product": 1,
    "detail": 2,
    "dimensions": 3,
    "lifestyle": 4,
    "included_item": 5,
    "packaging": 6,
    "unrelated": 99,
    "uncertain": 99,
}


class ListingImageRankingError(RuntimeError):
    pass


class JSONTaskProvider(Protocol):
    name: str

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass(slots=True, frozen=True)
class RankedImageDecision:
    image_id: str
    relevant: bool
    role: str
    main_image_score: int
    gallery_score: int
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "image_id": self.image_id,
            "relevant": self.relevant,
            "role": self.role,
            "main_image_score": self.main_image_score,
            "gallery_score": self.gallery_score,
            "reason": self.reason,
        }


@dataclass(slots=True, frozen=True)
class _Candidate:
    image_id: str
    assessment: ListingImageAssessment
    observation_available: bool
    profile: dict[str, Any]


@dataclass(slots=True, frozen=True)
class ListingImageRankingResult:
    status: str
    selected: tuple[Path, ...]
    model_calls: int
    mechanical_candidate_count: int
    semantically_rejected_count: int
    selection_report: Path | None


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


def _visual_profile(path: Path) -> dict[str, Any]:
    try:
        with Image.open(path) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            image.thumbnail((256, 256))
            width, height = image.size
            if width <= 0 or height <= 0:
                return {"available": False}

            border: list[tuple[int, int, int]] = []
            for x in range(width):
                border.append(image.getpixel((x, 0)))
                if height > 1:
                    border.append(image.getpixel((x, height - 1)))
            for y in range(1, max(1, height - 1)):
                border.append(image.getpixel((0, y)))
                if width > 1:
                    border.append(image.getpixel((width - 1, y)))

            light_neutral = 0
            neutral = 0
            for red, green, blue in border:
                spread = max(red, green, blue) - min(red, green, blue)
                if spread <= 24:
                    neutral += 1
                    if min(red, green, blue) >= 220:
                        light_neutral += 1
            denominator = max(1, len(border))
            gray = image.convert("L")
            stat = ImageStat.Stat(gray)
            brightness = float(stat.mean[0]) if stat.mean else 0.0
            return {
                "available": True,
                "light_neutral_border_ratio": round(light_neutral / denominator, 4),
                "neutral_border_ratio": round(neutral / denominator, 4),
                "mean_brightness": round(brightness, 2),
                "entropy": round(float(gray.entropy()), 3),
            }
    except (OSError, ValueError):
        return {"available": False}


def _load_observations(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    payload = _read_json(path)
    if not isinstance(payload, list):
        return {}
    output: dict[str, dict[str, Any]] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        digest = str(item.get("sha256") or "").strip()
        if digest and digest not in output:
            output[digest] = item
    return output


def _fact_excerpt(raw_facts: Any) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for item in raw_facts or []:
        if not isinstance(item, dict):
            continue
        output.append(
            {
                "name": _compact_text(item.get("name"), 80),
                "scope": _compact_text(item.get("scope"), 60),
                "value": _compact_text(item.get("value"), 140),
                "evidence_text": _compact_text(item.get("evidence_text"), 220),
            }
        )
        if len(output) >= 8:
            break
    return output


def _build_candidates(
    mechanical: Any,
    observations: dict[str, dict[str, Any]],
) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for assessment in mechanical.assessments:
        if not assessment.eligible:
            continue
        observation = observations.get(assessment.sha256) or {}
        available = bool(observation)
        image_id = f"image_{len(candidates) + 1:02d}"
        candidates.append(
            _Candidate(
                image_id=image_id,
                assessment=assessment,
                observation_available=available,
                profile={
                    "image_id": image_id,
                    "source_index": assessment.source_index,
                    "width": assessment.width,
                    "height": assessment.height,
                    "pixel_area": assessment.pixel_area,
                    "aspect_ratio": (
                        round(float(assessment.aspect_ratio), 4)
                        if assessment.aspect_ratio is not None
                        else None
                    ),
                    "visual_metrics": _visual_profile(assessment.path),
                    "ai_observation_available": available,
                    "visible_text": _compact_text(observation.get("visible_text"), 500),
                    "facts": _fact_excerpt(observation.get("facts")),
                    "notes": _compact_text(observation.get("notes"), 700),
                },
            )
        )
    return candidates


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


def _decision_schema(candidate_ids: list[str]) -> dict[str, Any]:
    item_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "relevant": {"type": "boolean"},
            "role": {"type": "string", "enum": list(LISTING_IMAGE_ROLES)},
            "main_image_score": {"type": "integer", "minimum": 0, "maximum": 100},
            "gallery_score": {"type": "integer", "minimum": 0, "maximum": 100},
            "reason": {"type": "string", "minLength": 1},
        },
        "required": [
            "relevant",
            "role",
            "main_image_score",
            "gallery_score",
            "reason",
        ],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "decisions": {
                "type": "object",
                "additionalProperties": False,
                "properties": {image_id: item_schema for image_id in candidate_ids},
                "required": list(candidate_ids),
            },
            "summary": {"type": "string"},
        },
        "required": ["decisions", "summary"],
    }


def build_listing_image_ranking_request(
    *,
    product_context: dict[str, Any],
    candidates: list[_Candidate],
) -> dict[str, Any]:
    candidate_ids = [candidate.image_id for candidate in candidates]
    return {
        "task": "rank_supplier_listing_images",
        "system_instruction": (
            "You are the final semantic gate for ecommerce listing photos. Decide whether every candidate image "
            "belongs to the exact target product and classify its useful gallery role. The image pixels were already "
            "inspected by an upstream vision stage; use only the supplied target product context, AI image observations "
            "and mechanical visual metrics. JSON only."
        ),
        "prompt_instruction": (
            "Evaluate every candidate independently against context.target_product. Reject unrelated products and noisy "
            "page assets. Score main-image suitability separately from general gallery usefulness."
        ),
        "context": {
            "target_product": product_context,
            "candidate_images": [candidate.profile for candidate in candidates],
        },
        "rules": [
            "Return one decision for every candidate image_id exactly once.",
            "relevant=true only when the observation supports that the image depicts the exact target sellable product, an included item, its dimensions, its use, or its packaging.",
            "Reject recommendation products, other models or contradictory variants, unrelated accessories, seller logos, payment/shipping graphics, badges, category/navigation art, screenshots and generic banners.",
            "role=hero only for a strong primary photo: exact target product, whole product clearly visible, unobstructed, simple/clean composition, no dense text overlay or collage.",
            "role=product is an alternate whole-product view that is relevant but less ideal than a hero image.",
            "Use detail for useful close-ups, dimensions for measurement diagrams, lifestyle for in-use scenes, included_item for included components, and packaging for box/package views.",
            "main_image_score measures only suitability as gallery position 1. Packaging, dimensions, lifestyle and detail images must score materially below a clean full-product hero when one exists.",
            "gallery_score measures usefulness and clarity anywhere in the gallery. Prefer sharp, well-framed, informative images and penalize clutter, collages, tiny subjects and heavy promotional text.",
            "Do not use source order as evidence of relevance or quality.",
        ],
        "target_fields": [],
        "grounded_sources": [],
        "json_contract": _decision_schema(candidate_ids),
        "strict_json_schema": True,
    }


def _parse_decisions(raw: Any, candidates: list[_Candidate]) -> dict[str, RankedImageDecision]:
    if not isinstance(raw, dict) or not isinstance(raw.get("decisions"), dict):
        raise ListingImageRankingError("listing image ranker returned no decisions object")
    payload = raw["decisions"]
    expected = {candidate.image_id for candidate in candidates}
    if set(payload) != expected:
        raise ListingImageRankingError("listing image ranker did not return the exact candidate partition")

    output: dict[str, RankedImageDecision] = {}
    for candidate in candidates:
        image_id = candidate.image_id
        if not candidate.observation_available:
            raise ListingImageRankingError(
                f"{image_id} reached semantic ranking without an upstream AI observation"
            )
        item = payload.get(image_id)
        if not isinstance(item, dict):
            raise ListingImageRankingError(f"{image_id} decision must be an object")
        relevant = item.get("relevant")
        role = str(item.get("role") or "").strip()
        main_score = item.get("main_image_score")
        gallery_score = item.get("gallery_score")
        reason = _compact_text(item.get("reason"), 500)
        if type(relevant) is not bool:
            raise ListingImageRankingError(f"{image_id}.relevant must be boolean")
        if role not in LISTING_IMAGE_ROLES:
            raise ListingImageRankingError(f"{image_id}.role is invalid: {role!r}")
        if type(main_score) is not int or not 0 <= main_score <= 100:
            raise ListingImageRankingError(f"{image_id}.main_image_score must be integer 0..100")
        if type(gallery_score) is not int or not 0 <= gallery_score <= 100:
            raise ListingImageRankingError(f"{image_id}.gallery_score must be integer 0..100")
        if not reason:
            raise ListingImageRankingError(f"{image_id}.reason is required")
        if role in {"unrelated", "uncertain"}:
            relevant = False

        output[image_id] = RankedImageDecision(
            image_id=image_id,
            relevant=relevant,
            role=role,
            main_image_score=main_score,
            gallery_score=gallery_score,
            reason=reason,
        )
    return output


def _mechanical_fallback_decision(candidate: _Candidate) -> RankedImageDecision:
    return RankedImageDecision(
        image_id=candidate.image_id,
        relevant=True,
        role="uncertain",
        main_image_score=0,
        gallery_score=0,
        reason=(
            "Upstream AI observation was unavailable; retained only as a mechanically safe "
            "supplier product-image fallback instead of deleting the photo set."
        ),
    )


def _ordered_candidates(
    candidates: list[_Candidate],
    decisions: dict[str, RankedImageDecision],
) -> list[_Candidate]:
    relevant = [
        candidate
        for candidate in candidates
        if decisions[candidate.image_id].relevant
        and decisions[candidate.image_id].role not in {"unrelated", "uncertain"}
    ]
    if not relevant:
        return []

    def first_key(candidate: _Candidate) -> tuple[int, int, int, int]:
        decision = decisions[candidate.image_id]
        area = int(candidate.assessment.pixel_area or 0)
        return (
            decision.main_image_score,
            decision.gallery_score,
            area,
            -candidate.assessment.source_index,
        )

    primary_pool = [
        candidate
        for candidate in relevant
        if decisions[candidate.image_id].role in {"hero", "product"}
    ]
    first = max(primary_pool or relevant, key=first_key)

    remaining = [candidate for candidate in relevant if candidate.image_id != first.image_id]
    remaining.sort(
        key=lambda candidate: (
            _ROLE_PRIORITY[decisions[candidate.image_id].role],
            -decisions[candidate.image_id].gallery_score,
            -decisions[candidate.image_id].main_image_score,
            -int(candidate.assessment.pixel_area or 0),
            candidate.assessment.source_index,
        )
    )
    return [first, *remaining]


def _selection_report(
    *,
    mechanical: Any,
    candidates: list[_Candidate],
    decisions: dict[str, RankedImageDecision],
    selected: list[_Candidate],
) -> dict[str, Any]:
    selected_ids = {candidate.image_id for candidate in selected}
    return {
        "schema_version": 3,
        "policy": {
            "version": LISTING_IMAGE_RANKING_VERSION,
            "kind": "mechanical-quality-gate-then-ai-semantic-ranking",
            "max_auto_listing_images": MAX_AUTO_LISTING_IMAGES,
            "ordering": "best-main-image-then-role-priority-and-ai-gallery-score",
            "missing_ai_observation": "mechanically-safe-fallback-after-semantic-images",
        },
        "selected": [str(candidate.assessment.path) for candidate in selected],
        "selected_count": len(selected),
        "rejected_count": len(mechanical.assessments) - len(selected),
        "mechanical": mechanical.as_dict(),
        "semantic_ranking": {
            "candidate_count": len(candidates),
            "observed_candidate_count": sum(1 for candidate in candidates if candidate.observation_available),
            "fallback_candidate_count": sum(1 for candidate in candidates if not candidate.observation_available),
            "selected_ids": [candidate.image_id for candidate in selected],
            "decisions": [
                {
                    **decisions[candidate.image_id].as_dict(),
                    "path": str(candidate.assessment.path),
                    "source_index": candidate.assessment.source_index,
                    "selected": candidate.image_id in selected_ids,
                    "profile": candidate.profile,
                }
                for candidate in candidates
            ],
        },
    }


def _publish_selection(
    *,
    manifest: dict[str, Any],
    manifest_path: Path,
    outputs: dict[str, Any],
    selection_path: Path,
    mechanical: Any,
    candidates: list[_Candidate],
    decisions: dict[str, RankedImageDecision],
    selected_candidates: list[_Candidate],
    status: str,
    model_calls: int,
    semantically_rejected: int,
) -> ListingImageRankingResult:
    selected_paths = tuple(candidate.assessment.path for candidate in selected_candidates)
    report = _selection_report(
        mechanical=mechanical,
        candidates=candidates,
        decisions=decisions,
        selected=selected_candidates,
    )
    _write_json_atomic(selection_path, report)

    outputs["primary_source_listing_images"] = [str(path) for path in selected_paths]
    outputs["primary_source_listing_image_selection"] = str(selection_path)
    manifest["outputs"] = outputs
    source_capture = manifest.get("source_capture") or {}
    if isinstance(source_capture, dict):
        source_capture["listing_image_policy_version"] = LISTING_IMAGE_RANKING_VERSION
        source_capture["listing_images_selected"] = len(selected_paths)
        source_capture["listing_images"] = [str(path) for path in selected_paths]
        source_capture["listing_images_rejected"] = len(mechanical.assessments) - len(selected_paths)
        manifest["source_capture"] = source_capture
    manifest["listing_image_ranking"] = {
        "version": LISTING_IMAGE_RANKING_VERSION,
        "status": status,
        "strategy": "semantic_rank_observed_then_mechanical_fallback_for_unobserved",
        "model_calls": model_calls,
        "mechanical_candidate_count": len(candidates),
        "observed_candidate_count": sum(1 for candidate in candidates if candidate.observation_available),
        "fallback_candidate_count": sum(1 for candidate in candidates if not candidate.observation_available),
        "semantic_rejected_count": semantically_rejected,
        "selected_count": len(selected_paths),
        "selected": [str(path) for path in selected_paths],
        "selection_report": str(selection_path),
    }
    if model_calls:
        manifest["total_model_calls"] = int(manifest.get("total_model_calls") or 0) + model_calls
    _write_json_atomic(manifest_path, manifest)

    return ListingImageRankingResult(
        status=status,
        selected=selected_paths,
        model_calls=model_calls,
        mechanical_candidate_count=len(candidates),
        semantically_rejected_count=semantically_rejected,
        selection_report=selection_path,
    )


def finalize_supplier_listing_images(
    run_dir: str | Path,
    provider: JSONTaskProvider,
) -> ListingImageRankingResult:
    """Publish relevance-aware supplier photos without turning AI outages into zero photos.

    Mechanical validation remains the file-safety boundary. Candidates with an
    upstream image observation are semantically filtered and ranked. A missing
    observation means "unknown", not "unrelated": mechanically safe supplier
    images are retained as a bounded fallback after semantically ranked images.
    Positive AI rejection still removes an observed unrelated image.
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

    mechanical = select_listing_images(raw_images)
    observations_path = Path(str(outputs.get("image_observations") or "")).expanduser()
    observations = _load_observations(observations_path)
    candidates = _build_candidates(mechanical, observations)
    selection_path = Path(
        str(outputs.get("primary_source_listing_image_selection") or root / "listing-image-selection.json")
    ).expanduser().resolve()

    if not candidates:
        outputs["primary_source_listing_images"] = []
        report = {
            "schema_version": 3,
            "policy": {
                "version": LISTING_IMAGE_RANKING_VERSION,
                "kind": "mechanical-quality-gate-then-ai-semantic-ranking",
                "max_auto_listing_images": MAX_AUTO_LISTING_IMAGES,
                "missing_ai_observation": "mechanically-safe-fallback-after-semantic-images",
            },
            "selected": [],
            "selected_count": 0,
            "rejected_count": len(mechanical.assessments),
            "mechanical": mechanical.as_dict(),
            "semantic_ranking": {"candidate_count": 0, "decisions": []},
        }
        _write_json_atomic(selection_path, report)
        manifest["outputs"] = outputs
        manifest["listing_image_ranking"] = {
            "version": LISTING_IMAGE_RANKING_VERSION,
            "status": "no_mechanical_candidates",
            "model_calls": 0,
            "selected_count": 0,
        }
        _write_json_atomic(manifest_path, manifest)
        return ListingImageRankingResult(
            status="no_mechanical_candidates",
            selected=(),
            model_calls=0,
            mechanical_candidate_count=0,
            semantically_rejected_count=0,
            selection_report=selection_path,
        )

    observed = [candidate for candidate in candidates if candidate.observation_available]
    fallback = [candidate for candidate in candidates if not candidate.observation_available]

    if not observed:
        decisions = {
            candidate.image_id: _mechanical_fallback_decision(candidate)
            for candidate in candidates
        }
        return _publish_selection(
            manifest=manifest,
            manifest_path=manifest_path,
            outputs=outputs,
            selection_path=selection_path,
            mechanical=mechanical,
            candidates=candidates,
            decisions=decisions,
            selected_candidates=candidates[:MAX_AUTO_LISTING_IMAGES],
            status="mechanical_fallback_no_ai_observations",
            model_calls=0,
            semantically_rejected=0,
        )

    request = build_listing_image_ranking_request(
        product_context=_product_context(manifest),
        candidates=observed,
    )
    with usage_request_context(
        task=str(request.get("task") or ""),
        provider=str(getattr(provider, "name", "")),
        model=str(getattr(provider, "model", "")),
    ):
        raw = provider.extract_json(request)
    semantic_decisions = _parse_decisions(raw, observed)
    decisions = dict(semantic_decisions)
    decisions.update(
        {
            candidate.image_id: _mechanical_fallback_decision(candidate)
            for candidate in fallback
        }
    )

    ordered = _ordered_candidates(observed, semantic_decisions)
    selected_candidates = ordered[:MAX_AUTO_LISTING_IMAGES]
    if len(selected_candidates) < MAX_AUTO_LISTING_IMAGES:
        selected_ids = {candidate.image_id for candidate in selected_candidates}
        for candidate in fallback:
            if candidate.image_id in selected_ids:
                continue
            selected_candidates.append(candidate)
            selected_ids.add(candidate.image_id)
            if len(selected_candidates) >= MAX_AUTO_LISTING_IMAGES:
                break

    semantically_rejected = sum(
        1
        for candidate in observed
        if not semantic_decisions[candidate.image_id].relevant
    )
    used_fallback = any(not candidate.observation_available for candidate in selected_candidates)
    status = "ranked_with_mechanical_fallback" if used_fallback else "ranked"
    return _publish_selection(
        manifest=manifest,
        manifest_path=manifest_path,
        outputs=outputs,
        selection_path=selection_path,
        mechanical=mechanical,
        candidates=candidates,
        decisions=decisions,
        selected_candidates=selected_candidates,
        status=status,
        model_calls=1,
        semantically_rejected=semantically_rejected,
    )


__all__ = [
    "LISTING_IMAGE_RANKING_VERSION",
    "LISTING_IMAGE_ROLES",
    "MAX_AUTO_LISTING_IMAGES",
    "ListingImageRankingError",
    "ListingImageRankingResult",
    "RankedImageDecision",
    "build_listing_image_ranking_request",
    "finalize_supplier_listing_images",
]
