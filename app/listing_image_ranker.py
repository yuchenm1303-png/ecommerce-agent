from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .image_media import has_decodable_image_pixels
from .product_identity import ProductIdentity, infer_product_identity
from .providers.usage_telemetry import usage_request_context
from .source_snapshot import SourceSnapshot, source_snapshot_from_json


LISTING_IMAGE_RANKING_VERSION = 9
MAX_AUTO_LISTING_IMAGES = 5
VISUAL_FACTS_BATCH_SIZE = 6
# Compatibility name retained for callers/tests that previously used Ownership batching.
# The multimodal transport boundary now belongs to target-blind visual perception.
OWNERSHIP_BATCH_SIZE = VISUAL_FACTS_BATCH_SIZE

_IMAGE_OWNERSHIP_CLASSES = (
    "EXACT_TARGET",
    "TARGET_PACKAGING_OR_DETAIL",
    "SAME_PRODUCT_OTHER_VARIANT",
    "OTHER_PRODUCT",
    "PAGE_ASSET",
    "UNCERTAIN",
)
_AUTO_ELIGIBLE_OWNERSHIP_CLASSES = frozenset(
    {"EXACT_TARGET", "TARGET_PACKAGING_OR_DETAIL"}
)
_BLIND_VISUAL_FACTS_PROTOCOL = "target_blind_pixel_observation_v1"
_IMAGE_OWNERSHIP_PROTOCOL = "frozen_blind_facts_target_comparison_v6"
_GALLERY_PROTOCOL = "identity_frozen_quality_ordering_v2"


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

    def as_source(self) -> dict[str, Any]:
        return {
            "source_id": self.image_id,
            "source_type": "supplier_listing_image_candidate",
            "kind": "image",
            "origin": str(self.path),
            "image_path": str(self.path),
            "sha256": self.sha256,
            "source_index": self.source_index,
        }


@dataclass(slots=True, frozen=True)
class ImageVisualFacts:
    image_id: str
    visual_subject: str
    readable_identity: str
    raw_colour_materials: str
    design_configuration: str
    neutral_product_guess: str
    visual_uncertainty: str
    presentation_quality: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "image_id": self.image_id,
            "visual_subject": self.visual_subject,
            "readable_identity": self.readable_identity,
            "raw_colour_materials": self.raw_colour_materials,
            "design_configuration": self.design_configuration,
            "neutral_product_guess": self.neutral_product_guess,
            "visual_uncertainty": self.visual_uncertainty,
            "presentation_quality": self.presentation_quality,
        }


@dataclass(slots=True, frozen=True)
class ImageOwnershipDecision:
    image_id: str
    classification: str
    confidence: float
    reason: str
    visual_subject: str = ""
    visible_identity: str = ""
    visible_configuration: str = ""
    target_conflicts: str = ""
    target_match_evidence: str = ""
    target_identity_gaps: str = ""

    @property
    def auto_eligible(self) -> bool:
        return self.classification in _AUTO_ELIGIBLE_OWNERSHIP_CLASSES

    def as_dict(self) -> dict[str, Any]:
        return {
            "image_id": self.image_id,
            "visual_subject": self.visual_subject,
            "visible_identity": self.visible_identity,
            "visible_configuration": self.visible_configuration,
            "target_conflicts": self.target_conflicts,
            "target_match_evidence": self.target_match_evidence,
            "target_identity_gaps": self.target_identity_gaps,
            "classification": self.classification,
            "confidence": self.confidence,
            "reason": self.reason,
            "auto_eligible": self.auto_eligible,
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
class _ParsedVisualFacts:
    facts: dict[str, ImageVisualFacts]
    summary: str
    model_calls: int = 1


@dataclass(slots=True, frozen=True)
class _ParsedOwnership:
    decisions: dict[str, ImageOwnershipDecision]
    summary: str
    model_calls: int = 1

    @property
    def eligible_ids(self) -> tuple[str, ...]:
        return tuple(
            image_id
            for image_id, decision in self.decisions.items()
            if decision.auto_eligible
        )


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


def _source_snapshot(manifest: dict[str, Any]) -> SourceSnapshot:
    outputs = manifest.get("outputs") or {}
    if not isinstance(outputs, dict):
        raise ListingImageRankingError("resolver manifest outputs must be an object")
    snapshot_text = str(outputs.get("primary_source_snapshot") or "").strip()
    if not snapshot_text:
        raise ListingImageRankingError("primary_source_snapshot is required for safe image ownership")
    snapshot_path = Path(snapshot_text).expanduser().resolve()
    if not snapshot_path.is_file():
        raise ListingImageRankingError(f"primary source snapshot not found: {snapshot_path}")
    try:
        return source_snapshot_from_json(snapshot_path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ListingImageRankingError(f"invalid primary source snapshot: {snapshot_path}") from exc


def _infer_target_product(
    provider: JSONTaskProvider,
    snapshot: SourceSnapshot,
) -> ProductIdentity:
    with usage_request_context(
        task="infer_grounded_supplier_product_identity",
        provider=str(getattr(provider, "name", "")),
        model=str(getattr(provider, "model", "")),
    ):
        return infer_product_identity(provider, snapshot, image_paths=())


def _visual_facts_schema(candidate_ids: list[str]) -> dict[str, Any]:
    fact_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "visual_subject": {"type": "string", "minLength": 1},
            "readable_identity": {"type": "string"},
            "raw_colour_materials": {"type": "string"},
            "design_configuration": {"type": "string"},
            "neutral_product_guess": {"type": "string"},
            "visual_uncertainty": {"type": "string"},
            "presentation_quality": {"type": "string"},
        },
        "required": [
            "visual_subject",
            "readable_identity",
            "raw_colour_materials",
            "design_configuration",
            "neutral_product_guess",
            "visual_uncertainty",
            "presentation_quality",
        ],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "facts": {
                "type": "object",
                "additionalProperties": False,
                "properties": {image_id: fact_schema for image_id in candidate_ids},
                "required": candidate_ids,
            },
            "summary": {"type": "string"},
        },
        "required": ["facts", "summary"],
    }


