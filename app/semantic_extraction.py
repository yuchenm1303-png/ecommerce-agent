from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from .evidence_contract import (
    EvidenceContractError,
    EvidencePacket,
    ExtractedFact,
    IdentityMismatchError,
    ProductIdentity,
    assert_identity_compatible,
)
from .evidence_pipeline import source_policy
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


@dataclass(slots=True)
class SemanticPacketValidationResult:
    """Validated source packet plus facts rejected individually.

    Rejected facts never enter evidence. Keeping valid sibling facts avoids
    repeating an expensive image/source request just because one model-authored
    fact violated the same strict grounding rules.
    """

    packet: EvidencePacket
    rejected_facts: list[str] = field(default_factory=list)

    @property
    def rejected_fact_count(self) -> int:
        return len(self.rejected_facts)


GROUNDED_OUTPUT_RULES = (
    "GROUNDED OUTPUT RULES - follow these before answering:\n"
    "1. fact.key must exactly equal one question string from \"questions\". Never invent keys.\n"
    "2. Every fact MUST include source_reference, and it must exactly equal one grounded_sources[].source_id.\n"
    "3. Text sources: evidence_text must be ONE single contiguous run of characters copied "
    "character-for-character from the cited source content, trimmed only at the edges. Never "
    "paraphrase, translate, reorder, merge multiple fragments, or insert colons/ellipsis/labels.\n"
    "4. Image sources: evidence_text must describe only the exact visible text/structure in that image.\n"
    "5. Direct facts: value must be written exactly as in the source and must literally appear inside "
    "evidence_text (e.g. source \"黑色\" => value \"黑色\", never \"Black\").\n"
    "6. Direct facts also require DIRECT ATTRIBUTE BINDING: the cited evidence must explicitly name the "
    "same QA attribute/question. If you map a differently named source label to the QA field, translate "
    "the attribute name, select a subset from a generic feature list, or assign a generic specification "
    "to a more specific field, fact.source_type MUST be \"ai_synthesis\". Examples: \"拍摄角度 120°\" "
    "cannot be a direct Exterior Field of View or Interior Field of View fact; \"品牌 other\" cannot be "
    "a direct Vehicle Brand fact; \"规格尺寸 ...\" cannot be a direct Vehicle Model Name fact.\n"
    "7. If the answer is NOT literally written in the source and requires inference, counting, unit "
    "conversion, language translation, or semantic attribute mapping (e.g. \"双镜头\" => \"2\", "
    "\"黑色\" => \"Black\", \"数据传输接口 USB\" => QA field \"USB Type Supported\"), set "
    "fact.source_type=\"ai_synthesis\" instead of the source's own type, keep source_reference on the "
    "real source, and still provide a literal excerpt as evidence_text. Inferred, translated, or "
    "semantically remapped values are never direct facts.\n"
    "8. Never answer a business_locked question. Never invent a fact. If evidence is absent or "
    "ambiguous, omit the fact entirely. Absence of a mention is not evidence for a value such as No.\n"
    "Worked example: a source row reads \"颜色分类  黑色\". If the required answer for \"Colour\" must "
    "be \"Black\", emit value=[\"Black\"], source_type=\"ai_synthesis\", the real source_reference, and "
    "evidence_text=\"颜色分类  黑色\". Never emit value=[\"Black\"] with source_type=\"supplier_web\"."
)


def validation_error_instruction(payload: dict[str, Any]) -> str:
    """Build a corrective re-prompt fragment after one rejected model output."""
    error = str(payload.get("validation_error") or "").strip()
    if not error:
        return ""
    return (
        "\n\nCORRECTION REQUIRED - your previous output was rejected by the validator:\n"
        + error
        + "\nReturn a complete corrected JSON object that satisfies every rule. "
        "Remove any fact you cannot fully ground instead of leaving empty fields."
    )


