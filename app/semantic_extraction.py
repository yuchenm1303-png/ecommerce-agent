from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from .evidence_contract import EvidencePacket, ProductIdentity
from .evidence_validation import EvidenceValidationError, validate_evidence_packet
from .extraction_request import build_extraction_request_payload
from .qa_catalog import QuestionCatalog, QuestionRecord
from .semantic_grounding import GroundedSource, GroundingCatalog, IMAGE_KIND, TEXT_KIND
from .source_bundle import normalize_key
from .value_normalization import canonical_scalar_for_field


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


_RESOLUTION_IN_TEXT = re.compile(r"\d{2,5}\s*[x×*]\s*\d{2,5}", re.IGNORECASE)
_NUMBER_IN_TEXT = re.compile(
    r"(?<![\w.])([+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:\s*[a-zA-Z°%]+(?:\s+[a-zA-Z]+)?)?)(?![\w.])",
    re.IGNORECASE,
)


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
            "Each fact.key must exactly equal one question string from this request. Do not invent aliases or canonical field names.",
            "Return aliases as an empty array. Model-authored aliases are not trusted for question matching.",
            "For text sources, evidence_text must be a short literal excerpt copied from the cited source content.",
            "For image sources, evidence_text must be a concise description of the exact visible feature/text that supports the answer.",
            "For direct source facts, every returned value must be explicitly present in evidence_text; if the answer requires inference or conversion, use ai_synthesis instead.",
            "fact.source_type must equal the cited source source_type, unless the answer requires inference; inferred answers must use ai_synthesis.",
            "Do not create a fact when evidence is ambiguous, partially visible, inferred from product category, or merely plausible.",
            "Do not silently normalize conflicting values into one answer; emit separate facts with separate source_reference values.",
            "Do not answer any business_locked question under any circumstance.",
            "Do not cite a source id that is absent from grounded_sources.",
        ]
    )
    base["required_output_shape"]["facts"][0]["key"] = (
        "exact current QA question string"
    )
    base["required_output_shape"]["facts"][0]["aliases"] = []
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


def _question_lookup(catalog: QuestionCatalog) -> dict[str, QuestionRecord]:
    output: dict[str, QuestionRecord] = {}
    duplicates: set[str] = set()
    for question in catalog.questions:
        key = question.normalized_question
        if key in output:
            duplicates.add(key)
        else:
            output[key] = question
    for key in duplicates:
        output.pop(key, None)
    return output


def _validate_model_question_keys(packet: EvidencePacket, catalog: QuestionCatalog) -> None:
    """Do not let an untrusted model self-authorize a QA match via aliases."""

    counts: dict[str, int] = {}
    for question in catalog.questions:
        counts[question.normalized_question] = counts.get(question.normalized_question, 0) + 1

    for fact in packet.facts:
        normalized = normalize_key(fact.key)
        if counts.get(normalized, 0) != 1:
            raise SemanticGroundingError(
                f"semantic fact key={fact.key!r} 不是当前 QA 中唯一的精确问题名；禁止模型通过自造别名映射。"
            )
        if fact.aliases:
            raise SemanticGroundingError(
                f"semantic fact {fact.key!r} 返回了 aliases={list(fact.aliases)!r}；"
                "模型输出不得自行定义 QA alias。"
            )


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


def _candidate_canonical_values(question: QuestionRecord, evidence_text: str) -> set[str]:
    field = question.as_semantic_field()
    candidates: set[str] = set()
    for match in _RESOLUTION_IN_TEXT.finditer(evidence_text):
        candidates.add(canonical_scalar_for_field(field, match.group(0)))
    for match in _NUMBER_IN_TEXT.finditer(evidence_text):
        candidates.add(canonical_scalar_for_field(field, match.group(1)))
    return candidates


def _direct_value_supported(
    question: QuestionRecord,
    value: str,
    evidence_text: str,
) -> bool:
    raw_value = str(value).strip()
    if not raw_value:
        return False

    # Exact normalized textual containment covers ordinary dropdown/text values
    # and most direct numeric/unit strings while ignoring punctuation/case.
    normalized_value = normalize_key(raw_value)
    normalized_evidence = normalize_key(evidence_text)
    if len(normalized_value) >= 2 and normalized_value in normalized_evidence:
        return True

    # Mechanical equivalence handles safe representation-only differences such
    # as 3 inch vs 3.0 inches or 1920x1080 vs 1920 × 1080. It intentionally does
    # not turn marketing labels like 1080P into pixel dimensions.
    target = canonical_scalar_for_field(question.as_semantic_field(), raw_value)
    if target.startswith(("num:", "px:")):
        return target in _candidate_canonical_values(question, evidence_text)
    return False


def _validate_direct_claim_against_evidence(
    *,
    question: QuestionRecord,
    value: str | tuple[str, ...],
    source_type: str,
    evidence_text: str,
) -> None:
    if source_type == "ai_synthesis":
        return
    values = value if isinstance(value, tuple) else (value,)
    unsupported = [
        item
        for item in values
        if not _direct_value_supported(question, item, evidence_text)
    ]
    if unsupported:
        raise SemanticGroundingError(
            f"fact {question.question!r} 的直接答案 {unsupported!r} 未机械出现在 evidence_text 中；"
            "若答案需要推理/换算，必须标记为 ai_synthesis 并进入人工复核。"
        )


def validate_grounded_semantic_packet(
    payload: dict[str, Any] | EvidencePacket,
    catalog: QuestionCatalog,
    grounding: GroundingCatalog,
    *,
    expected_identity: ProductIdentity = ProductIdentity(),
) -> EvidencePacket:
    """Validate model output against QA scope and the exact supplied sources."""

    packet = payload if isinstance(payload, EvidencePacket) else EvidencePacket.from_mapping(payload)
    _validate_model_question_keys(packet, catalog)
    validated = validate_evidence_packet(
        packet,
        catalog,
        expected_identity=expected_identity,
    ).packet
    questions = _question_lookup(catalog)

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
        else:
            raise SemanticGroundingError(
                f"不支持的 grounded source kind={source.kind!r}。"
            )

        question = questions.get(normalize_key(fact.key))
        if question is None:  # should already be impossible after validation.
            raise SemanticGroundingError(f"无法恢复 QA 问题：{fact.key!r}")
        _validate_direct_claim_against_evidence(
            question=question,
            value=fact.value,
            source_type=fact.source_type,
            evidence_text=fact.evidence_text,
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