def build_listing_image_visual_facts_request(
    *,
    candidates: list[_Candidate],
) -> dict[str, Any]:
    """Observe candidate pixels without exposing the target product."""

    candidate_ids = [candidate.image_id for candidate in candidates]
    return {
        "task": "observe_supplier_listing_images_blind",
        "system_instruction": (
            "You are a target-blind visual observer for ecommerce images. You are deliberately NOT given the product "
            "the user wants. Describe only what the supplied pixels support, independently for each image. Do not decide "
            "whether any image matches a target. JSON only."
        ),
        "prompt_instruction": (
            "Record neutral visual facts before any target-aware reasoning exists. Describe literal colour/material "
            "appearance, form, controls, attachments, bundle composition, packaging, readable markings and distinctive "
            "design. Do NOT invent or normalize a marketing colourway/variant name from appearance alone. A name such as "
            "Ceramic Pink, Nickel/Copper, Rose Gold or another seller variant may be written as an identity/variant name "
            "only when that exact wording is readable in the pixels. Otherwise use literal appearance such as pale pink "
            "body, metallic copper accents, silver barrel, rose-gold-toned finish."
        ),
        "context": {
            "candidate_image_ids": candidate_ids,
            "decision_protocol": _BLIND_VISUAL_FACTS_PROTOCOL,
        },
        "rules": [
            "No target_product, target model, target variant or desired answer is available in this task; never assume one.",
            "visual_subject states the depicted object/media in neutral terms.",
            "readable_identity contains only text/branding/model/variant/count markings actually readable in pixels; otherwise empty.",
            "raw_colour_materials uses literal visual descriptors, not inferred seller marketing names unless those names are readable.",
            "design_configuration records silhouette, geometry, controls, attachments, kit/bundle composition, packaging structure and other visible distinguishing design facts.",
            "neutral_product_guess may identify a likely brand/product family/model from independent visual recognition, but must state uncertainty when exact identification is not reliable and must not manufacture a variant name from colour similarity.",
            "visual_uncertainty records visually unresolved identity, model, variant, colour/material or configuration ambiguity.",
            "presentation_quality records whether the image is a clear hero, alternate angle, packaging/detail/in-use view, crop, collage, duplicate-like view or page asset; this is descriptive, not target-aware ranking.",
            "Do not use filenames, URLs, source order, DOM text or nearby page copy as visual facts.",
            "Return one facts object for every candidate_image_id and no others.",
        ],
        "target_fields": [],
        "grounded_sources": [candidate.as_source() for candidate in candidates],
        "json_contract": _visual_facts_schema(candidate_ids),
        "strict_json_schema": True,
    }