_RESOLUTION_IN_TEXT = re.compile(r"\d{2,5}\s*[x×*]\s*\d{2,5}", re.IGNORECASE)
_NUMBER_IN_TEXT = re.compile(
    r"(?<![\w.])([+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:\s*[a-zA-Z°%]+(?:\s+[a-zA-Z]+)?)?)(?![\w.])",
    re.IGNORECASE,
)
_INFERENCE_NOTE_RE = re.compile(
    r"\b(?:assum(?:e|ed|ing)|infer(?:red|ence|ring)?|impli(?:ed|es|cation)|"
    r"map(?:ped|ping)?|appl(?:ied|ying)|default(?:ed|ing)?|selected?|"
    r"equivalent|remaining|corresponds?|not\s+explicit(?:ly)?|no\s+explicit|"
    r"source\s+does\s+not|no\s+distinction)\b|推断|假设|映射|未明确|未区分",
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
            "A direct source fact also requires explicit attribute binding: evidence must name the same QA attribute. Mapping a differently named/translated/generic source attribute to the QA field is ai_synthesis.",
            "fact.source_type must equal the cited source source_type, unless the answer requires inference; inferred answers must use ai_synthesis.",
            "Do not create a fact when evidence is ambiguous, partially visible, inferred from product category, merely plausible, or based only on absence of a mention.",
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


def _is_single_cjk_ideograph(text: str) -> bool:
    return len(text) == 1 and "\u4e00" <= text <= "\u9fff"


def _direct_value_supported(
    question: QuestionRecord,
    value: str,
    evidence_text: str,
) -> bool:
    raw_value = str(value).strip()
    if not raw_value:
        return False

    normalized_value = normalize_key(raw_value)
    normalized_evidence = normalize_key(evidence_text)
    matchable = normalized_value and (
        len(normalized_value) >= 2 or _is_single_cjk_ideograph(normalized_value)
    )
    if matchable and normalized_value in normalized_evidence:
        return True

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


def _direct_attribute_binding_explicit(question: QuestionRecord, evidence_text: str) -> bool:
    question_key = normalize_key(question.question)
    evidence_key = normalize_key(evidence_text)
    if not question_key or not evidence_key:
        return False
    return question_key in evidence_key


def _quarantine_semantically_unbound_direct_fact(
    fact: ExtractedFact,
    question: QuestionRecord,
) -> tuple[ExtractedFact, str | None]:
    if fact.source_type == "ai_synthesis":
        return fact, None

    binding_explicit = _direct_attribute_binding_explicit(question, fact.evidence_text)
    note_admits_inference = bool(_INFERENCE_NOTE_RE.search(fact.note or ""))
    if binding_explicit and not note_admits_inference:
        return fact, None

    reasons: list[str] = []
    if not binding_explicit:
        reasons.append("evidence does not explicitly name the same QA attribute")
    if note_admits_inference:
        reasons.append("extractor note admits inference/assumption/mapping")
    reason = "; ".join(reasons)
    ai_ceiling = source_policy("ai_synthesis").max_confidence
    quarantine_note = (
        "semantic binding quarantine: direct source value was retained for review but cannot "
        f"authorize autofill because {reason}"
    )
    effective_note = " | ".join(item for item in (fact.note, quarantine_note) if item)
    return (
        ExtractedFact(
            key=fact.key,
            value=fact.value,
            source_type="ai_synthesis",
            source_reference=fact.source_reference,
            confidence=min(fact.confidence, ai_ceiling),
            evidence_text=fact.evidence_text,
            aliases=fact.aliases,
            note=effective_note,
        ),
        f"semantic binding quarantined: {fact.key} @ {fact.source_reference} ({reason})",
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
    effective_facts: list[ExtractedFact] = []
    warnings = list(validated.warnings)

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
        if question is None:
            raise SemanticGroundingError(f"无法恢复 QA 问题：{fact.key!r}")
        _validate_direct_claim_against_evidence(
            question=question,
            value=fact.value,
            source_type=fact.source_type,
            evidence_text=fact.evidence_text,
        )
        effective, warning = _quarantine_semantically_unbound_direct_fact(fact, question)
        effective_facts.append(effective)
        if warning:
            warnings.append(warning)

    return EvidencePacket(
        identity=validated.identity,
        facts=effective_facts,
        extractor=validated.extractor,
        warnings=warnings,
    )


def _raw_fact_has_value(payload: dict[str, Any]) -> bool:
    raw = payload.get("value", payload.get("answer"))
    if raw in (None, ""):
        return False
    if isinstance(raw, list):
        return any(str(item).strip() for item in raw)
    return bool(str(raw).strip())


def validate_grounded_semantic_packet_partial(
    payload: dict[str, Any] | EvidencePacket,
    catalog: QuestionCatalog,
    grounding: GroundingCatalog,
    *,
    expected_identity: ProductIdentity = ProductIdentity(),
) -> SemanticPacketValidationResult:
    """Fail closed per fact while salvaging independently valid facts.

    Product identity and top-level packet structure remain hard gates. Individual
    bad facts are never repaired into evidence locally; they are dropped with an
    audit warning. This keeps the security boundary while avoiding a full
    re-request of an expensive image because one sibling fact was malformed.
    """

    if isinstance(payload, EvidencePacket):
        packet = validate_grounded_semantic_packet(
            payload,
            catalog,
            grounding,
            expected_identity=expected_identity,
        )
        return SemanticPacketValidationResult(packet=packet)

    if not isinstance(payload, dict):
        raise EvidenceContractError("evidence packet 必须是 JSON object。")
    raw_facts = payload.get("facts")
    if not isinstance(raw_facts, list):
        raise EvidenceContractError("evidence packet 缺少 facts 数组。")
    raw_warnings = payload.get("warnings") or []
    if not isinstance(raw_warnings, list):
        raise EvidenceContractError("warnings 必须是数组。")

    identity = ProductIdentity.from_mapping(payload.get("product_identity"))
    assert_identity_compatible(expected_identity, identity)
    extractor = str(payload.get("extractor") or "").strip()
    warnings = [str(item) for item in raw_warnings]
    accepted: list[ExtractedFact] = []
    rejected: list[str] = []

    for index, item in enumerate(raw_facts, start=1):
        if not isinstance(item, dict):
            rejected.append(f"facts[{index}]: fact must be an object")
            continue
        key = str(item.get("key") or item.get("question") or f"facts[{index}]").strip()
        if not _raw_fact_has_value(item):
            warnings.append(f"empty-value fact ignored: {key}")
            continue
        try:
            fact = ExtractedFact.from_mapping(item, index=index)
            single = EvidencePacket(
                identity=identity,
                facts=[fact],
                extractor=extractor,
                warnings=[],
            )
            validated = validate_grounded_semantic_packet(
                single,
                catalog,
                grounding,
                expected_identity=expected_identity,
            )
            accepted.extend(validated.facts)
            warnings.extend(validated.warnings)
        except IdentityMismatchError:
            raise
        except EvidenceContractError as exc:
            rejected.append(f"{key}: {exc}")

    for item in rejected:
        warnings.append(f"rejected semantic fact ignored: {item}")

    combined = EvidencePacket(
        identity=identity,
        facts=accepted,
        extractor=extractor,
        warnings=warnings,
    )
    # One final canonicalization pass deduplicates facts that survived
    # independently without weakening any source-level validation above.
    normalized = validate_evidence_packet(
        combined,
        catalog,
        expected_identity=expected_identity,
    ).packet
    return SemanticPacketValidationResult(
        packet=normalized,
        rejected_facts=rejected,
    )


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
