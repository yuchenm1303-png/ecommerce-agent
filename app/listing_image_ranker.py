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


LISTING_IMAGE_RANKING_VERSION = 8
MAX_AUTO_LISTING_IMAGES = 5
OWNERSHIP_BATCH_SIZE = 6

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
_IMAGE_OWNERSHIP_PROTOCOL = "pixel_facts_conflict_audit_positive_proof_classification_v5"
_GALLERY_PROTOCOL = "independent_conflict_first_gallery_verification_v1"


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
    target_conflicts: str = ""
    target_match_evidence: str = ""
    target_identity_gaps: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "image_id": self.image_id,
            "target_conflicts": self.target_conflicts,
            "target_match_evidence": self.target_match_evidence,
            "target_identity_gaps": self.target_identity_gaps,
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
    """Return images owned by customer auxiliary evidence, not supplier auto-gallery intent."""

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
    """Build the broad candidate universe using transport facts only."""

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
    """Create the image target from product-focused evidence, never candidate pixels."""

    with usage_request_context(
        task="infer_grounded_supplier_product_identity",
        provider=str(getattr(provider, "name", "")),
        model=str(getattr(provider, "model", "")),
    ):
        return infer_product_identity(provider, snapshot, image_paths=())


def _identity_audit_rules() -> list[str]:
    """One semantic identity protocol shared by Ownership and Gallery."""

    return [
        "Evaluate identity in this strict order: observe pixels, audit target conflicts, record positive target evidence, record residual ambiguity, then decide.",
        "target_conflicts must list affirmative pixel-visible contradictions against the target's product family/model, sellable variant, colourway/material finish, size/count, bundle/configuration or included-item identity; use an empty string only when no such contradiction is visible.",
        "A material target_conflicts value has veto priority: positive similarities must never cancel or average away a visible product/model/variant/configuration conflict.",
        "target_match_evidence must contain only affirmative pixel-grounded evidence for the exact target. It may combine silhouette, industrial design, geometry, controls, attachment layout, kit composition, materials, colourway, packaging structure and readable markings.",
        "Readable branding or model text is useful but not mandatory. A catalog model may be established by a sufficiently discriminative non-text visual fingerprint.",
        "target_identity_gaps must contain only material residual ambiguity after conflict and positive-evidence auditing. Missing or unreadable logo/model text alone is not a gap when the exact represented product and sellable variant are otherwise visually established.",
        "Brand match, category match, colour alone, generic family resemblance, or absence of contradiction is never sufficient positive proof.",
        "Do not collapse distinct sellable colourways, material finishes, models or configurations into a broad visual family. If a target variant is visually distinguishable, its defining visible traits must positively agree.",
        "Absence of conflict is not evidence of identity. Positive proof must come from affirmative visible features.",
        "Do not use candidate/source order, DOM text, nearby page copy, filenames or URLs as semantic evidence.",
    ]