def _parse_visual_facts(raw: Any, candidates: list[_Candidate]) -> _ParsedVisualFacts:
    if not isinstance(raw, dict):
        raise ListingImageRankingError("blind visual facts AI returned no JSON object")
    expected = [candidate.image_id for candidate in candidates]
    raw_facts = raw.get("facts")
    if not isinstance(raw_facts, dict) or set(raw_facts) != set(expected):
        raise ListingImageRankingError(
            "blind visual facts AI did not return the exact candidate partition"
        )

    facts: dict[str, ImageVisualFacts] = {}
    fields = (
        "visual_subject",
        "readable_identity",
        "raw_colour_materials",
        "design_configuration",
        "neutral_product_guess",
        "visual_uncertainty",
        "presentation_quality",
    )
    for image_id in expected:
        item = raw_facts.get(image_id)
        if not isinstance(item, dict):
            raise ListingImageRankingError(f"{image_id} blind visual facts must be an object")
        for field in fields:
            if field not in item or not isinstance(item.get(field), str):
                raise ListingImageRankingError(f"{image_id}.{field} must be a string")
        subject = _compact_text(item.get("visual_subject"), 240)
        if not subject:
            raise ListingImageRankingError(f"{image_id}.visual_subject is required")
        facts[image_id] = ImageVisualFacts(
            image_id=image_id,
            visual_subject=subject,
            readable_identity=_compact_text(item.get("readable_identity"), 280),
            raw_colour_materials=_compact_text(item.get("raw_colour_materials"), 360),
            design_configuration=_compact_text(item.get("design_configuration"), 600),
            neutral_product_guess=_compact_text(item.get("neutral_product_guess"), 360),
            visual_uncertainty=_compact_text(item.get("visual_uncertainty"), 420),
            presentation_quality=_compact_text(item.get("presentation_quality"), 360),
        )
    return _ParsedVisualFacts(
        facts=facts,
        summary=_compact_text(raw.get("summary"), 1200),
    )


def _run_visual_facts_request(
    provider: JSONTaskProvider,
    request: dict[str, Any],
    candidates: list[_Candidate],
) -> _ParsedVisualFacts:
    if not candidates:
        return _ParsedVisualFacts(facts={}, summary="", model_calls=0)

    parsed_batches: list[_ParsedVisualFacts] = []
    attempted_calls = 0
    for start in range(0, len(candidates), VISUAL_FACTS_BATCH_SIZE):
        batch = candidates[start : start + VISUAL_FACTS_BATCH_SIZE]
        batch_ids = [candidate.image_id for candidate in batch]
        batch_request = dict(request)
        context = dict(request.get("context") or {})
        context["candidate_image_ids"] = batch_ids
        batch_request["context"] = context
        batch_request["grounded_sources"] = [candidate.as_source() for candidate in batch]
        batch_request["json_contract"] = _visual_facts_schema(batch_ids)

        attempted_calls += 1
        try:
            with usage_request_context(
                task=str(batch_request.get("task") or ""),
                provider=str(getattr(provider, "name", "")),
                model=str(getattr(provider, "model", "")),
            ):
                raw = provider.extract_json(batch_request)
            parsed_batches.append(_parse_visual_facts(raw, batch))
        except Exception as exc:
            try:
                setattr(exc, "listing_image_visual_facts_model_calls", attempted_calls)
            except Exception:
                pass
            raise

    merged: dict[str, ImageVisualFacts] = {}
    summaries: list[str] = []
    for parsed in parsed_batches:
        for image_id, fact in parsed.facts.items():
            if image_id in merged:
                raise ListingImageRankingError(
                    f"duplicate blind visual facts across batches: {image_id}"
                )
            merged[image_id] = fact
        if parsed.summary:
            summaries.append(parsed.summary)

    expected = [candidate.image_id for candidate in candidates]
    if set(merged) != set(expected):
        raise ListingImageRankingError(
            "batched blind visual facts did not cover the exact candidate partition"
        )
    return _ParsedVisualFacts(
        facts={image_id: merged[image_id] for image_id in expected},
        summary=" | ".join(summaries),
        model_calls=attempted_calls,
    )


