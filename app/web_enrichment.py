from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Protocol

from .ai_decisions import (
    CONFLICT,
    MISSING,
    READY,
    REVIEW,
    AIDecisionPacket,
    DecisionCitation,
    FieldDecision,
    field_contract,
    field_id,
    schema_digest,
    validate_ai_decision_packet,
)
from .compact_evidence import CompactEvidence
from .business_fields import is_business_question
from .providers.dashscope_web_search import WebSearchJSONResult, WebSearchSource
from .semantic_grounding import GroundingCatalog, TEXT_KIND
from .source_bundle import normalize_key


WEB_SEARCH_CONTRACT_VERSION = 16
WEB_SEARCH_CACHE_VERSION = 16
WEB_FILLABLE_STATUSES = {MISSING, REVIEW}
STRONG_IDENTITY_BASES = {
    "canonical_offer_or_url",
    "exact_variant_identifier",
    "manufacturer_identifier",
    "global_trade_identifier",
    "explicit_cross_reference",
}
IDENTITY_BASES = {
    *STRONG_IDENTITY_BASES,
    "generic_model_or_similarity",
    "none",
}
SOURCE_MATCHES = {
    "same_product",
    "same_product_family",
    "similar_product",
    "uncertain",
    "different_product",
}
EVIDENCE_USABLE_MATCHES = SOURCE_MATCHES - {"different_product"}


class SourcedWebSearchProvider(Protocol):
    name: str
    model: str

    def search_json(self, prompt: str) -> WebSearchJSONResult:
        ...


@dataclass(slots=True, frozen=True)
class PersistedWebSource:
    source_reference: str
    url: str
    title: str = ""
    site_name: str = ""
    content: str = ""
    request_id: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "source_reference": self.source_reference,
            "url": self.url,
            "title": self.title,
            "site_name": self.site_name,
            "content": self.content,
            "request_id": self.request_id,
        }


@dataclass(slots=True, frozen=True)
class WebEvidence:
    field_id: str
    source_reference: str
    source_url: str
    evidence_text: str

    def as_dict(self) -> dict[str, str]:
        return {
            "field_id": self.field_id,
            "source_reference": self.source_reference,
            "source_url": self.source_url,
            "evidence_text": self.evidence_text,
        }


@dataclass(slots=True, frozen=True)
class WebSourceMatch:
    source_url: str
    match: str
    reason: str = ""
    identity_basis: str = "none"
    identity_evidence: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_url": self.source_url,
            "match": self.match,
            "reason": self.reason,
            "identity_basis": self.identity_basis,
            "identity_evidence": list(self.identity_evidence),
        }


@dataclass(slots=True)
class WebEnrichmentResult:
    packet: AIDecisionPacket
    web_sources: list[PersistedWebSource] = field(default_factory=list)
    evidence: list[WebEvidence] = field(default_factory=list)
    source_matches: list[WebSourceMatch] = field(default_factory=list)
    target_field_count: int = 0
    search_batch_count: int = 0
    search_model_calls: int = 0
    search_cache_hits: int = 0
    search_failed_batches: int = 0
    searched: bool = False
    search_elapsed_seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def model_calls(self) -> int:
        return self.search_model_calls

    @property
    def cache_hit(self) -> bool:
        return self.searched and self.search_model_calls == 0

    @property
    def elapsed_seconds(self) -> float:
        return self.search_elapsed_seconds

    @property
    def warning(self) -> str:
        return " | ".join(self.warnings)


def _field_is_business(field: dict[str, Any]) -> bool:
    return is_business_question(str(field.get("attribute_key") or "")) or is_business_question(
        str(field.get("label") or "")
    )


def _targets(
    packet: AIDecisionPacket,
    fields: Iterable[dict[str, Any]],
) -> list[tuple[dict[str, Any], FieldDecision]]:
    by_id = {field_id(field): field for field in fields}
    output: list[tuple[dict[str, Any], FieldDecision]] = []
    for decision in packet.decisions:
        field = by_id.get(decision.field_id)
        if field is None or _field_is_business(field):
            continue
        if decision.status in WEB_FILLABLE_STATUSES:
            output.append((field, decision))
    return output