def _ownership_schema(candidate_ids: list[str]) -> dict[str, Any]:
    decision_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "visual_subject": {"type": "string", "minLength": 1},
            "visible_identity": {"type": "string"},
            "visible_configuration": {"type": "string"},
            "target_conflicts": {"type": "string"},
            "target_match_evidence": {"type": "string"},
            "target_identity_gaps": {"type": "string"},
            "classification": {"type": "string", "enum": list(_IMAGE_OWNERSHIP_CLASSES)},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reason": {"type": "string", "minLength": 1},
        },
        "required": [
            "visual_subject",
            "visible_identity",
            "visible_configuration",
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
) -> dict[str, Any]:
    """Observe -> conflict audit -> positive proof -> residual ambiguity -> classification."""

    candidate_ids = [candidate.image_id for candidate in candidates]
    return {
        "task": "classify_supplier_listing_image_ownership",
        "system_instruction": (
            "You are the semantic ownership gate for ecommerce Product Photos. Use one conflict-first identity "
            "protocol for every candidate: observe only the pixels, explicitly audit contradictions against the exact "
            "grounded target, then record affirmative target evidence and any residual ambiguity, and only then classify. "
            "A visible model/variant/configuration contradiction always outranks visual similarity. JSON only."
        ),
        "prompt_instruction": (
            "For each image_id fill visual_subject, visible_identity and visible_configuration from pixels. Next fill "
            "target_conflicts BEFORE target_match_evidence. Then fill target_identity_gaps and classification. A readable "
            "logo/model code is not required when a distinctive visual fingerprint establishes the exact product and "
            "variant, but a conflicting visible product shape, sellable colourway/material finish, model, bundle or "
            "configuration can never be overridden by an otherwise similar fingerprint."
        ),
        "context": {
            "target_product": product_context,
            "candidate_image_ids": candidate_ids,
            "decision_protocol": _IMAGE_OWNERSHIP_PROTOCOL,
            "auto_eligible_classifications": sorted(_AUTO_ELIGIBLE_OWNERSHIP_CLASSES),
        },
        "rules": [
            "Inspect the actual pixels of every supplied candidate before classifying it.",
            "visual_subject must briefly state what product/object/media the pixels depict without asserting target ownership.",
            "visible_identity contains only readable brand/model/size/count/variant markings; use an empty string when none is readable.",
            "visible_configuration records visible colourway/material finish, form, controls, package count, bundle/kit composition, attachments, packaging/detail context and other distinguishing design facts.",
            "Visual facts are observations, not conclusions: never copy unsupported target_product facts into them.",
            *_identity_audit_rules(),
            "EXACT_TARGET requires: target_conflicts is empty; target_match_evidence positively establishes the exact sellable target product and supported variant/configuration; target_identity_gaps is empty.",
            "TARGET_PACKAGING_OR_DETAIL requires: target_conflicts is empty; the pixels positively tie the packaging/detail/included component to this exact target sale unit; target_identity_gaps is empty. Generic same-brand accessories or packaging are insufficient.",
            "Use SAME_PRODUCT_OTHER_VARIANT when the pixels positively establish the same product family but a different sellable colourway, material finish, size, model, count, flavour, bundle or configuration.",
            "Use OTHER_PRODUCT when the visible product identity or form establishes a different sellable product, including a different product line even under the same brand.",
            "Use PAGE_ASSET for logos, banners, navigation graphics, seller decoration, advertisements or other non-product page media.",
            "Use UNCERTAIN when no material conflict is established but the positive evidence is still insufficient to distinguish the exact target from plausible alternatives.",
            "Return one decision for every candidate_image_id and no others.",
        ],
        "target_fields": [],
        "grounded_sources": [candidate.as_source() for candidate in candidates],
        "json_contract": _ownership_schema(candidate_ids),
        "strict_json_schema": True,
    }


def _parse_ownership(raw: Any, candidates: list[_Candidate]) -> _ParsedOwnership:
    if not isinstance(raw, dict):
        raise ListingImageRankingError("listing image ownership AI returned no JSON object")

    expected = [candidate.image_id for candidate in candidates]
    expected_set = set(expected)
    raw_decisions = raw.get("decisions")
    if not isinstance(raw_decisions, dict) or set(raw_decisions) != expected_set:
        raise ListingImageRankingError(
            "listing image ownership AI did not return the exact candidate decision partition"
        )

    decisions: dict[str, ImageOwnershipDecision] = {}
    for image_id in expected:
        item = raw_decisions.get(image_id)
        if not isinstance(item, dict):
            raise ListingImageRankingError(f"{image_id} ownership decision must be an object")
        for field in (
            "visual_subject",
            "visible_identity",
            "visible_configuration",
            "target_conflicts",
            "target_match_evidence",
            "target_identity_gaps",
        ):
            if field not in item or not isinstance(item.get(field), str):
                raise ListingImageRankingError(f"{image_id}.{field} must be a string")

        visual_subject = _compact_text(item.get("visual_subject"), 240)
        if not visual_subject:
            raise ListingImageRankingError(f"{image_id}.visual_subject is required")
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
        decisions[image_id] = ImageOwnershipDecision(
            image_id=image_id,
            classification=classification,
            confidence=confidence,
            reason=reason,
            visual_subject=visual_subject,
            visible_identity=_compact_text(item.get("visible_identity"), 240),
            visible_configuration=_compact_text(item.get("visible_configuration"), 320),
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
) -> _ParsedOwnership:
    """Execute the same Ownership contract in bounded transport batches."""

    if not candidates:
        return _ParsedOwnership(decisions={}, summary="", model_calls=0)

    parsed_batches: list[_ParsedOwnership] = []
    attempted_calls = 0
    for start in range(0, len(candidates), OWNERSHIP_BATCH_SIZE):
        batch = candidates[start : start + OWNERSHIP_BATCH_SIZE]
        batch_ids = [candidate.image_id for candidate in batch]
        batch_request = dict(request)
        context = dict(request.get("context") or {})
        context["candidate_image_ids"] = batch_ids
        batch_request["context"] = context
        batch_request["grounded_sources"] = [candidate.as_source() for candidate in batch]
        batch_request["json_contract"] = _ownership_schema(batch_ids)

        attempted_calls += 1
        try:
            with usage_request_context(
                task=str(batch_request.get("task") or ""),
                provider=str(getattr(provider, "name", "")),
                model=str(getattr(provider, "model", "")),
            ):
                raw = provider.extract_json(batch_request)
            parsed_batches.append(_parse_ownership(raw, batch))
        except Exception as exc:
            try:
                setattr(exc, "listing_image_ownership_model_calls", attempted_calls)
            except Exception:
                pass
            raise

    merged_decisions: dict[str, ImageOwnershipDecision] = {}
    summaries: list[str] = []
    for parsed in parsed_batches:
        for image_id, decision in parsed.decisions.items():
            if image_id in merged_decisions:
                raise ListingImageRankingError(
                    f"duplicate ownership decision across batches: {image_id}"
                )
            merged_decisions[image_id] = decision
        if parsed.summary:
            summaries.append(parsed.summary)

    expected_ids = [candidate.image_id for candidate in candidates]
    if set(merged_decisions) != set(expected_ids):
        raise ListingImageRankingError(
            "batched listing image ownership did not cover the exact candidate partition"
        )
    return _ParsedOwnership(
        decisions={image_id: merged_decisions[image_id] for image_id in expected_ids},
        summary=" | ".join(summaries),
        model_calls=attempted_calls,
    )


