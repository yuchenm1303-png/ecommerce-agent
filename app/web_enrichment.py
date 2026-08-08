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
    AIDecisionError,
    AIDecisionPacket,
    DecisionAlternative,
    DecisionCitation,
    FieldDecision,
    field_contract,
    field_id,
    load_ai_decision_packet,
    validate_ai_decision_packet,
)
from .business_fields import is_business_question
from .evidence_contract import ProductIdentity
from .providers.dashscope_web_search import WebSearchJSONResult, WebSearchSource
from .semantic_grounding import GroundingCatalog
from .source_bundle import normalize_key


WEB_ENRICHMENT_CONTRACT_VERSION = 1
WEB_ENRICHMENT_CACHE_VERSION = 1
WEB_UPDATABLE_STATUSES = {MISSING, REVIEW, CONFLICT}


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

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "PersistedWebSource":
        reference = str(payload.get("source_reference") or "").strip()
        url = str(payload.get("url") or "").strip()
        content = str(payload.get("content") or "").strip()
        if not reference or not url or not content:
            raise AIDecisionError("persisted web source 缺少 source_reference/url/content。")
        return cls(
            source_reference=reference,
            url=url,
            title=str(payload.get("title") or "").strip(),
            site_name=str(payload.get("site_name") or "").strip(),
            content=content,
            request_id=str(payload.get("request_id") or "").strip(),
        )


@dataclass(slots=True)
class WebEnrichmentResult:
    packet: AIDecisionPacket
    web_sources: list[PersistedWebSource] = field(default_factory=list)
    model_calls: int = 0
    cache_hit: bool = False
    target_field_count: int = 0
    searched: bool = False
    elapsed_seconds: float = 0.0
    warning: str = ""


def _field_is_business(field: dict[str, Any]) -> bool:
    return is_business_question(str(field.get("attribute_key") or "")) or is_business_question(
        str(field.get("label") or "")
    )


def _target_decisions(
    packet: AIDecisionPacket,
    fields: Iterable[dict[str, Any]],
) -> list[tuple[dict[str, Any], FieldDecision]]:
    by_id = {field_id(field): field for field in fields}
    output: list[tuple[dict[str, Any], FieldDecision]] = []
    for decision in packet.decisions:
        field = by_id.get(decision.field_id)
        if field is None or _field_is_business(field):
            continue
        if decision.status in WEB_UPDATABLE_STATUSES and decision.search_queries:
            output.append((field, decision))
    return output


def _prior_decision_payload(decision: FieldDecision) -> dict[str, Any]:
    return {
        "status": decision.status,
        "values": list(decision.values),
        "qualifier": decision.qualifier,
        "confidence": decision.confidence,
        "citations": [item.as_dict() for item in decision.citations],
        "alternatives": [item.as_dict() for item in decision.alternatives],
        "reason": decision.reason,
        "search_queries": list(decision.search_queries),
    }