def _identity_audit_rules() -> list[str]:
    return [
        "Use only target_product plus the frozen blind_visual_facts as candidate identity evidence. You cannot inspect pixels in this stage.",
        "target_conflicts records affirmative contradictions between frozen facts and target product family/model, sellable variant, colour/material finish, size/count, bundle/configuration or included-item identity.",
        "A material target_conflicts value has veto priority: similarities never cancel a visible contradiction already present in frozen facts.",
        "target_match_evidence records affirmative frozen facts that positively establish the exact target sale unit and supported variant/configuration.",
        "target_identity_gaps records material unresolved identity after conflict/evidence auditing.",
        "Generic visual terms are not exact seller-variant proof. For example a frozen fact saying only pink, silver, gold, dark, light or metallic cannot be silently renamed to the target's marketing colourway.",
        "If the target names a sellable colourway/material/configuration and the frozen facts do not positively establish its defining visible traits, keep a target_identity_gaps value rather than assuming agreement.",
        "Readable model text is not mandatory when frozen blind facts contain a sufficiently discriminative design fingerprint for the exact product/model.",
        "Brand match, category match, colour family alone, generic family resemblance, or absence of contradiction is not sufficient positive proof.",
        "Do not reinterpret neutral_product_guess or raw_colour_materials to make them fit the target. Compare them as frozen observations.",
    ]


def _ownership_schema(candidate_ids: list[str]) -> dict[str, Any]:
    decision_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "target_conflicts": {"type": "string"},
            "target_match_evidence": {"type": "string"},
            "target_identity_gaps": {"type": "string"},
            "classification": {"type": "string", "enum": list(_IMAGE_OWNERSHIP_CLASSES)},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reason": {"type": "string", "minLength": 1},
        },
        "required": [
            "target_conflicts",
            "target_match_evidence",
            "target_identity_gaps",
            "classification",
            "confidence",
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
                "properties": {image_id: decision_schema for image_id in candidate_ids},
                "required": candidate_ids,
            },
            "summary": {"type": "string"},
        },
        "required": ["decisions", "summary"],
    }


def build_listing_image_ownership_request(
    *,
    product_context: dict[str, Any],
    candidates: list[_Candidate],
    visual_facts: _ParsedVisualFacts | None = None,
) -> dict[str, Any]:
    candidate_ids = [candidate.image_id for candidate in candidates]
    frozen = {
        image_id: visual_facts.facts[image_id].as_dict()
        for image_id in candidate_ids
        if visual_facts is not None and image_id in visual_facts.facts
    }
    return {
        "task": "compare_blind_image_facts_to_target",
        "system_instruction": (
            "You are the target comparator for ecommerce Product Photos. Candidate perception has already been completed "
            "in a separate target-blind multimodal stage. You receive only frozen blind visual facts and the grounded "
            "target identity. Never imagine new pixel facts and never reinterpret a generic observation into a target "
            "marketing term. Compare frozen evidence to the target, then classify. JSON only."
        ),
        "prompt_instruction": (
            "For every image_id audit target_conflicts first, then target_match_evidence, then target_identity_gaps, and "
            "only then classification. The target must not leak backward into perception: blind_visual_facts are immutable. "
            "Exact seller variants require positive support from the frozen literal appearance/configuration facts; similarity "
            "or a broad colour family is insufficient."
        ),
        "context": {
            "target_product": product_context,
            "blind_visual_facts": frozen,
            "candidate_image_ids": candidate_ids,
            "decision_protocol": _IMAGE_OWNERSHIP_PROTOCOL,
            "auto_eligible_classifications": sorted(_AUTO_ELIGIBLE_OWNERSHIP_CLASSES),
        },
        "rules": [
            *_identity_audit_rules(),
            "EXACT_TARGET requires target_conflicts empty, non-empty target_match_evidence that positively establishes exact product and supported variant/configuration, and target_identity_gaps empty.",
            "TARGET_PACKAGING_OR_DETAIL requires target_conflicts empty and frozen facts positively tying the packaging/detail/included component to the exact target sale unit, with target_identity_gaps empty.",
            "Use SAME_PRODUCT_OTHER_VARIANT when frozen facts establish the same product family but a different sellable colourway/material finish/size/model/count/bundle/configuration.",
            "Use OTHER_PRODUCT when frozen facts establish a different product line or different sellable product, including under the same brand/category.",
            "Use PAGE_ASSET when frozen facts describe non-product page media.",
            "Use UNCERTAIN when no material conflict is established but exact target identity or variant remains insufficiently proven.",
            "Return one decision for every candidate_image_id and no others.",
        ],
        "target_fields": [],
        "grounded_sources": [],
        "json_contract": _ownership_schema(candidate_ids),
        "strict_json_schema": True,
    }