def _ranking_schema(candidate_ids: list[str]) -> dict[str, Any]:
    decision_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "target_conflicts": {"type": "string"},
            "target_match_evidence": {"type": "string"},
            "target_identity_gaps": {"type": "string"},
            "selected": {"type": "boolean"},
            "reason": {"type": "string", "minLength": 1},
        },
        "required": [
            "target_conflicts",
            "target_match_evidence",
            "target_identity_gaps",
            "selected",
            "reason",
        ],
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
    product_context: dict[str, Any],
    candidates: list[_Candidate],
    ownership: _ParsedOwnership | None = None,
) -> dict[str, Any]:
    """Independently re-audit Ownership-approved images and emit final upload order."""

    candidate_ids = [candidate.image_id for candidate in candidates]
    ownership_context = {
        candidate.image_id: (
            ownership.decisions[candidate.image_id].as_dict()
            if ownership is not None and candidate.image_id in ownership.decisions
            else {}
        )
        for candidate in candidates
    }
    return {
        "task": "verify_and_order_exact_supplier_gallery",
        "system_instruction": (
            "You are the independent final verifier for ecommerce Product Photos. Ownership decisions are prior-stage "
            "context, not proof. Reinspect every supplied image from pixels and perform the same conflict-first identity "
            "audit yourself before selection. A visible product/model/variant/configuration contradiction has veto priority "
            "and can never be averaged away by general resemblance. Return the exact final upload order. JSON only."
        ),
        "prompt_instruction": (
            "For every candidate independently fill target_conflicts first, then target_match_evidence, then "
            "target_identity_gaps, and only then selected. Select at most five. Readable model text is not mandatory when "
            "a distinctive visual fingerprint proves the exact target, but any material visible target conflict requires "
            "selected=false. Never fill a quota."
        ),
        "context": {
            "target_product": product_context,
            "ownership_decisions": ownership_context,
            "candidate_image_ids": candidate_ids,
            "decision_protocol": _GALLERY_PROTOCOL,
        },
        "rules": [
            "Inspect the actual pixels of every supplied candidate again; never inherit the Ownership conclusion as evidence.",
            *_identity_audit_rules(),
            "A selected image requires target_conflicts empty, non-empty target_match_evidence sufficient for the exact target sale unit and supported variant/configuration, and target_identity_gaps empty.",
            "If the pixels show a different sellable variant/colourway/material finish/configuration, selected must be false even when product family and most design features match.",
            "If the pixels establish a different product line or product form, selected must be false even under the same brand/category.",
            "Reject residual unrelated products, recommendations, misleading accessories and page assets even if Ownership admitted them.",
            "Position 1 should be the strongest clear main image of the exact sale unit when one exists.",
            "Supporting images may show verified details, dimensions, packaging, included components or in-use context when useful and non-redundant.",
            "Treat visual duplication and near-duplication as semantic judgment and keep the better or more informative version.",
            "selected_image_ids is the final gallery order, contains at most five unique image_ids, and may be empty.",
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

    decisions: dict[str, RankedImageDecision] = {}
    selected_set = set(selected_ids)
    for image_id in expected:
        item = raw_decisions.get(image_id)
        if not isinstance(item, dict):
            raise ListingImageRankingError(f"{image_id} gallery decision must be an object")
        for field in ("target_conflicts", "target_match_evidence", "target_identity_gaps"):
            if field not in item or not isinstance(item.get(field), str):
                raise ListingImageRankingError(f"{image_id}.{field} must be a string")

        selected = item.get("selected")
        if type(selected) is not bool:
            raise ListingImageRankingError(f"{image_id}.selected must be boolean")
        if selected != (image_id in selected_set):
            raise ListingImageRankingError(
                f"{image_id}.selected disagrees with selected_image_ids"
            )
        conflicts = _compact_text(item.get("target_conflicts"), 600)
        match_evidence = _compact_text(item.get("target_match_evidence"), 600)
        identity_gaps = _compact_text(item.get("target_identity_gaps"), 600)
        if selected:
            if conflicts:
                raise ListingImageRankingError(
                    f"{image_id} selected=true contradicts declared target_conflicts"
                )
            if not match_evidence:
                raise ListingImageRankingError(
                    f"{image_id} selected=true requires target_match_evidence"
                )
            if identity_gaps:
                raise ListingImageRankingError(
                    f"{image_id} selected=true contradicts declared target_identity_gaps"
                )

        reason = _compact_text(item.get("reason"), 800)
        if not reason:
            raise ListingImageRankingError(f"{image_id}.reason is required")
        decisions[image_id] = RankedImageDecision(
            image_id=image_id,
            selected=selected,
            reason=reason,
            target_conflicts=conflicts,
            target_match_evidence=match_evidence,
            target_identity_gaps=identity_gaps,
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
    ownership: _ParsedOwnership | None,
    ranking: _ParsedRanking | None,
    status: str,
    error: BaseException | None = None,
) -> dict[str, Any]:
    by_id = {candidate.image_id: candidate for candidate in candidates}
    selected_ids = list(ranking.selected_ids) if ranking is not None else []
    return {
        "schema_version": 8,
        "status": status,
        "policy": {
            "version": LISTING_IMAGE_RANKING_VERSION,
            "kind": "grounded-identity-two-stage-ai-listing-photo-selection",
            "max_auto_listing_images": MAX_AUTO_LISTING_IMAGES,
            "ownership_batch_size": OWNERSHIP_BATCH_SIZE,
            "pre_ai_filter": "explicit-ownership-boundary-plus-decodable-image-and-exact-byte-duplicate-only",
            "target_identity_source": "product-focused-structured-supplier-evidence-without-page-body-or-candidate-images",
            "ownership_semantic_owner": "multimodal_ai",
            "ownership_decision_protocol": _IMAGE_OWNERSHIP_PROTOCOL,
            "gallery_decision_protocol": _GALLERY_PROTOCOL,
            "identity_audit_order": "pixel_facts_then_conflicts_then_positive_evidence_then_residual_ambiguity_then_decision",
            "material_visible_conflict_veto": True,
            "ownership_visual_facts_required_by_contract": True,
            "ownership_positive_identity_proof_required": True,
            "ownership_visual_fingerprint_allowed_without_readable_model_text": True,
            "gallery_semantic_owner": "multimodal_ai_independent_reinspection",
            "auto_eligible_ownership_classes": sorted(_AUTO_ELIGIBLE_OWNERSHIP_CLASSES),
            "precision_policy": "fewer_correct_images_over_quota_fill",
            "ordering": "selected_image_ids_verbatim",
            "semantic_failure": "fail_closed_empty_automatic_gallery",
            "program_semantic_fallback": "none",
        },
        "target_product_identity": identity.as_dict() if identity is not None else None,
        "candidate_count": len(candidates),
        "transport_rejected": transport_rejected,
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
        "strategy": "grounded_identity_then_conflict_first_ownership_then_independent_gallery",
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
    """Select automatic Product Photos with two independent conflict-first AI stages."""

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
        report = _selection_report(
            candidates=candidates,
            transport_rejected=transport_rejected,
            identity=None,
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

        ownership_request = build_listing_image_ownership_request(
            product_context=product_context,
            candidates=candidates,
        )
        model_calls += 1
        ownership = _run_ownership_request(provider, ownership_request, candidates)
        model_calls += max(0, ownership.model_calls - 1)

        eligible_set = set(ownership.eligible_ids)
        eligible_candidates = [
            candidate for candidate in candidates if candidate.image_id in eligible_set
        ]

        if eligible_candidates:
            ranking_request = build_listing_image_ranking_request(
                product_context=product_context,
                candidates=eligible_candidates,
                ownership=ownership,
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
        attempted_ownership_calls = int(
            getattr(exc, "listing_image_ownership_model_calls", 1) or 1
        )
        model_calls += max(0, attempted_ownership_calls - 1)
        report = _selection_report(
            candidates=candidates,
            transport_rejected=transport_rejected,
            identity=identity,
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
    "OWNERSHIP_BATCH_SIZE",
    "ImageOwnershipDecision",
    "ListingImageRankingError",
    "ListingImageRankingResult",
    "RankedImageDecision",
    "build_listing_image_ownership_request",
    "build_listing_image_ranking_request",
    "finalize_supplier_listing_images",
]
