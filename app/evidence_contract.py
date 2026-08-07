from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .evidence_pipeline import add_fact
from .source_bundle import ProductSourceBundle, normalize_key


class EvidenceContractError(ValueError):
    pass


class IdentityMismatchError(EvidenceContractError):
    pass


@dataclass(slots=True, frozen=True)
class ProductIdentity:
    sku: str = ""
    model_number: str = ""
    brand: str = ""

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> "ProductIdentity":
        data = payload or {}
        return cls(
            sku=str(data.get("sku") or "").strip(),
            model_number=str(data.get("model_number") or data.get("model") or "").strip(),
            brand=str(data.get("brand") or "").strip(),
        )


@dataclass(slots=True, frozen=True)
class ExtractedFact:
    key: str
    value: str | tuple[str, ...]
    source_type: str
    source_reference: str
    confidence: float
    evidence_text: str
    aliases: tuple[str, ...] = ()
    note: str = ""

    @classmethod
    def from_mapping(cls, payload: dict[str, Any], *, index: int) -> "ExtractedFact":
        key = str(payload.get("key") or payload.get("question") or "").strip()
        raw_value = payload.get("value", payload.get("answer"))
        source_type = str(payload.get("source_type") or "").strip()
        source_reference = str(payload.get("source_reference") or "").strip()
        evidence_text = str(payload.get("evidence_text") or payload.get("evidence") or "").strip()
        if not key:
            raise EvidenceContractError(f"facts[{index}] 缺少 key/question。")
        if raw_value in (None, ""):
            raise EvidenceContractError(f"facts[{index}] 缺少 value/answer。")
        if not source_type:
            raise EvidenceContractError(f"facts[{index}] 缺少 source_type。")
        if not source_reference:
            raise EvidenceContractError(f"facts[{index}] 缺少 source_reference。")
        if not evidence_text:
            raise EvidenceContractError(f"facts[{index}] 缺少 evidence_text；AI/识图事实必须可追溯。")

        confidence = float(payload.get("confidence", -1))
        if not 0.0 <= confidence <= 1.0:
            raise EvidenceContractError(f"facts[{index}].confidence 必须在 0..1。")

        if isinstance(raw_value, list):
            value: str | tuple[str, ...] = tuple(
                str(item).strip() for item in raw_value if str(item).strip()
            )
            if not value:
                raise EvidenceContractError(f"facts[{index}].value 数组为空。")
        else:
            value = str(raw_value).strip()
            if not value:
                raise EvidenceContractError(f"facts[{index}].value 为空。")

        raw_aliases = payload.get("aliases") or []
        if not isinstance(raw_aliases, list):
            raise EvidenceContractError(f"facts[{index}].aliases 必须是数组。")
        aliases = tuple(str(item).strip() for item in raw_aliases if str(item).strip())
        return cls(
            key=key,
            value=value,
            source_type=source_type,
            source_reference=source_reference,
            confidence=confidence,
            evidence_text=evidence_text,
            aliases=aliases,
            note=str(payload.get("note") or "").strip(),
        )


@dataclass(slots=True)
class EvidencePacket:
    identity: ProductIdentity
    facts: list[ExtractedFact] = field(default_factory=list)
    extractor: str = ""
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "EvidencePacket":
        if not isinstance(payload, dict):
            raise EvidenceContractError("evidence packet 必须是 JSON object。")
        raw_facts = payload.get("facts")
        if not isinstance(raw_facts, list):
            raise EvidenceContractError("evidence packet 缺少 facts 数组。")
        facts = [
            ExtractedFact.from_mapping(item, index=index)
            for index, item in enumerate(raw_facts, start=1)
            if isinstance(item, dict)
        ]
        if len(facts) != len(raw_facts):
            raise EvidenceContractError("facts 数组只能包含 object。")
        warnings = payload.get("warnings") or []
        if not isinstance(warnings, list):
            raise EvidenceContractError("warnings 必须是数组。")
        return cls(
            identity=ProductIdentity.from_mapping(payload.get("product_identity")),
            facts=facts,
            extractor=str(payload.get("extractor") or "").strip(),
            warnings=[str(item) for item in warnings],
        )


def assert_identity_compatible(expected: ProductIdentity, observed: ProductIdentity) -> None:
    """Fail closed when the extractor clearly describes another product."""

    for field_name in ("sku", "model_number", "brand"):
        wanted = getattr(expected, field_name)
        actual = getattr(observed, field_name)
        if not wanted or not actual:
            continue
        if normalize_key(wanted) != normalize_key(actual):
            raise IdentityMismatchError(
                f"商品身份不一致：{field_name} expected={wanted!r}, observed={actual!r}"
            )


def bundle_from_evidence_packet(
    packet: EvidencePacket,
    *,
    expected_identity: ProductIdentity | None = None,
    confidence_ceiling: float | None = None,
    quarantine_note: str = "",
) -> ProductSourceBundle:
    """Convert a validated packet into resolver evidence.

    ``confidence_ceiling`` is a second trust boundary used for generic external
    packet files. A packet may have a correct JSON shape and QA/identity scope
    without proving that its cited image/web evidence belongs to the current
    source universe. Callers can therefore quarantine it below the system
    autofill floor while still keeping its candidate values visible for review.

    Grounded semantic pipelines and deterministic snapshot extractors omit this
    ceiling only after they have independently validated their source binding.
    """

    if expected_identity is not None:
        assert_identity_compatible(expected_identity, packet.identity)
    if confidence_ceiling is not None and not 0.0 <= confidence_ceiling <= 1.0:
        raise ValueError("confidence_ceiling 必须在 0..1。")

    bundle = ProductSourceBundle(sku=packet.identity.sku)
    for fact in packet.facts:
        confidence = fact.confidence
        note_parts = [fact.note]
        if confidence_ceiling is not None and confidence > confidence_ceiling:
            confidence = confidence_ceiling
            note_parts.append(
                "external packet quarantined below autofill trust floor: "
                f"original={fact.confidence:.4f}, effective={confidence:.4f}"
            )
        if quarantine_note:
            note_parts.append(quarantine_note)
        add_fact(
            bundle,
            key=fact.key,
            value=fact.value,
            source_type=fact.source_type,
            source_reference=fact.source_reference,
            confidence=confidence,
            aliases=fact.aliases,
            evidence_text=fact.evidence_text,
            note=" | ".join(part for part in note_parts if part),
        )
    return bundle
