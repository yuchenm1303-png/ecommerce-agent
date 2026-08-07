from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .evidence_contract import EvidencePacket, ExtractedFact, ProductIdentity
from .evidence_validation import EXTERNAL_EVIDENCE_SOURCE_TYPES, is_business_question
from .qa_catalog import QuestionCatalog, QuestionRecord
from .source_snapshot import SourceSnapshot, SnapshotTableRow
from .source_bundle import normalize_key


@dataclass(slots=True)
class SnapshotExtractionResult:
    packet: EvidencePacket
    matched_rows: int
    ignored_rows: int
    warnings: list[str] = field(default_factory=list)


def _catalog_index(catalog: QuestionCatalog) -> dict[str, QuestionRecord]:
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


def _scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value).strip()
    return ""


def _jsonld_product_nodes(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, list):
        for item in value:
            yield from _jsonld_product_nodes(item)
        return
    if not isinstance(value, dict):
        return

    raw_type = value.get("@type")
    types = raw_type if isinstance(raw_type, list) else [raw_type]
    if any(str(item).casefold() == "product" for item in types if item):
        yield value

    graph = value.get("@graph")
    if graph is not None:
        yield from _jsonld_product_nodes(graph)


def _identity_from_jsonld(snapshot: SourceSnapshot) -> ProductIdentity:
    sku = ""
    model = ""
    brand = ""
    for root in snapshot.json_ld:
        for product in _jsonld_product_nodes(root):
            sku = sku or _scalar(product.get("sku")) or _scalar(product.get("mpn"))
            model = model or _scalar(product.get("model")) or _scalar(product.get("mpn"))
            raw_brand = product.get("brand")
            if isinstance(raw_brand, dict):
                brand = brand or _scalar(raw_brand.get("name"))
            else:
                brand = brand or _scalar(raw_brand)
    return ProductIdentity(sku=sku, model_number=model, brand=brand)


def _jsonld_pairs(snapshot: SourceSnapshot) -> list[tuple[str, str, str]]:
    pairs: list[tuple[str, str, str]] = []
    for root_index, root in enumerate(snapshot.json_ld, start=1):
        for product_index, product in enumerate(_jsonld_product_nodes(root), start=1):
            reference = f"{snapshot.final_url}#jsonld-{root_index}-product-{product_index}"
            additional = product.get("additionalProperty") or []
            if isinstance(additional, dict):
                additional = [additional]
            if isinstance(additional, list):
                for item in additional:
                    if not isinstance(item, dict):
                        continue
                    key = _scalar(item.get("name"))
                    value = _scalar(item.get("value"))
                    if key and value:
                        pairs.append((key, value, reference))

            # Some product JSON-LD exposes requested attributes directly. Keep a
            # small mechanical set; catalog matching later still requires the key
            # to be an exact current QA question.
            for key in ("model", "color", "weight", "material", "size"):
                value = _scalar(product.get(key))
                if value:
                    pairs.append((key, value, reference))
    return pairs


def extract_snapshot_evidence(
    snapshot: SourceSnapshot,
    catalog: QuestionCatalog,
    *,
    source_type: str = "supplier_web",
    confidence: float = 0.88,
) -> SnapshotExtractionResult:
    """Extract deterministic facts from explicit table/JSON-LD key-value pairs.

    Visible prose is intentionally not parsed here. A key must exactly normalize
    to one unique current QA question. Free-text interpretation is reserved for a
    later evidence-grounded model extractor and uses the stricter EvidencePacket
    contract.
    """

    if source_type not in EXTERNAL_EVIDENCE_SOURCE_TYPES or source_type == "ai_synthesis":
        raise ValueError(f"snapshot source_type 不允许：{source_type!r}")
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence 必须在 0..1。")

    index = _catalog_index(catalog)
    facts: list[ExtractedFact] = []
    warnings = list(snapshot.warnings)
    ignored = 0
    seen: set[tuple[str, str, str]] = set()

    pairs: list[tuple[str, str, str]] = [
        (
            row.key,
            row.value,
            f"{snapshot.final_url}#table-{row.table_index}-row-{row.row_index}",
        )
        for row in snapshot.table_rows
    ]
    pairs.extend(_jsonld_pairs(snapshot))

    for raw_key, value, reference in pairs:
        question = index.get(normalize_key(raw_key))
        if question is None:
            ignored += 1
            continue
        if is_business_question(question.question):
            ignored += 1
            warnings.append(
                f"business field ignored from snapshot: {question.question} @ {reference}"
            )
            continue

        fingerprint = (question.normalized_question, normalize_key(value), reference)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        facts.append(
            ExtractedFact(
                key=question.question,
                value=value,
                source_type=source_type,
                source_reference=reference,
                confidence=confidence,
                evidence_text=f"{raw_key}: {value}",
                aliases=() if normalize_key(raw_key) == question.normalized_question else (raw_key,),
                note="deterministic key/value extraction from captured source snapshot",
            )
        )

    packet = EvidencePacket(
        identity=_identity_from_jsonld(snapshot),
        facts=facts,
        extractor="deterministic-source-snapshot",
        warnings=warnings,
    )
    return SnapshotExtractionResult(
        packet=packet,
        matched_rows=len(facts),
        ignored_rows=ignored,
        warnings=warnings,
    )