def _prior_payload(decision: FieldDecision) -> dict[str, Any]:
    return {
        "status": decision.status,
        "values": list(decision.values),
        "qualifier": decision.qualifier,
        "search_queries": list(decision.search_queries),
    }


def _web_field_contract(field: dict[str, Any]) -> dict[str, Any]:
    """Keep only field metadata that changes Web research or answer shape."""

    contract = field_contract(field)
    section = contract["section_heading"].casefold()
    payload: dict[str, Any] = {
        "key": contract["attribute_key"],
        "label": contract["label"],
        "section": (
            "S"
            if "price, stock and shipping" in section
            else "P"
            if "product description" in section
            else "A"
        ),
    }
    if contract["multi_value"]:
        payload["multi_value"] = True
    if contract["options"]:
        payload["options"] = contract["options"]
    if contract["qualifier_options"]:
        payload["qualifier_options"] = contract["qualifier_options"]
    return payload


def _web_target_payload(field: dict[str, Any], decision: FieldDecision) -> dict[str, Any]:
    payload = {"field_id": decision.field_id, **_web_field_contract(field)}
    if decision.status == REVIEW:
        payload["prior_review"] = _prior_payload(decision)
    return payload


def _local_anchor_payload(initial: AIDecisionPacket, fields: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {field_id(field): field for field in fields}
    output: list[dict[str, Any]] = []
    for decision in initial.decisions:
        if decision.status not in {READY, CONFLICT}:
            continue
        field = by_id.get(decision.field_id)
        if field is None or _field_is_business(field):
            continue
        item: dict[str, Any] = {
            "attribute_key": field_contract(field)["attribute_key"],
            "label": field_contract(field)["label"],
            "status": decision.status,
        }
        if decision.status == READY:
            item["values"] = list(decision.values)
            item["qualifier"] = decision.qualifier
        else:
            item["alternatives"] = [alternative.as_dict() for alternative in decision.alternatives]
        output.append(item)
    return output


def _fingerprint_lines(text: str, *, budget: int) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    used = 0
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and "] " in line:
            line = line.split("] ", 1)[1]
        if not line:
            continue
        # Long rendered-text lines often begin with the supplier/manufacturer
        # identity and then continue into page chrome. Keep that useful prefix
        # instead of dropping the entire line from the compact fingerprint.
        line = line[:280]
        key = normalize_key(line)
        if not key or key in seen:
            continue
        remaining = budget - used
        if remaining <= 0:
            break
        clipped = line[:remaining]
        output.append(clipped)
        seen.add(key)
        used += len(clipped) + 1
    return output


def build_product_fingerprint(
    initial: AIDecisionPacket,
    fields: Iterable[dict[str, Any]],
    grounding: GroundingCatalog,
    *,
    compact_evidence: CompactEvidence | None = None,
    max_chars: int = 2_000,
) -> str:
    """Build a small search identity, never a second copy of all product evidence."""

    lines: list[str] = []
    if compact_evidence is not None:
        lines.extend(_fingerprint_lines(compact_evidence.web_text, budget=1_200))
        remaining = max(0, 1_600 - sum(len(item) + 1 for item in lines))
        lines.extend(_fingerprint_lines(compact_evidence.image_facts, budget=remaining))
    else:
        supplier_text = "\n".join(
            source.content
            for source in grounding.sources
            if source.source_type == "supplier_web" and source.kind == TEXT_KIND
        )
        lines.extend(_fingerprint_lines(supplier_text, budget=1_600))

    for item in _local_anchor_payload(initial, fields):
        rendered = f"{item['attribute_key']}={item['status']}"
        if item["status"] == READY:
            rendered += ":" + ",".join(item.get("values") or [])
        elif item["status"] == CONFLICT:
            alternatives = [
                "/".join(alternative.get("values") or [])
                for alternative in item.get("alternatives") or []
            ]
            rendered += ":" + " vs ".join(alternatives)
        if rendered not in lines:
            lines.append(rendered)
        if sum(len(value) + 1 for value in lines) >= max_chars:
            break
    return "\n".join(lines)[:max_chars]


def _research_prompt(
    initial: AIDecisionPacket,
    all_fields: list[dict[str, Any]],
    targets: list[tuple[dict[str, Any], FieldDecision]],
    *,
    product_url: str,
    product_fingerprint: str,
) -> str:
    payload = {
        "task": "research_product_and_comparable_products_then_fill_all_missing_fields",
        "canonical_product": {
            "source_product_url": product_url.strip(),
            "fingerprint": product_fingerprint,
        },
        "target_fields": [
            _web_target_payload(field, decision)
            for field, decision in targets
        ],
        "workflow": [
            "Actually invoke the built-in Web search tool now. Search for this product, its model family, and genuinely comparable products using a few product-level queries.",
            "Reuse the real pages returned by the tool across all unresolved fields; do not invent or guess URLs.",
            "Classify returned pages as same_product, same_product_family, similar_product, uncertain, or different_product. Reject only clearly different_product pages.",
            "Return fields supported by the returned pages. A separate text-only inference stage handles everything still unresolved.",
        ],
        "rules": [
            "Locally READY and CONFLICT fields are frozen and must never be rewritten.",
            "Exact product identity is preferred but not required. Similar and uncertain pages may provide evidence; only different_product is unusable.",
            "Every citation URL must be a real URL returned by this Web search call. For inferred values, cite the closest comparable-product evidence and explain the inference.",
            "Use READY for direct comparable-product evidence, REVIEW for material uncertainty, and CONFLICT for direct disagreement.",
            "Do not rotate dimension axes or mix packaging/body/mount, cabin/rear, documentation/device-interface language, product/vehicle compatibility, display resolution/recording resolution, or other neighboring field scopes.",
            "If multi_value=false return one value. If qualifier_options exist, use an exact allowed qualifier; if qualifier_options are empty, qualifier must be empty.",
            "Never research seller-operated price, stock, MOQ, fulfilment, shipping policy or listing-status fields.",
            "Return decisions only for fields found in real returned pages. Omit everything else; the separate inference stage will handle it.",
            "Return one JSON object only.",
        ],
        "json_contract": {
            "source_matches": [
                {
                    "source_url": "exact URL returned by this web search",
                    "match": "same_product | same_product_family | similar_product | uncertain | different_product",
                    "identity_basis": "canonical_offer_or_url | exact_variant_identifier | manufacturer_identifier | global_trade_identifier | explicit_cross_reference | generic_model_or_similarity | none",
                    "reason": "short identity judgment",
                    "identity_evidence": ["concrete evidence for the declared identity basis"],
                }
            ],
            "decisions": [
                {
                    "field_id": "exact target field_id",
                    "status": "ready | review | conflict | missing",
                    "values": ["value for READY"],
                    "qualifier": "optional exact qualifier",
                    "confidence": 0.0,
                    "citations": [
                        {
                            "source_url": "URL returned by this search and not classified as different_product",
                            "evidence_text": "direct evidence for this exact target field",
                        }
                    ],
                    "alternatives": [
                        {
                            "values": ["conflicting value"],
                            "qualifier": "optional qualifier",
                            "citations": [
                                {
                                    "source_url": "URL returned by this search and not classified as different_product",
                                    "evidence_text": "direct evidence for this exact target field alternative",
                                }
                            ],
                        }
                    ],
                    "reason": "short explanation",
                }
            ],
            "summary": "string",
        },
    }
    return (
        "Search the Web now for one product and comparable products, then fill unresolved fields only from real returned pages. "
        "Exact identity is preferred but not required; reject only clearly different products. Return JSON only.\n\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


def _normalize_url(value: str) -> str:
    return value.strip().rstrip("/")


def _web_source_reference(url: str) -> str:
    digest = hashlib.sha256(_normalize_url(url).encode("utf-8")).hexdigest()
    return f"web-search:{digest[:20]}"


def _source_lookup(items: Iterable[WebSearchSource]) -> dict[str, WebSearchSource]:
    output: dict[str, WebSearchSource] = {}
    for item in items:
        key = _normalize_url(item.url)
        if key and key not in output:
            output[key] = item
    return output


def _search_cache_key(
    provider: SourcedWebSearchProvider,
    initial: AIDecisionPacket,
    all_fields: list[dict[str, Any]],
    targets: list[tuple[dict[str, Any], FieldDecision]],
    product_url: str,
    product_fingerprint: str,
) -> str:
    payload = {
        "cache_version": WEB_SEARCH_CACHE_VERSION,
        "contract_version": WEB_SEARCH_CONTRACT_VERSION,
        "provider": provider.name,
        "model": provider.model,
        "source_manifest_sha256": initial.source_manifest_sha256,
        "product_url": product_url.strip(),
        "product_fingerprint": product_fingerprint,
        "targets": [
            _web_target_payload(field, decision)
            for field, decision in targets
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _serialize_search_result(result: WebSearchJSONResult) -> dict[str, Any]:
    return {
        "payload": result.payload,
        "sources": [item.as_dict() for item in result.sources],
        "request_id": result.request_id,
    }


def _deserialize_search_result(payload: dict[str, Any]) -> WebSearchJSONResult:
    return WebSearchJSONResult(
        payload=dict(payload.get("payload") or {}),
        sources=[
            WebSearchSource(
                index=str(item.get("index") or ""),
                title=str(item.get("title") or ""),
                url=str(item.get("url") or ""),
                site_name=str(item.get("site_name") or ""),
            )
            for item in payload.get("sources") or []
            if isinstance(item, dict) and str(item.get("url") or "").strip()
        ],
        request_id=str(payload.get("request_id") or ""),
    )


def _run_research(
    provider: SourcedWebSearchProvider,
    initial: AIDecisionPacket,
    all_fields: list[dict[str, Any]],
    targets: list[tuple[dict[str, Any], FieldDecision]],
    product_url: str,
    product_fingerprint: str,
    *,
    cache_dir: Path | None,
) -> tuple[WebSearchJSONResult | None, int, bool, str]:
    key = _search_cache_key(
        provider,
        initial,
        all_fields,
        targets,
        product_url,
        product_fingerprint,
    )
    cache_path = cache_dir / f"web-product-research-{key}.json" if cache_dir is not None else None
    if cache_path is not None and cache_path.is_file():
        try:
            cached = _deserialize_search_result(json.loads(cache_path.read_text(encoding="utf-8")))
            return cached, 0, True, ""
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

    try:
        result = provider.search_json(
            _research_prompt(
                initial,
                all_fields,
                targets,
                product_url=product_url,
                product_fingerprint=product_fingerprint,
            )
        )
    except Exception as exc:
        return None, 1, False, f"web product research failed: {exc}"

    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp = cache_path.with_suffix(cache_path.suffix + ".tmp")
        temp.write_text(json.dumps(_serialize_search_result(result), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(cache_path)
    return result, 1, False, ""


def _parse_source_matches(
    result: WebSearchJSONResult,
) -> tuple[list[WebSourceMatch], dict[str, WebSearchSource], list[str]]:
    returned = _source_lookup(result.sources)
    accepted: dict[str, WebSearchSource] = {}
    matches: list[WebSourceMatch] = []
    warnings: list[str] = []
    raw_matches = result.payload.get("source_matches") or []
    if not isinstance(raw_matches, list):
        return [], {}, ["web product research response missing source_matches array"]

    seen: set[str] = set()
    for item in raw_matches:
        if not isinstance(item, dict):
            continue
        url = str(item.get("source_url") or "").strip()
        key = _normalize_url(url)
        source = returned.get(key)
        if source is None or not key or key in seen:
            continue
        seen.add(key)
        reported_match = str(item.get("match") or "").strip().casefold()
        if reported_match not in SOURCE_MATCHES:
            warnings.append(f"web source match ignored invalid status for {url}")
            continue
        basis = str(item.get("identity_basis") or "none").strip().casefold()
        if basis not in IDENTITY_BASES:
            warnings.append(f"web source match used invalid identity_basis for {url}; downgraded to uncertain")
            basis = "none"
        identity_evidence = tuple(
            str(value).strip()
            for value in item.get("identity_evidence") or []
            if str(value).strip()
        )

        matches.append(
            WebSourceMatch(
                source_url=source.url,
                match=reported_match,
                reason=str(item.get("reason") or "").strip(),
                identity_basis=basis,
                identity_evidence=identity_evidence,
            )
        )
        if reported_match in EVIDENCE_USABLE_MATCHES:
            accepted[key] = source
    return matches, accepted, warnings


def _accepted_citations(
    raw: Any,
    accepted_sources: dict[str, WebSearchSource],
    *,
    field_identifier: str,
    evidence: list[WebEvidence],
    evidence_by_url: dict[str, list[str]],
) -> list[DecisionCitation]:
    if not isinstance(raw, list):
        return []
    output: list[DecisionCitation] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = str(item.get("source_url") or "").strip()
        text = str(item.get("evidence_text") or "").strip()
        url_key = _normalize_url(url)
        source = accepted_sources.get(url_key)
        if source is None or not text:
            continue
        reference = _web_source_reference(source.url)
        fingerprint = (reference, normalize_key(text))
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        output.append(DecisionCitation(reference, text))
        evidence.append(
            WebEvidence(
                field_id=field_identifier,
                source_reference=reference,
                source_url=source.url,
                evidence_text=text,
            )
        )
        evidence_by_url.setdefault(url_key, []).append(text)
    return output


def _parse_web_decisions(
    result: WebSearchJSONResult,
    target_ids: set[str],
    accepted_sources: dict[str, WebSearchSource],
) -> tuple[list[FieldDecision], list[WebEvidence], list[PersistedWebSource], list[str]]:
    decisions: list[FieldDecision] = []
    evidence: list[WebEvidence] = []
    warnings: list[str] = []
    evidence_by_url: dict[str, list[str]] = {}
    seen_decisions: set[str] = set()

    raw_decisions = result.payload.get("decisions") or []
    if not isinstance(raw_decisions, list):
        return [], [], [], ["web product research response missing decisions array"]

    for raw_index, raw in enumerate(raw_decisions, start=1):
        if not isinstance(raw, dict):
            continue
        identifier = str(raw.get("field_id") or "").strip()
        if identifier not in target_ids or identifier in seen_decisions:
            continue

        citations = _accepted_citations(
            raw.get("citations"),
            accepted_sources,
            field_identifier=identifier,
            evidence=evidence,
            evidence_by_url=evidence_by_url,
        )
        alternatives: list[dict[str, Any]] = []
        for alternative in raw.get("alternatives") or []:
            if not isinstance(alternative, dict):
                continue
            alt_citations = _accepted_citations(
                alternative.get("citations"),
                accepted_sources,
                field_identifier=identifier,
                evidence=evidence,
                evidence_by_url=evidence_by_url,
            )
            alternatives.append(
                {
                    "values": list(alternative.get("values") or []),
                    "qualifier": str(alternative.get("qualifier") or ""),
                    "citations": [item.as_dict() for item in alt_citations],
                    "reason": "",
                }
            )

        normalized = {
            "field_id": identifier,
            "status": str(raw.get("status") or MISSING).strip().casefold(),
            "values": list(raw.get("values") or []),
            "qualifier": str(raw.get("qualifier") or ""),
            "confidence": raw.get("confidence", 0.0),
            "citations": [item.as_dict() for item in citations],
            "alternatives": alternatives,
            "reason": str(raw.get("reason") or ""),
            "search_queries": [],
        }
        try:
            decision = FieldDecision.from_mapping(normalized, index=raw_index)
        except (TypeError, ValueError) as exc:
            warnings.append(f"web fill field {identifier}: invalid decision ignored: {exc}")
            continue
        decisions.append(decision)
        seen_decisions.add(identifier)

    persisted: list[PersistedWebSource] = []
    for url_key, evidence_items in evidence_by_url.items():
        source = accepted_sources.get(url_key)
        if source is None:
            continue
        unique: list[str] = []
        seen: set[str] = set()
        for text in evidence_items:
            key = normalize_key(text)
            if key and key not in seen:
                seen.add(key)
                unique.append(text)
        content = "\n".join(
            [
                *([f"Search result title: {source.title}"] if source.title else []),
                f"Search result URL: {source.url}",
                *[f"Search evidence: {text}" for text in unique],
            ]
        )
        persisted.append(
            PersistedWebSource(
                source_reference=_web_source_reference(source.url),
                url=source.url,
                title=source.title,
                site_name=source.site_name,
                content=content,
                request_id=result.request_id,
            )
        )
    return decisions, evidence, persisted, warnings


def _external_source_map(items: Iterable[PersistedWebSource]) -> dict[str, str]:
    return {item.source_reference: item.content for item in items}


def run_web_enrichment(
    search_provider: SourcedWebSearchProvider,
    initial: AIDecisionPacket,
    fields: Iterable[dict[str, Any]],
    grounding: GroundingCatalog,
    *,
    product_url: str = "",
    batch_size: int = 5,
    concurrency: int = 3,
    cache_dir: str | Path | None = None,
    compact_evidence: CompactEvidence | None = None,
) -> WebEnrichmentResult:
    """Run one product-level Web research session, then fill only MISSING fields."""

    if int(batch_size) < 1:
        raise ValueError("web batch_size 必须 >= 1。")
    if int(concurrency) < 1:
        raise ValueError("web concurrency 必须 >= 1。")

    field_list = list(fields)
    targets = _targets(initial, field_list)
    if not targets:
        return WebEnrichmentResult(packet=initial, target_field_count=0, searched=False)

    product_fingerprint = build_product_fingerprint(
        initial,
        field_list,
        grounding,
        compact_evidence=compact_evidence,
    )
    cache_root = Path(cache_dir) if cache_dir is not None else None
    started = time.monotonic()
    result, model_calls, cache_hit, warning = _run_research(
        search_provider,
        initial,
        field_list,
        targets,
        product_url,
        product_fingerprint,
        cache_dir=cache_root,
    )
    warnings: list[str] = [warning] if warning else []
    if result is None:
        return WebEnrichmentResult(
            packet=initial,
            target_field_count=len(targets),
            search_batch_count=1,
            search_model_calls=model_calls,
            search_cache_hits=1 if cache_hit else 0,
            search_failed_batches=1,
            searched=True,
            search_elapsed_seconds=time.monotonic() - started,
            warnings=warnings,
        )

    source_matches, accepted_sources, match_warnings = _parse_source_matches(result)
    warnings.extend(match_warnings)
    target_ids = {decision.field_id for _, decision in targets}
    raw_updates, evidence, web_sources, parse_warnings = _parse_web_decisions(
        result,
        target_ids,
        accepted_sources,
    )
    warnings.extend(parse_warnings)
    external = _external_source_map(web_sources)

    update_fields = [field for field in field_list if field_id(field) in target_ids]
    candidate = AIDecisionPacket(
        identity=initial.identity,
        schema_sha256=schema_digest(update_fields),
        source_manifest_sha256=initial.source_manifest_sha256,
        decisions=raw_updates,
        model_summary="One product-level Web research session enriched only MISSING Makro fields.",
        warnings=[],
        extractor=f"{search_provider.name}+product-research",
    )
    validated_updates = validate_ai_decision_packet(
        candidate,
        update_fields,
        grounding,
        expected_identity=initial.identity,
        external_sources=external,
    )
    usable_updates = {
        decision.field_id: decision
        for decision in validated_updates.decisions
        if decision.status in {READY, REVIEW, CONFLICT}
    }
    merged = AIDecisionPacket(
        identity=initial.identity,
        schema_sha256=initial.schema_sha256,
        source_manifest_sha256=initial.source_manifest_sha256,
        decisions=[usable_updates.get(item.field_id, item) for item in initial.decisions],
        model_summary=(initial.model_summary + "\nWeb product research resolved or proposed candidates only for previously unresolved fields.").strip(),
        warnings=[*initial.warnings, *warnings, *validated_updates.warnings],
        extractor=(initial.extractor + "+product-research").strip("+"),
    )
    final_packet = validate_ai_decision_packet(
        merged,
        field_list,
        grounding,
        expected_identity=initial.identity,
        external_sources=external,
    )

    return WebEnrichmentResult(
        packet=final_packet,
        web_sources=web_sources,
        evidence=evidence,
        source_matches=source_matches,
        target_field_count=len(targets),
        search_batch_count=1,
        search_model_calls=model_calls,
        search_cache_hits=1 if cache_hit else 0,
        search_failed_batches=0,
        searched=True,
        search_elapsed_seconds=time.monotonic() - started,
        warnings=warnings,
    )


def write_enriched_ai_decision_packet(
    packet: AIDecisionPacket,
    web_sources: Iterable[PersistedWebSource],
    path: str | Path,
) -> Path:
    payload = packet.as_dict()
    payload["web_sources"] = [item.as_dict() for item in web_sources]
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target