def _web_prompt(
    packet: AIDecisionPacket,
    targets: list[tuple[dict[str, Any], FieldDecision]],
) -> str:
    payload = {
        "task": "research_and_resolve_only_the_unresolved_marketplace_fields",
        "product_identity": {
            "sku": packet.identity.sku,
            "model_number": packet.identity.model_number,
            "brand": packet.identity.brand,
        },
        "model_summary": packet.model_summary,
        "target_fields": [
            {
                "field_id": decision.field_id,
                **field_contract(field),
                "prior_decision": _prior_decision_payload(decision),
            }
            for field, decision in targets
        ],
        "rules": [
            "Use web search only for these target fields; never change fields that were already READY.",
            "Preserve exact product and selected-variant identity; generic model names require extra caution.",
            "Use each target's search_queries as the research starting point and combine searches when efficient.",
            "READY means web research supports one best answer for the exact current product/variant.",
            "REVIEW means a plausible answer exists but identity, scope or evidence is not strong enough for automatic entry.",
            "CONFLICT means credible web/local evidence remains materially incompatible; preserve alternatives.",
            "MISSING means bounded normal web research did not establish the value.",
            "Never infer seller-operated price, stock, MOQ, fulfilment, shipping or listing status.",
            "Every READY/REVIEW citation and every CONFLICT alternative citation must use the exact source_url of a page actually returned by this search call.",
            "Do not invent URLs and do not treat lack of mention as No/False/Not included.",
            "When target options are supplied, return exact marketplace option text when one clearly matches.",
            "Return exactly one JSON object and no markdown.",
        ],
        "json_contract": {
            "decisions": [
                {
                    "field_id": "exact target field_id",
                    "status": "ready|review|conflict|missing",
                    "values": ["strings"],
                    "qualifier": "string",
                    "confidence": "0..1",
                    "citations": [
                        {
                            "source_url": "exact URL returned by web search",
                            "evidence_text": "concise evidence supporting the field",
                        }
                    ],
                    "alternatives": [
                        {
                            "values": ["strings"],
                            "qualifier": "string",
                            "citations": [
                                {
                                    "source_url": "exact URL returned by web search",
                                    "evidence_text": "concise evidence",
                                }
                            ],
                            "reason": "string",
                        }
                    ],
                    "reason": "string",
                }
            ],
            "summary": "string",
        },
    }
    return (
        "You are the single bounded web-research phase of an AI-first product listing resolver. "
        "Research all unresolved targets in this one call and return JSON only.\n\n"
        + json.dumps(payload, ensure_ascii=False)
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


def _convert_citations(
    payload: Any,
    source_lookup: dict[str, WebSearchSource],
    evidence_by_url: dict[str, list[str]],
) -> list[DecisionCitation]:
    if not isinstance(payload, list):
        return []
    output: list[DecisionCitation] = []
    seen: set[tuple[str, str]] = set()
    for item in payload:
        if not isinstance(item, dict):
            continue
        url = str(item.get("source_url") or "").strip()
        evidence = str(item.get("evidence_text") or "").strip()
        source = source_lookup.get(_normalize_url(url))
        if source is None or not evidence:
            continue
        reference = _web_source_reference(source.url)
        fingerprint = (reference, normalize_key(evidence))
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        output.append(DecisionCitation(reference, evidence))
        evidence_by_url.setdefault(_normalize_url(source.url), []).append(evidence)
    return output


def _convert_alternatives(
    payload: Any,
    source_lookup: dict[str, WebSearchSource],
    evidence_by_url: dict[str, list[str]],
) -> list[DecisionAlternative]:
    if not isinstance(payload, list):
        return []
    output: list[DecisionAlternative] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        raw_values = item.get("values") or []
        values = (
            tuple(str(value).strip() for value in raw_values if str(value).strip())
            if isinstance(raw_values, list)
            else ()
        )
        output.append(
            DecisionAlternative(
                values=values,
                qualifier=str(item.get("qualifier") or "").strip(),
                citations=tuple(
                    _convert_citations(item.get("citations"), source_lookup, evidence_by_url)
                ),
                reason=str(item.get("reason") or "").strip(),
            )
        )
    return output


def _float_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _parse_web_updates(
    payload: dict[str, Any],
    targets: list[tuple[dict[str, Any], FieldDecision]],
    search_result: WebSearchJSONResult,
) -> tuple[dict[str, FieldDecision], list[PersistedWebSource], list[str]]:
    target_ids = {decision.field_id for _, decision in targets}
    source_lookup = _source_lookup(search_result.sources)
    evidence_by_url: dict[str, list[str]] = {}
    warnings: list[str] = []
    updates: dict[str, FieldDecision] = {}

    raw_decisions = payload.get("decisions") or []
    if not isinstance(raw_decisions, list):
        raise AIDecisionError("web enrichment 响应缺少 decisions 数组。")

    for item in raw_decisions:
        if not isinstance(item, dict):
            continue
        identifier = str(item.get("field_id") or "").strip()
        if identifier not in target_ids:
            warnings.append(f"web update ignored unknown/non-target field_id={identifier!r}")
            continue
        if identifier in updates:
            raise AIDecisionError(f"web enrichment field_id 重复：{identifier}")
        status = str(item.get("status") or "").strip().casefold()
        if status not in WEB_UPDATABLE_STATUSES | {READY}:
            warnings.append(f"web update {identifier}: invalid status={status!r}; kept prior")
            continue
        raw_values = item.get("values") or []
        values = (
            [str(value).strip() for value in raw_values if str(value).strip()]
            if isinstance(raw_values, list)
            else []
        )
        updates[identifier] = FieldDecision(
            field_id=identifier,
            status=status,
            values=values,
            qualifier=str(item.get("qualifier") or "").strip(),
            confidence=_float_confidence(item.get("confidence")),
            citations=_convert_citations(
                item.get("citations"), source_lookup, evidence_by_url
            ),
            alternatives=_convert_alternatives(
                item.get("alternatives"), source_lookup, evidence_by_url
            ),
            reason=str(item.get("reason") or "").strip(),
            search_queries=[],
        )

    persisted: list[PersistedWebSource] = []
    for normalized_url, evidence_items in evidence_by_url.items():
        source = source_lookup[normalized_url]
        unique_evidence: list[str] = []
        seen: set[str] = set()
        for evidence in evidence_items:
            key = normalize_key(evidence)
            if key and key not in seen:
                seen.add(key)
                unique_evidence.append(evidence)
        content = "\n".join(
            part
            for part in [
                f"Search result title: {source.title}" if source.title else "",
                f"Search result URL: {source.url}",
                *[f"Search-model evidence: {item}" for item in unique_evidence],
            ]
            if part
        ).strip()
        persisted.append(
            PersistedWebSource(
                source_reference=_web_source_reference(source.url),
                url=source.url,
                title=source.title,
                site_name=source.site_name,
                content=content,
                request_id=search_result.request_id,
            )
        )
    return updates, persisted, warnings


def _external_source_map(web_sources: Iterable[PersistedWebSource]) -> dict[str, str]:
    output: dict[str, str] = {}
    for source in web_sources:
        if source.source_reference in output:
            raise AIDecisionError(
                f"web source_reference 重复：{source.source_reference}"
            )
        output[source.source_reference] = source.content
    return output


def _merge_packet(
    initial: AIDecisionPacket,
    updates: dict[str, FieldDecision],
    web_sources: list[PersistedWebSource],
    local_grounding: GroundingCatalog,
    fields: list[dict[str, Any]],
    warnings: list[str],
    summary: str,
) -> AIDecisionPacket:
    candidate = AIDecisionPacket(
        identity=initial.identity,
        schema_sha256=initial.schema_sha256,
        # Web evidence is embedded separately. This digest continues to bind the
        # exact original customer/images/supplier source pack.
        source_manifest_sha256=initial.source_manifest_sha256,
        decisions=[updates.get(item.field_id, item) for item in initial.decisions],
        model_summary=(
            initial.model_summary
            + ("\nWeb enrichment: " + summary if summary else "\nWeb enrichment completed.")
        ).strip(),
        warnings=[*initial.warnings, *warnings],
        extractor=(initial.extractor + "+dashscope-web-search").strip("+"),
    )
    return validate_ai_decision_packet(
        candidate,
        fields,
        local_grounding,
        expected_identity=initial.identity,
        external_sources=_external_source_map(web_sources),
    )


def _packet_fingerprint(packet: AIDecisionPacket) -> str:
    raw = json.dumps(packet.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _web_cache_key(
    provider: SourcedWebSearchProvider,
    initial: AIDecisionPacket,
    targets: list[tuple[dict[str, Any], FieldDecision]],
) -> str:
    payload = {
        "cache_version": WEB_ENRICHMENT_CACHE_VERSION,
        "contract_version": WEB_ENRICHMENT_CONTRACT_VERSION,
        "provider": provider.name,
        "model": provider.model,
        "initial_packet_sha256": _packet_fingerprint(initial),
        "targets": [
            {
                "field_id": decision.field_id,
                "search_queries": decision.search_queries,
                "contract": field_contract(field),
            }
            for field, decision in targets
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def enriched_packet_payload(
    packet: AIDecisionPacket,
    web_sources: Iterable[PersistedWebSource],
) -> dict[str, Any]:
    payload = packet.as_dict()
    payload["web_sources"] = [source.as_dict() for source in web_sources]
    return payload


def write_enriched_ai_decision_packet(
    packet: AIDecisionPacket,
    web_sources: Iterable[PersistedWebSource],
    path: str | Path,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(enriched_packet_payload(packet, web_sources), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


def load_enriched_ai_decision_packet(
    path: str | Path,
    fields: Iterable[dict[str, Any]],
    local_grounding: GroundingCatalog,
    *,
    expected_identity: ProductIdentity = ProductIdentity(),
) -> AIDecisionPacket:
    # Unified loader already understands optional embedded web_sources.
    return load_ai_decision_packet(
        path,
        fields,
        local_grounding,
        expected_identity=expected_identity,
    )


def run_web_enrichment(
    provider: SourcedWebSearchProvider,
    initial: AIDecisionPacket,
    fields: Iterable[dict[str, Any]],
    local_grounding: GroundingCatalog,
    *,
    cache_dir: str | Path | None = None,
) -> WebEnrichmentResult:
    """Research all AI-requested gaps in at most one sourced web call.

    Existing READY and business fields are immutable. Optional search failure
    preserves the valid local decision packet. Repeated identical research can
    replay from cache with zero web model calls.
    """

    started = time.monotonic()
    field_list = list(fields)
    targets = _target_decisions(initial, field_list)
    if not targets:
        return WebEnrichmentResult(
            packet=initial,
            elapsed_seconds=time.monotonic() - started,
        )

    cache_path: Path | None = None
    if cache_dir is not None:
        cache_path = Path(cache_dir) / (
            f"web-enrichment-{_web_cache_key(provider, initial, targets)}.json"
        )
        if cache_path.is_file():
            try:
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
                raw_sources = payload.get("web_sources") or []
                web_sources = [
                    PersistedWebSource.from_mapping(item)
                    for item in raw_sources
                    if isinstance(item, dict)
                ]
                packet = validate_ai_decision_packet(
                    AIDecisionPacket.from_mapping(payload),
                    field_list,
                    local_grounding,
                    expected_identity=initial.identity,
                    external_sources=_external_source_map(web_sources),
                )
                return WebEnrichmentResult(
                    packet=packet,
                    web_sources=web_sources,
                    model_calls=0,
                    cache_hit=True,
                    target_field_count=len(targets),
                    searched=True,
                    elapsed_seconds=time.monotonic() - started,
                )
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass

    try:
        search_result = provider.search_json(_web_prompt(initial, targets))
        updates, web_sources, warnings = _parse_web_updates(
            search_result.payload,
            targets,
            search_result,
        )
        packet = _merge_packet(
            initial,
            updates,
            web_sources,
            local_grounding,
            field_list,
            warnings,
            str(search_result.payload.get("summary") or "").strip(),
        )
    except Exception as exc:
        return WebEnrichmentResult(
            packet=initial,
            model_calls=1,
            target_field_count=len(targets),
            searched=True,
            elapsed_seconds=time.monotonic() - started,
            warning=f"web enrichment failed; local decisions preserved: {exc}",
        )

    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp = cache_path.with_suffix(cache_path.suffix + ".tmp")
        temp.write_text(
            json.dumps(
                enriched_packet_payload(packet, web_sources),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temp.replace(cache_path)

    return WebEnrichmentResult(
        packet=packet,
        web_sources=web_sources,
        model_calls=1,
        target_field_count=len(targets),
        searched=True,
        elapsed_seconds=time.monotonic() - started,
    )
