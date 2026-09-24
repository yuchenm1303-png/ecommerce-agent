from __future__ import annotations

from dataclasses import dataclass, replace

from .qa_catalog import QuestionCatalog
from .resolver_inputs import (
    ResolutionInputResult,
    ResolutionInputSpec,
    build_resolution_inputs,
    customer_context_for_resolution,
)


_TRUSTED_CONTEXT_SOURCE_TYPES = {
    "structured",
    "customer_answer",
    "business",
    "config",
    "rule",
}


@dataclass(slots=True)
class AIProductContext:
    text: str
    trusted_inputs: ResolutionInputResult


def _format_value(value: object) -> str:
    if isinstance(value, tuple):
        return " | ".join(str(item) for item in value)
    return str(value)


def build_ai_product_context(
    customer_catalog: QuestionCatalog,
    spec: ResolutionInputSpec,
) -> AIProductContext:
    """Build the canonical customer/structured source seen by the AI resolver.

    Raw workbook context appears exactly once. Explicit Answer cells, SKU and
    other structured seller/customer facts are listed separately because they
    carry stronger provenance. No product meaning is inferred here.
    """

    trusted_spec = replace(
        spec,
        supplier_snapshots=(),
        official_snapshots=(),
        image_paths=(),
    )
    trusted = build_resolution_inputs(customer_catalog, trusted_spec)
    parts: list[str] = []
    canonical_context = customer_context_for_resolution(customer_catalog, trusted_spec)
    if canonical_context:
        parts.append("Customer/product context:\n" + canonical_context)

    evidence_lines: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for item in trusted.bundle.evidence:
        if item.source_type not in _TRUSTED_CONTEXT_SOURCE_TYPES:
            continue
        value = _format_value(item.value).strip()
        if not value:
            continue
        fingerprint = (item.key.strip(), value, item.source_type)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        evidence_lines.append(
            f"- {item.key}: {value} [source_type={item.source_type}; source={item.source_reference}]"
        )
    if evidence_lines:
        parts.append("Explicit customer/structured facts:\n" + "\n".join(evidence_lines))

    return AIProductContext(
        text="\n\n".join(parts).strip(),
        trusted_inputs=trusted,
    )
