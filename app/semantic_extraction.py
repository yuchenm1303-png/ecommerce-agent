from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from .evidence_contract import EvidencePacket, ProductIdentity
from .evidence_validation import EvidenceValidationError, validate_evidence_packet
from .extraction_request import build_extraction_request_payload
from .qa_catalog import QuestionCatalog
from .semantic_grounding import GroundedSource, GroundingCatalog, IMAGE_KIND, TEXT_KIND


class SemanticGroundingError(EvidenceValidationError):
    pass


class SemanticExtractionProvider(Protocol):
    """Provider-neutral JSON extraction interface.

    Provider-specific API clients are intentionally kept outside the resolver.
    They receive one fully constrained request and must return a JSON-compatible
    mapping. The returned mapping is never trusted until grounded validation
    passes.
    """

    name: str

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass(slots=True)
class SemanticExtractionResult:
    packet: EvidencePacket
    provider_name: str
    request_payload: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


def _business_locked_questions(payload: dict[str, Any]) -> set[str]:
    return {
        str(item.get("question") or "")
        for item in payload.get("questions") or []
        if isinstance(item, dict) and item.get("business_locked")
    }


def build_grounded_semantic_request(
    catalog: QuestionCatalog,
    grounding: GroundingCatalog,
    *,
    identity: ProductIdentity = ProductIdentity(),
) -> dict[str, Any]:
    """Build the exact request contract sent to a vision/web semantic model."""

    base = build_extraction_request_payload(
        catalog,
        identity=identity,
        image_paths=tuple(
            source.image_path
            for source in grounding.sources
            if source.kind == IMAGE_KIND
        ),
    )
    base["task"] = "extract_only_source_grounded_answers_for_current_qa"
    base["grounded_sources"] = grounding.as_request_list()
    base["source_reference_rule"] = (
        "Each fact.source_reference MUST exactly equal one grounded_sources[].source_id."
    )
    base["rules"].extend(
        [
            "Use only grounded_sources supplied in this request; do not use prior knowledge or unstated web knowledge.",
            "For text sources, evidence_text must be a short literal excerpt copied from the cited source content.",
            "For image sources, evidence_text must be a concise description of the exact visible feature/text that supports the answer.",
            "fact.source_type must equal the cited source source_type, unless the answer requires inference; inferred answers must use ai_synthesis.",
            "Do not create a fact when evidence is ambiguous, partially visible, inferred from product category, or merely plausible.",
            "Do not silently normalize conflicting values into one answer; emit separate facts with separate source_reference values.",
            "Do not answer any business_locked question under any circumstance.",
            "Do not cite a source id that is absent from grounded_sources.",
        ]
    )
    base["required_output_shape"]["facts"][0]["source_reference"] = (
        "exact grounded_sources[].source_id"
    )
    base["required_output_shape"]["facts"][0]["evidence_text"] = (
        "literal source excerpt for text, or precise visible evidence description for image"
    )
    base["business_locked_questions"] = sorted(_business_locked_questions(base))
    return base


def _literal_normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def _validate_text_grounding(fact_evidence: str, source: GroundedSource) -> None:
    evidence = _literal_normalize(fact_evidence)
    content = _literal_normalize(source.content)
    if not evidence:
        raise SemanticGroundingError(
            f"文本来源 {source.source_id} 的 fact 缺少 evidence_text。"
        )
    if len(evidence) < 2:
        raise SemanticGroundingError(
            f"文本来源 {source.source_id} 的 evidence_text 过短，无法审计。"
        )
    if evidence not in content:
        raise SemanticGroundingError(
            f"evidence_text 并非 cited text source {source.source_id} 的逐字片段；禁止接受模型改写/幻觉证据。"
        )


def _validate_image_grounding(fact_evidence: str, source: GroundedSource) -> None:
    evidence = fact_evidence.strip()
    if len(evidence) < 4:
        raise SemanticGroundingError(
            f"图片来源 {source.source_id} 的 evidence_text 过短；必须说明可见文字/结构/规格依据。"
        )


def validate_grounded_semantic_packet(
    payload: dict[str, Any] | EvidencePacket,
    catalog: QuestionCatalog,
    grounding: GroundingCatalog,
    *,
    expected_identity: ProductIdentity = ProductIdentity(),
) -> EvidencePacket:
    """Validate model output against both QA scope and exact supplied sources.

    Existing EvidencePacket validation proves question scope, business-field
    isolation and identity compatibility. This extra boundary proves that every
    cited source actually existed in the semantic request and that text evidence
    is literally present in the cited source chunk.
    """

    packet = payload if isinstance(payload, EvidencePacket) else EvidencePacket.from_mapping(payload)
    validated = validate_evidence_packet(
        packet,
        catalog,
        expected_identity=expected_identity,
    ).packet

    for fact in validated.facts:
        source = grounding.by_id(fact.source_reference)
        if source is None:
            raise SemanticGroundingError(
                f"fact {fact.key!r} 引用了未提供的 source_reference={fact.source_reference!r}。"
            )

        if fact.source_type not in {source.source_type, "ai_synthesis"}:
            raise SemanticGroundingError(
                f"fact {fact.key!r} 的 source_type={fact.source_type!r} 与 cited source "
                f"{source.source_id} 的 source_type={source.source_type!r} 不一致。"
            )

        if source.kind == TEXT_KIND:
            _validate_text_grounding(fact.evidence_text, source)
        elif source.kind == IMAGE_KIND:
            _validate_image_grounding(fact.evidence_text, source)
        else:  # GroundingCatalog already rejects this; retain fail-closed guard.
            raise SemanticGroundingError(
                f"不支持的 grounded source kind={source.kind!r}。"
            )

    return validated


def run_grounded_semantic_extraction(
    provider: SemanticExtractionProvider,
    catalog: QuestionCatalog,
    grounding: GroundingCatalog,
    *,
    expected_identity: ProductIdentity = ProductIdentity(),
) -> SemanticExtractionResult:
    """Run one provider and return only a fully grounded EvidencePacket."""

    request_payload = build_grounded_semantic_request(
        catalog,
        grounding,
        identity=expected_identity,
    )
    raw = provider.extract_json(request_payload)
    if not isinstance(raw, dict):
        raise SemanticGroundingError(
            f"semantic provider {provider.name!r} 未返回 JSON object。"
        )
    packet = validate_grounded_semantic_packet(
        raw,
        catalog,
        grounding,
        expected_identity=expected_identity,
    )
    return SemanticExtractionResult(
        packet=packet,
        provider_name=provider.name,
        request_payload=request_payload,
        warnings=list(packet.warnings),
    )