def _parse_ownership(
    raw: Any,
    candidates: list[_Candidate],
    visual_facts: _ParsedVisualFacts | None = None,
) -> _ParsedOwnership:
    if not isinstance(raw, dict):
        raise ListingImageRankingError("listing image ownership AI returned no JSON object")
    expected = [candidate.image_id for candidate in candidates]
    raw_decisions = raw.get("decisions")
    if not isinstance(raw_decisions, dict) or set(raw_decisions) != set(expected):
        raise ListingImageRankingError(
            "listing image ownership AI did not return the exact candidate decision partition"
        )

    decisions: dict[str, ImageOwnershipDecision] = {}
    for image_id in expected:
        item = raw_decisions.get(image_id)
        if not isinstance(item, dict):
            raise ListingImageRankingError(f"{image_id} ownership decision must be an object")
        for field in ("target_conflicts", "target_match_evidence", "target_identity_gaps"):
            if field not in item or not isinstance(item.get(field), str):
                raise ListingImageRankingError(f"{image_id}.{field} must be a string")
        classification = _compact_text(item.get("classification"), 80).upper()
        if classification not in _IMAGE_OWNERSHIP_CLASSES:
            raise ListingImageRankingError(
                f"{image_id}.classification is invalid: {classification!r}"
            )
        try:
            confidence = float(item.get("confidence"))
        except (TypeError, ValueError) as exc:
            raise ListingImageRankingError(f"{image_id}.confidence must be numeric") from exc
        if not 0.0 <= confidence <= 1.0:
            raise ListingImageRankingError(f"{image_id}.confidence must be within 0..1")

        conflicts = _compact_text(item.get("target_conflicts"), 600)
        match_evidence = _compact_text(item.get("target_match_evidence"), 600)
        identity_gaps = _compact_text(item.get("target_identity_gaps"), 600)
        if classification in _AUTO_ELIGIBLE_OWNERSHIP_CLASSES:
            if conflicts:
                raise ListingImageRankingError(
                    f"{image_id} auto-eligible classification contradicts declared target_conflicts"
                )
            if not match_evidence:
                raise ListingImageRankingError(
                    f"{image_id} auto-eligible classification requires target_match_evidence"
                )
            if identity_gaps:
                raise ListingImageRankingError(
                    f"{image_id} auto-eligible classification contradicts declared target_identity_gaps"
                )
        reason = _compact_text(item.get("reason"), 800)
        if not reason:
            raise ListingImageRankingError(f"{image_id}.reason is required")

        fact = visual_facts.facts.get(image_id) if visual_facts is not None else None
        visible_configuration = ""
        if fact is not None:
            visible_configuration = _compact_text(
                "; ".join(
                    value
                    for value in (
                        fact.raw_colour_materials,
                        fact.design_configuration,
                        fact.neutral_product_guess,
                    )
                    if value
                ),
                900,
            )
        decisions[image_id] = ImageOwnershipDecision(
            image_id=image_id,
            classification=classification,
            confidence=confidence,
            reason=reason,
            visual_subject=fact.visual_subject if fact is not None else "",
            visible_identity=fact.readable_identity if fact is not None else "",
            visible_configuration=visible_configuration,
            target_conflicts=conflicts,
            target_match_evidence=match_evidence,
            target_identity_gaps=identity_gaps,
        )
    return _ParsedOwnership(
        decisions=decisions,
        summary=_compact_text(raw.get("summary"), 1200),
    )


