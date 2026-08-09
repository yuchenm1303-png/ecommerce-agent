from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Protocol

from .ai_decisions import (
    CONFLICT,
    MISSING,
    READY,
    REVIEW,
    AIDecisionPacket,
    DecisionAlternative,
    DecisionCitation,
    FieldDecision,
    field_contract,
    field_id,
    schema_digest,
    validate_ai_decision_packet,
)
from .business_fields import is_business_question
from .product_profile import ProductProfile, profile_digest
from .providers.dashscope_web_search import WebSearchJSONResult, WebSearchSource
from .semantic_grounding import GroundingCatalog
from .source_bundle import normalize_key


WEB_SEARCH_CONTRACT_VERSION = 4
WEB_SEARCH_CACHE_VERSION = 4
WEB_FILLABLE_STATUSES = {MISSING, REVIEW}


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


@dataclass(slots=True)
class WebEnrichmentResult:
    packet: AIDecisionPacket
    web_sources: list[PersistedWebSource] = field(default_factory=list)
    evidence: list[WebEvidence] = field(default_factory=list)
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
    """Return only fields that are still empty/unusable after the local pass.

    Local READY answers and genuine local CONFLICT answers are frozen. Web search
    is a fill-the-blanks step, not a second pass over already-known product facts.
    """

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
        "citations": [item.as_dict() for item in decision.citations],
        "reason": decision.reason,
        "search_queries": list(decision.search_queries),
    }


def _profile_payload(profile: ProductProfile) -> dict[str, Any]:
    return {
        "product_identity": {
            "sku": profile.identity.sku,
            "model_number": profile.identity.model_number,
            "brand": profile.identity.brand,
        },
        "summary": profile.summary,
        "facts": [fact.as_dict() for fact in profile.facts],
    }


