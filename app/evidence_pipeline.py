from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .qa_catalog import QuestionCatalog
from .source_bundle import ProductSourceBundle


@dataclass(slots=True, frozen=True)
class SourcePolicy:
    priority: int
    default_confidence: float
    description: str


# Lower priority number wins when agreeing evidence is otherwise equivalent.
# Conflicting accepted evidence is never silently overridden; the resolver sends
# it to review instead.
SOURCE_POLICIES: dict[str, SourcePolicy] = {
    "business": SourcePolicy(5, 1.00, "explicit seller operating data"),
    "config": SourcePolicy(6, 1.00, "explicit seller configuration"),
    "rule": SourcePolicy(7, 0.99, "explicit deterministic seller rule"),
    "structured": SourcePolicy(10, 0.99, "structured product table"),
    "customer_answer": SourcePolicy(12, 0.99, "customer-confirmed QA answer"),
    "manufacturer_doc": SourcePolicy(20, 0.98, "manufacturer specification/document"),
    "supplier_doc": SourcePolicy(25, 0.95, "supplier product specification/document"),
    "product_image": SourcePolicy(30, 0.92, "fact read from product image/packaging"),
    "official_doc": SourcePolicy(35, 0.95, "official product documentation"),
    "knowledge_base": SourcePolicy(40, 0.95, "previously confirmed knowledge base fact"),
    "official_web": SourcePolicy(50, 0.90, "official web page"),
    "supplier_web": SourcePolicy(55, 0.86, "supplier marketplace/product page"),
    "customer_file": SourcePolicy(60, 0.90, "unstructured customer file evidence"),
    "ai_synthesis": SourcePolicy(90, 0.70, "AI synthesis from cited source material"),
}


def source_policy(source_type: str) -> SourcePolicy:
    return SOURCE_POLICIES.get(
        source_type,
        SourcePolicy(80, 0.75, "unknown/uncategorized source"),
    )


def add_fact(
    bundle: ProductSourceBundle,
    *,
    key: str,
    value: str | Iterable[str],
    source_type: str,
    source_reference: str,
    confidence: float | None = None,
    aliases: Iterable[str] = (),
    note: str = "",
) -> None:
    policy = source_policy(source_type)
    actual_confidence = policy.default_confidence if confidence is None else float(confidence)
    if not 0.0 <= actual_confidence <= 1.0:
        raise ValueError(f"confidence 必须在 0..1，当前 {actual_confidence}")

    keys = [key, *aliases]
    seen: set[str] = set()
    for candidate in keys:
        clean = str(candidate).strip()
        if not clean or clean.casefold() in seen:
            continue
        seen.add(clean.casefold())
        bundle.add_evidence(
            key=clean,
            value=value,
            source_type=source_type,
            source_reference=source_reference,
            priority=policy.priority,
            confidence=actual_confidence,
            note=note,
        )


def bundle_from_catalog_answers(
    catalog: QuestionCatalog,
    *,
    sku: str = "",
    image_paths: Iterable[str] = (),
    product_url: str | None = None,
    supplemental_text: str = "",
) -> ProductSourceBundle:
    """Build evidence only from explicit answers while retaining blank questions elsewhere."""

    bundle = ProductSourceBundle(
        sku=sku,
        image_paths=tuple(str(item) for item in image_paths),
        product_url=product_url,
        supplemental_text=supplemental_text,
    )
    for item in catalog.questions:
        if not item.has_answer:
            continue
        add_fact(
            bundle,
            key=item.question,
            value=item.answer,
            source_type="customer_answer",
            source_reference=item.source_reference,
            confidence=0.99,
            note="explicit Answer cell in customer QA workbook",
        )
    return bundle


def merge_bundles(*bundles: ProductSourceBundle) -> ProductSourceBundle:
    merged = ProductSourceBundle()
    for bundle in bundles:
        if bundle.sku and not merged.sku:
            merged.sku = bundle.sku
        merged.evidence.extend(bundle.evidence)
        if bundle.image_paths:
            merged.image_paths = tuple(dict.fromkeys((*merged.image_paths, *bundle.image_paths)))
        if bundle.product_url and not merged.product_url:
            merged.product_url = bundle.product_url
        if bundle.supplemental_text:
            merged.supplemental_text = "\n".join(
                item for item in (merged.supplemental_text, bundle.supplemental_text) if item
            )
    return merged


def bundle_from_facts_json(path: str | Path, *, sku: str = "") -> ProductSourceBundle:
    """Load normalized facts produced by future image/web/AI extractors.

    Accepted shape:
      {"facts": [{"key": ..., "value": ..., "source_type": ...,
                  "source_reference": ..., "confidence": 0.95,
                  "aliases": [...] }]}

    A plain object mapping keys to scalar values is also accepted as a convenient
    structured/manual source.
    """

    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    bundle = ProductSourceBundle(sku=sku)

    if isinstance(payload, dict) and "facts" not in payload:
        for key, value in payload.items():
            if value in (None, ""):
                continue
            add_fact(
                bundle,
                key=str(key),
                value=str(value),
                source_type="structured",
                source_reference=f"{source.name}:key={key}",
            )
        return bundle

    facts = payload.get("facts") if isinstance(payload, dict) else None
    if not isinstance(facts, list):
        raise ValueError("facts JSON 必须是普通 key/value object 或包含 facts 数组。")

    for index, item in enumerate(facts, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"facts[{index}] 必须是 object。")
        key = str(item.get("key") or "").strip()
        value = item.get("value")
        if not key or value in (None, ""):
            continue
        if isinstance(value, list):
            stored_value: str | Iterable[str] = [str(part) for part in value]
        else:
            stored_value = str(value)
        source_type = str(item.get("source_type") or "structured").strip()
        reference = str(item.get("source_reference") or f"{source.name}:facts[{index}]").strip()
        confidence = item.get("confidence")
        aliases = item.get("aliases") or []
        if not isinstance(aliases, list):
            raise ValueError(f"facts[{index}].aliases 必须是数组。")
        add_fact(
            bundle,
            key=key,
            value=stored_value,
            source_type=source_type,
            source_reference=reference,
            confidence=float(confidence) if confidence is not None else None,
            aliases=[str(alias) for alias in aliases],
            note=str(item.get("note") or ""),
        )
    return bundle


_KEY_VALUE_LINE = re.compile(r"^\s*([^:=：]{2,80})\s*[:=：]\s*(.+?)\s*$")


def bundle_from_key_value_text(
    text: str,
    *,
    source_reference: str = "supplemental_text",
    source_type: str = "customer_file",
) -> ProductSourceBundle:
    """Conservatively parse explicit `key: value` lines only.

    Free prose is deliberately ignored. This prevents accidental extraction of
    guessed facts before the AI evidence extractor is implemented.
    """

    bundle = ProductSourceBundle(supplemental_text=text)
    for line_number, line in enumerate(text.splitlines(), start=1):
        match = _KEY_VALUE_LINE.match(line)
        if not match:
            continue
        key, value = match.group(1).strip(), match.group(2).strip()
        if not key or not value:
            continue
        add_fact(
            bundle,
            key=key,
            value=value,
            source_type=source_type,
            source_reference=f"{source_reference}:line={line_number}",
        )
    return bundle