def _run_ownership_request(
    provider: JSONTaskProvider,
    request: dict[str, Any],
    candidates: list[_Candidate],
    visual_facts: _ParsedVisualFacts | None = None,
) -> _ParsedOwnership:
    with usage_request_context(
        task=str(request.get("task") or ""),
        provider=str(getattr(provider, "name", "")),
        model=str(getattr(provider, "model", "")),
    ):
        raw = provider.extract_json(request)
    return _parse_ownership(raw, candidates, visual_facts)


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
    product_context: dict[str, Any] | None = None,
    candidates: list[_Candidate],
    ownership: _ParsedOwnership | None = None,
    visual_facts: _ParsedVisualFacts | None = None,
) -> dict[str, Any]:
    """Rank only identity-approved images; target identity is intentionally absent."""

    candidate_ids = [candidate.image_id for candidate in candidates]
    ownership_context = {
        image_id: ownership.decisions[image_id].as_dict()
        for image_id in candidate_ids
        if ownership is not None and image_id in ownership.decisions
    }
    frozen = {
        image_id: visual_facts.facts[image_id].as_dict()
        for image_id in candidate_ids
        if visual_facts is not None and image_id in visual_facts.facts
    }
    return {
        "task": "order_identity_approved_supplier_gallery",
        "system_instruction": (
            "You are the gallery quality/order stage. Product identity membership is already frozen by the Ownership "
            "comparator. You are deliberately NOT given target_product and must not reclassify product identity or variant. "
            "Choose the clearest, most useful, non-redundant presentation among the supplied approved candidates. JSON only."
        ),
        "prompt_instruction": (
            "Select at most five approved candidates and order them for upload. Use actual pixels for composition, clarity, "
            "hero suitability and semantic near-duplicate judgment. Use frozen blind facts for presentation context. Never "
            "fill a quota; fewer useful images are valid."
        ),
        "context": {
            "ownership_decisions": ownership_context,
            "blind_visual_facts": frozen,
            "candidate_image_ids": candidate_ids,
            "decision_protocol": _GALLERY_PROTOCOL,
        },
        "rules": [
            "Do not infer, verify or alter target identity; every candidate here already passed the sole identity gate.",
            "Inspect pixels only for photo quality, composition, hero suitability, detail usefulness, misleading crop/collage risk and semantic duplication.",
            "Position 1 should be the clearest complete product/approved sale-unit hero image when one exists.",
            "Supporting images may add useful alternate views, packaging, details, dimensions, included components or in-use context.",
            "Treat visual duplication and near-duplication as semantic judgment and keep the better or more informative version.",
            "Reject page-like, unusably cropped, misleading or redundant presentation even if identity was approved.",
            "selected_image_ids is the final gallery order, contains at most five unique image_ids and may be empty.",
            "Return one decision for every candidate_image_id. selected=true must exactly match membership in selected_image_ids.",
        ],
        "target_fields": [],
        "grounded_sources": [candidate.as_source() for candidate in candidates],
        "json_contract": _ranking_schema(candidate_ids),
        "strict_json_schema": True,
    }


def _parse_ranking(raw: Any, candidates: list[_Candidate]) -> _ParsedRanking:
    if not isinstance(raw, dict):
        raise ListingImageRankingError("listing image gallery AI returned no JSON object")
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
        raise ListingImageRankingError(
            "listing image gallery AI did not return the exact candidate decision partition"
        )
    selected_set = set(selected_ids)
    decisions: dict[str, RankedImageDecision] = {}
    for image_id in expected:
        item = raw_decisions.get(image_id)
        if not isinstance(item, dict):
            raise ListingImageRankingError(f"{image_id} gallery decision must be an object")
        selected = item.get("selected")
        if type(selected) is not bool:
            raise ListingImageRankingError(f"{image_id}.selected must be boolean")
        if selected != (image_id in selected_set):
            raise ListingImageRankingError(
                f"{image_id}.selected disagrees with selected_image_ids"
            )
        reason = _compact_text(item.get("reason"), 800)
        if not reason:
            raise ListingImageRankingError(f"{image_id}.reason is required")
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


def _run_ranking_request(
    provider: JSONTaskProvider,
    request: dict[str, Any],
    candidates: list[_Candidate],
) -> _ParsedRanking:
    with usage_request_context(
        task=str(request.get("task") or ""),
        provider=str(getattr(provider, "name", "")),
        model=str(getattr(provider, "model", "")),
    ):
        raw = provider.extract_json(request)
    return _parse_ranking(raw, candidates)