def _research_prompt(
    profile: ProductProfile,
    targets: list[tuple[dict[str, Any], FieldDecision]],
) -> str:
    payload = {
        "task": "search_and_fill_missing_marketplace_fields",
        "product_profile": _profile_payload(profile),
        "target_fields": [
            {
                "field_id": decision.field_id,
                **field_contract(field),
                "prior_decision": _prior_payload(decision),
            }
            for field, decision in targets
        ],
        "rules": [
            "These fields were not filled from the supplied local product material. Search only these fields; do not revisit fields that were already locally READY or CONFLICT.",
            "Use the Product Profile only to identify the exact current product/selected variant and to formulate focused searches. A seller SKU may be internal and does not need to appear on public pages.",
            "For each field: return READY if web research establishes the exact value; return CONFLICT if credible web sources genuinely disagree; return MISSING if the value still cannot be established.",
            "READY citations must use source_url values actually returned by this same web-search call and must include concise evidence_text supporting the exact field value.",
            "Every CONFLICT alternative must include its own source_url/evidence_text citations from pages actually returned by this same web-search call.",
            "Never invent a URL, product fact, option, qualifier or negative claim. Never infer No/False/Unsupported/Not included merely because a feature is absent from a page.",
            "Treat attribute_key as authoritative when labels are generic or duplicated. Preserve dimension axes and scope exactly; do not mix packaging/body/mount, cabin/rear, manual/UI language, product/vehicle compatibility, or generic features with storage-specific fields.",
            "Prefer the exact current supplier/offer, manufacturer manual, official product page, or another source that clearly applies to this product/variant. Generic family pages are usable only when the evidence clearly applies.",
            "If multi_value=false return one value. If qualifier_options exist, put the magnitude in values and the unit in qualifier.",
            "Never research seller-operated price, stock, MOQ, fulfilment, shipping policy or listing-status fields.",
            "Return one decision for every supplied field_id and JSON only.",
        ],
        "json_contract": {
            "decisions": [
                {
                    "field_id": "exact target field_id",
                    "status": "ready | conflict | missing",
                    "values": ["value for READY"],
                    "qualifier": "optional exact qualifier",
                    "confidence": 0.0,
                    "citations": [
                        {
                            "source_url": "exact URL returned by this web search",
                            "evidence_text": "concise evidence supporting READY",
                        }
                    ],
                    "alternatives": [
                        {
                            "values": ["conflicting value"],
                            "qualifier": "optional qualifier",
                            "citations": [
                                {
                                    "source_url": "exact URL returned by this web search",
                                    "evidence_text": "evidence for this alternative",
                                }
                            ],
                            "reason": "optional",
                        }
                    ],
                    "reason": "short explanation",
                }
            ],
            "summary": "string",
        },
    }
    return (
        "You are the web fill step of a product-listing workflow. Search the web for only "
        "the still-empty fields below and directly return the field answers. Do not add a "
        "separate review pass. Return one JSON object only.\n\n"
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


def _mechanical_batches(items: list[Any], batch_size: int) -> list[list[Any]]:
    return [items[index : index + batch_size] for index in range(0, len(items), batch_size)]


def _search_cache_key(
    provider: SourcedWebSearchProvider,
    profile: ProductProfile,
    targets: list[tuple[dict[str, Any], FieldDecision]],
) -> str:
    payload = {
        "cache_version": WEB_SEARCH_CACHE_VERSION,
        "contract_version": WEB_SEARCH_CONTRACT_VERSION,
        "provider": provider.name,
        "model": provider.model,
        "profile_sha256": profile_digest(profile),
        "targets": [
            {
                "field_id": decision.field_id,
                "field": field_contract(field),
                "prior": _prior_payload(decision),
            }
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


@dataclass(slots=True)
class _SearchBatchRun:
    index: int
    result: WebSearchJSONResult | None
    model_calls: int
    cache_hit: bool
    warning: str = ""


def _run_search_batch(
    provider: SourcedWebSearchProvider,
    profile: ProductProfile,
    batch_index: int,
    batch_targets: list[tuple[dict[str, Any], FieldDecision]],
    *,
    cache_dir: Path | None,
) -> _SearchBatchRun:
    key = _search_cache_key(provider, profile, batch_targets)
    cache_path = cache_dir / f"web-fill-{key}.json" if cache_dir is not None else None
    if cache_path is not None and cache_path.is_file():
        try:
            cached = _deserialize_search_result(json.loads(cache_path.read_text(encoding="utf-8")))
            return _SearchBatchRun(batch_index, cached, 0, True)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

    try:
        result = provider.search_json(_research_prompt(profile, batch_targets))
    except Exception as exc:
        return _SearchBatchRun(
            batch_index,
            None,
            1,
            False,
            warning=f"web fill batch {batch_index} failed: {exc}",
        )

    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp = cache_path.with_suffix(cache_path.suffix + ".tmp")
        temp.write_text(
            json.dumps(_serialize_search_result(result), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp.replace(cache_path)
    return _SearchBatchRun(batch_index, result, 1, False)


def _accepted_citations(
    raw: Any,
    source_lookup: dict[str, WebSearchSource],
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
        source = source_lookup.get(url_key)
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
    runs: list[_SearchBatchRun],
    target_ids: set[str],
) -> tuple[list[FieldDecision], list[WebEvidence], list[PersistedWebSource], list[str]]:
    decisions: list[FieldDecision] = []
    evidence: list[WebEvidence] = []
    warnings: list[str] = []
    source_meta: dict[str, WebSearchSource] = {}
    source_request: dict[str, str] = {}
    evidence_by_url: dict[str, list[str]] = {}
    seen_decisions: set[str] = set()

    for run in runs:
        if run.warning:
            warnings.append(run.warning)
        if run.result is None:
            continue
        lookup = _source_lookup(run.result.sources)
        for url_key, source in lookup.items():
            source_meta.setdefault(url_key, source)
            source_request.setdefault(url_key, run.result.request_id)

        raw_decisions = run.result.payload.get("decisions") or []
        if not isinstance(raw_decisions, list):
            warnings.append(f"web fill batch {run.index}: response missing decisions array")
            continue

        for raw_index, raw in enumerate(raw_decisions, start=1):
            if not isinstance(raw, dict):
                continue
            identifier = str(raw.get("field_id") or "").strip()
            if identifier not in target_ids or identifier in seen_decisions:
                continue

            citations = _accepted_citations(
                raw.get("citations"),
                lookup,
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
                    lookup,
                    field_identifier=identifier,
                    evidence=evidence,
                    evidence_by_url=evidence_by_url,
                )
                alternatives.append(
                    {
                        "values": list(alternative.get("values") or []),
                        "qualifier": str(alternative.get("qualifier") or ""),
                        "citations": [item.as_dict() for item in alt_citations],
                        "reason": str(alternative.get("reason") or ""),
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
        source = source_meta.get(url_key)
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
                request_id=source_request.get(url_key, ""),
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
    profile: ProductProfile,
    *,
    batch_size: int = 5,
    concurrency: int = 3,
    cache_dir: str | Path | None = None,
) -> WebEnrichmentResult:
    """Fill only still-unresolved fields with one web-search AI pass.

    There is deliberately no second Final Resolve model call. The search model
    both finds the information and returns the field decision. Python only
    verifies that cited URLs really came from the search call and that the
    resulting decision packet is structurally executable.
    """

    if not 1 <= int(batch_size) <= 12:
        raise ValueError("web batch_size 必须在 1..12。")
    if not 1 <= int(concurrency) <= 8:
        raise ValueError("web concurrency 必须在 1..8。")

    field_list = list(fields)
    targets = _targets(initial, field_list)
    if not targets:
        return WebEnrichmentResult(packet=initial, target_field_count=0, searched=False)

    cache_root = Path(cache_dir) if cache_dir is not None else None
    batches = _mechanical_batches(targets, int(batch_size))
    started = time.monotonic()
    runs: list[_SearchBatchRun] = []
    workers = min(int(concurrency), len(batches))
    with ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="web-fill",
    ) as executor:
        futures = {
            executor.submit(
                _run_search_batch,
                search_provider,
                profile,
                index,
                batch,
                cache_dir=cache_root,
            ): index
            for index, batch in enumerate(batches, start=1)
        }
        for future in as_completed(futures):
            runs.append(future.result())
    runs.sort(key=lambda item: item.index)

    target_ids = {decision.field_id for _, decision in targets}
    raw_updates, evidence, web_sources, warnings = _parse_web_decisions(runs, target_ids)
    external = _external_source_map(web_sources)

    update_fields = [field for field in field_list if field_id(field) in target_ids]
    candidate = AIDecisionPacket(
        identity=initial.identity,
        schema_sha256=schema_digest(update_fields),
        source_manifest_sha256=initial.source_manifest_sha256,
        decisions=raw_updates,
        model_summary="Web search filled the unresolved Makro fields.",
        warnings=[],
        extractor=f"{search_provider.name}+web-fill",
    )
    validated_updates = validate_ai_decision_packet(
        candidate,
        update_fields,
        grounding,
        expected_identity=profile.identity,
        external_sources=external,
    )

    # Web is allowed to replace an unresolved local field only when it produced
    # a usable answer or a genuine cited conflict. Invalid/incomplete web output
    # simply leaves the original unresolved field untouched.
    usable_updates = {
        decision.field_id: decision
        for decision in validated_updates.decisions
        if decision.status in {READY, CONFLICT}
    }
    merged = AIDecisionPacket(
        identity=initial.identity,
        schema_sha256=initial.schema_sha256,
        source_manifest_sha256=initial.source_manifest_sha256,
        decisions=[usable_updates.get(item.field_id, item) for item in initial.decisions],
        model_summary=(initial.model_summary + "\nWeb filled only previously unresolved fields.").strip(),
        warnings=[*initial.warnings, *warnings, *validated_updates.warnings],
        extractor=(initial.extractor + "+web-fill").strip("+"),
    )
    final_packet = validate_ai_decision_packet(
        merged,
        field_list,
        grounding,
        expected_identity=profile.identity,
        external_sources=external,
    )

    return WebEnrichmentResult(
        packet=final_packet,
        web_sources=web_sources,
        evidence=evidence,
        target_field_count=len(targets),
        search_batch_count=len(batches),
        search_model_calls=sum(run.model_calls for run in runs),
        search_cache_hits=sum(1 for run in runs if run.cache_hit),
        search_failed_batches=sum(1 for run in runs if run.warning),
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