def _selection_report(
    *,
    candidates: list[_Candidate],
    transport_rejected: list[dict[str, Any]],
    identity: ProductIdentity | None,
    visual_facts: _ParsedVisualFacts | None,
    ownership: _ParsedOwnership | None,
    ranking: _ParsedRanking | None,
    status: str,
    error: BaseException | None = None,
) -> dict[str, Any]:
    by_id = {candidate.image_id: candidate for candidate in candidates}
    selected_ids = list(ranking.selected_ids) if ranking is not None else []
    return {
        "schema_version": 9,
        "status": status,
        "policy": {
            "version": LISTING_IMAGE_RANKING_VERSION,
            "kind": "target_blind_perception_then_target_comparison_then_quality_gallery",
            "max_auto_listing_images": MAX_AUTO_LISTING_IMAGES,
            "visual_facts_batch_size": VISUAL_FACTS_BATCH_SIZE,
            "pre_ai_filter": "explicit-ownership-boundary-plus-decodable-image-and-exact-byte-duplicate-only",
            "target_identity_source": "product-focused-structured-supplier-evidence-without-candidate-images",
            "blind_visual_facts_protocol": _BLIND_VISUAL_FACTS_PROTOCOL,
            "blind_visual_facts_target_visible": False,
            "ownership_decision_protocol": _IMAGE_OWNERSHIP_PROTOCOL,
            "ownership_semantic_owner": "text_ai_comparator_over_frozen_blind_visual_facts",
            "ownership_candidate_pixels_visible": False,
            "gallery_decision_protocol": _GALLERY_PROTOCOL,
            "gallery_semantic_owner": "quality_and_order_only_identity_frozen",
            "gallery_target_identity_visible": False,
            "auto_eligible_ownership_classes": sorted(_AUTO_ELIGIBLE_OWNERSHIP_CLASSES),
            "precision_policy": "fewer_correct_images_over_quota_fill",
            "semantic_failure": "fail_closed_empty_automatic_gallery",
            "program_semantic_fallback": "none",
        },
        "target_product_identity": identity.as_dict() if identity is not None else None,
        "candidate_count": len(candidates),
        "transport_rejected": transport_rejected,
        "blind_visual_facts_summary": visual_facts.summary if visual_facts is not None else "",
        "ownership_summary": ownership.summary if ownership is not None else "",
        "gallery_summary": ranking.summary if ranking is not None else "",
        "selected_ids": selected_ids,
        "selected": [str(by_id[image_id].path) for image_id in selected_ids if image_id in by_id],
        "candidates": [
            {
                "image_id": candidate.image_id,
                "path": str(candidate.path),
                "source_index": candidate.source_index,
                "sha256": candidate.sha256,
                "blind_visual_facts": (
                    visual_facts.facts[candidate.image_id].as_dict()
                    if visual_facts is not None and candidate.image_id in visual_facts.facts
                    else None
                ),
                "ownership": (
                    ownership.decisions[candidate.image_id].as_dict()
                    if ownership is not None and candidate.image_id in ownership.decisions
                    else None
                ),
                "gallery": (
                    ranking.decisions[candidate.image_id].as_dict()
                    if ranking is not None and candidate.image_id in ranking.decisions
                    else None
                ),
            }
            for candidate in candidates
        ],
        "error": (
            {"type": type(error).__name__, "message": _compact_text(error, 1600)}
            if error is not None
            else None
        ),
    }


def _publish_manifest_state(
    *,
    manifest_path: Path,
    manifest: dict[str, Any],
    outputs: dict[str, Any],
    selection_path: Path,
    selected_paths: tuple[Path, ...],
    status: str,
    model_calls: int,
    candidates: list[_Candidate],
    transport_rejected: list[dict[str, Any]],
    ownership: _ParsedOwnership | None,
    ranking: _ParsedRanking | None,
    error: BaseException | None = None,
) -> None:
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

    eligible_count = len(ownership.eligible_ids) if ownership is not None else 0
    manifest["listing_image_ranking"] = {
        "version": LISTING_IMAGE_RANKING_VERSION,
        "status": status,
        "strategy": "target_blind_perception_then_frozen_facts_ownership_then_quality_gallery",
        "blind_visual_facts_protocol": _BLIND_VISUAL_FACTS_PROTOCOL,
        "ownership_decision_protocol": _IMAGE_OWNERSHIP_PROTOCOL,
        "gallery_decision_protocol": _GALLERY_PROTOCOL,
        "precision_first": True,
        "model_calls": model_calls,
        "candidate_count": len(candidates),
        "transport_rejected_count": len(transport_rejected),
        "ownership_eligible_count": eligible_count,
        "semantic_rejected_count": len(candidates) - len(selected_paths),
        "selected_count": len(selected_paths),
        "selected": [str(path) for path in selected_paths],
        "selected_ids": list(ranking.selected_ids) if ranking is not None else [],
        "selection_report": str(selection_path),
        "error": (
            {"type": type(error).__name__, "message": _compact_text(error, 1600)}
            if error is not None
            else None
        ),
    }
    manifest["total_model_calls"] = int(manifest.get("total_model_calls") or 0) + model_calls
    _write_json_atomic(manifest_path, manifest)


def finalize_supplier_listing_images(
    run_dir: str | Path,
    provider: JSONTaskProvider,
) -> ListingImageRankingResult:
    """Select automatic Product Photos without exposing target identity during perception."""

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

    visual_facts: _ParsedVisualFacts | None = None
    if not candidates:
        report = _selection_report(
            candidates=candidates,
            transport_rejected=transport_rejected,
            identity=None,
            visual_facts=None,
            ownership=None,
            ranking=None,
            status="no_transport_candidates",
        )
        _write_json_atomic(selection_path, report)
        _publish_manifest_state(
            manifest_path=manifest_path,
            manifest=manifest,
            outputs=outputs,
            selection_path=selection_path,
            selected_paths=(),
            status="no_transport_candidates",
            model_calls=0,
            candidates=candidates,
            transport_rejected=transport_rejected,
            ownership=None,
            ranking=None,
        )
        return ListingImageRankingResult(
            status="no_transport_candidates",
            selected=(),
            model_calls=0,
            mechanical_candidate_count=0,
            semantically_rejected_count=0,
            selection_report=selection_path,
        )

    model_calls = 0
    identity: ProductIdentity | None = None
    ownership: _ParsedOwnership | None = None
    ranking: _ParsedRanking | None = None

    try:
        snapshot = _source_snapshot(manifest)
        model_calls += 1
        identity = _infer_target_product(provider, snapshot)
        product_context = identity.as_dict()

        visual_request = build_listing_image_visual_facts_request(candidates=candidates)
        try:
            visual_facts = _run_visual_facts_request(provider, visual_request, candidates)
        except Exception as exc:
            model_calls += int(getattr(exc, "listing_image_visual_facts_model_calls", 1) or 1)
            raise
        model_calls += visual_facts.model_calls

        ownership_request = build_listing_image_ownership_request(
            product_context=product_context,
            candidates=candidates,
            visual_facts=visual_facts,
        )
        model_calls += 1
        ownership = _run_ownership_request(
            provider,
            ownership_request,
            candidates,
            visual_facts,
        )

        eligible_set = set(ownership.eligible_ids)
        eligible_candidates = [
            candidate for candidate in candidates if candidate.image_id in eligible_set
        ]
        if eligible_candidates:
            ranking_request = build_listing_image_ranking_request(
                candidates=eligible_candidates,
                ownership=ownership,
                visual_facts=visual_facts,
            )
            model_calls += 1
            ranking = _run_ranking_request(provider, ranking_request, eligible_candidates)
            by_id = {candidate.image_id: candidate for candidate in eligible_candidates}
            selected_paths = tuple(by_id[image_id].path for image_id in ranking.selected_ids)
            status = "ai_ranked" if selected_paths else "ai_ranked_empty"
        else:
            selected_paths = ()
            status = "ai_ownership_empty"

        report = _selection_report(
            candidates=candidates,
            transport_rejected=transport_rejected,
            identity=identity,
            visual_facts=visual_facts,
            ownership=ownership,
            ranking=ranking,
            status=status,
        )
        _write_json_atomic(selection_path, report)
        _publish_manifest_state(
            manifest_path=manifest_path,
            manifest=manifest,
            outputs=outputs,
            selection_path=selection_path,
            selected_paths=selected_paths,
            status=status,
            model_calls=model_calls,
            candidates=candidates,
            transport_rejected=transport_rejected,
            ownership=ownership,
            ranking=ranking,
        )
    except Exception as exc:
        report = _selection_report(
            candidates=candidates,
            transport_rejected=transport_rejected,
            identity=identity,
            visual_facts=visual_facts,
            ownership=ownership,
            ranking=ranking,
            status="failed_closed",
            error=exc,
        )
        _write_json_atomic(selection_path, report)
        _publish_manifest_state(
            manifest_path=manifest_path,
            manifest=manifest,
            outputs=outputs,
            selection_path=selection_path,
            selected_paths=(),
            status="failed_closed",
            model_calls=model_calls,
            candidates=candidates,
            transport_rejected=transport_rejected,
            ownership=ownership,
            ranking=ranking,
            error=exc,
        )
        raise

    return ListingImageRankingResult(
        status=status,
        selected=selected_paths,
        model_calls=model_calls,
        mechanical_candidate_count=len(candidates),
        semantically_rejected_count=len(candidates) - len(selected_paths),
        selection_report=selection_path,
    )


__all__ = [
    "LISTING_IMAGE_RANKING_VERSION",
    "MAX_AUTO_LISTING_IMAGES",
    "VISUAL_FACTS_BATCH_SIZE",
    "OWNERSHIP_BATCH_SIZE",
    "ImageVisualFacts",
    "ImageOwnershipDecision",
    "ListingImageRankingError",
    "ListingImageRankingResult",
    "RankedImageDecision",
    "build_listing_image_visual_facts_request",
    "build_listing_image_ownership_request",
    "build_listing_image_ranking_request",
    "finalize_supplier_listing_images",
]
