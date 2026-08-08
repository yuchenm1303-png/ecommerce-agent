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
    REVIEW,
    AIDecisionPacket,
    FieldDecision,
    field_contract,
    field_id,
    schema_digest,
    validate_ai_decision_packet,
)
from .business_fields import is_business_question
from .product_profile import JSONTaskProvider, ProductProfile, profile_digest
from .providers.dashscope_web_search import WebSearchJSONResult, WebSearchSource
from .semantic_grounding import GroundingCatalog
from .source_bundle import normalize_key


WEB_RESEARCH_CONTRACT_VERSION = 2
WEB_RESEARCH_CACHE_VERSION = 2
WEB_FINAL_CONTRACT_VERSION = 2
WEB_FINAL_CACHE_VERSION = 2
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
    final_batch_count: int = 0
    final_model_calls: int = 0
    final_cache_hits: int = 0
    final_failed_batches: int = 0
    final_cache_hit: bool = False
    searched: bool = False
    search_elapsed_seconds: float = 0.0
    final_elapsed_seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def model_calls(self) -> int:
        return self.search_model_calls + self.final_model_calls

    @property
    def cache_hit(self) -> bool:
        return self.searched and self.search_model_calls == 0 and self.final_model_calls == 0

    @property
    def elapsed_seconds(self) -> float:
        return self.search_elapsed_seconds + self.final_elapsed_seconds

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
        if decision.status in WEB_UPDATABLE_STATUSES:
            output.append((field, decision))
    return output


def _prior_payload(decision: FieldDecision) -> dict[str, Any]:
    return {
        "status": decision.status,
        "values": list(decision.values),
        "qualifier": decision.qualifier,
        "citations": [item.as_dict() for item in decision.citations],
        "alternatives": [item.as_dict() for item in decision.alternatives],
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
        "task": "research_evidence_for_unresolved_marketplace_fields",
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
            "Research only these unresolved fields for the exact current product/selected variant.",
            "This stage gathers evidence only; do not decide READY/REVIEW/CONFLICT/MISSING.",
            "Use model-authored search_queries when useful; otherwise derive focused queries from exact product identity plus the field.",
            "Prefer exact manufacturer/manual/current supplier evidence. Generic model-name pages require strong identity matching.",
            "Never research seller-operated price, stock, MOQ, fulfilment, shipping or listing status.",
            "Return only evidence from pages actually returned by this search call. Do not invent URLs.",
            "If evidence is ambiguous, still return the exact evidence and let the field resolver judge it.",
            "Return JSON only.",
        ],
        "json_contract": {
            "evidence": [
                {
                    "field_id": "exact target field_id",
                    "items": [
                        {
                            "source_url": "exact URL returned by web search",
                            "evidence_text": "concise evidence relevant to this field",
                        }
                    ],
                }
            ],
            "summary": "string",
        },
    }
    return (
        "You are the bounded web-research stage of a product-listing pipeline. "
        "Gather evidence for this small batch and return one JSON object only.\n\n"
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
    return [
        items[index : index + batch_size]
        for index in range(0, len(items), batch_size)
    ]


def _research_cache_key(
    provider: SourcedWebSearchProvider,
    profile: ProductProfile,
    targets: list[tuple[dict[str, Any], FieldDecision]],
) -> str:
    payload = {
        "cache_version": WEB_RESEARCH_CACHE_VERSION,
        "contract_version": WEB_RESEARCH_CONTRACT_VERSION,
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
class _ResearchBatchRun:
    index: int
    result: WebSearchJSONResult | None
    model_calls: int
    cache_hit: bool
    warning: str = ""


def _run_research_batch(
    provider: SourcedWebSearchProvider,
    profile: ProductProfile,
    batch_index: int,
    batch_targets: list[tuple[dict[str, Any], FieldDecision]],
    *,
    cache_dir: Path | None,
) -> _ResearchBatchRun:
    key = _research_cache_key(provider, profile, batch_targets)
    cache_path = cache_dir / f"web-research-{key}.json" if cache_dir is not None else None
    if cache_path is not None and cache_path.is_file():
        try:
            cached = _deserialize_search_result(
                json.loads(cache_path.read_text(encoding="utf-8"))
            )
            return _ResearchBatchRun(batch_index, cached, 0, True)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    try:
        result = provider.search_json(_research_prompt(profile, batch_targets))
    except Exception as exc:
        return _ResearchBatchRun(
            batch_index,
            None,
            1,
            False,
            warning=f"web research batch {batch_index} failed: {exc}",
        )
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp = cache_path.with_suffix(cache_path.suffix + ".tmp")
        temp.write_text(
            json.dumps(_serialize_search_result(result), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp.replace(cache_path)
    return _ResearchBatchRun(batch_index, result, 1, False)


def _accepted_evidence(
    runs: list[_ResearchBatchRun],
    target_ids: set[str],
) -> tuple[list[WebEvidence], list[PersistedWebSource], list[str]]:
    evidence: list[WebEvidence] = []
    warnings: list[str] = []
    source_meta: dict[str, WebSearchSource] = {}
    source_request: dict[str, str] = {}
    evidence_by_url: dict[str, list[str]] = {}
    seen_evidence: set[tuple[str, str, str]] = set()

    for run in runs:
        if run.warning:
            warnings.append(run.warning)
        if run.result is None:
            continue
        source_lookup = _source_lookup(run.result.sources)
        for url_key, source in source_lookup.items():
            source_meta.setdefault(url_key, source)
            source_request.setdefault(url_key, run.result.request_id)
        raw_evidence = run.result.payload.get("evidence") or []
        if not isinstance(raw_evidence, list):
            warnings.append(f"web research batch {run.index}: response missing evidence array")
            continue
        for group in raw_evidence:
            if not isinstance(group, dict):
                continue
            identifier = str(group.get("field_id") or "").strip()
            if identifier not in target_ids:
                continue
            items = group.get("items") or []
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("source_url") or "").strip()
                text = str(item.get("evidence_text") or "").strip()
                source = source_lookup.get(_normalize_url(url))
                if source is None or not text:
                    continue
                reference = _web_source_reference(source.url)
                fingerprint = (identifier, reference, normalize_key(text))
                if fingerprint in seen_evidence:
                    continue
                seen_evidence.add(fingerprint)
                evidence.append(
                    WebEvidence(
                        field_id=identifier,
                        source_reference=reference,
                        source_url=source.url,
                        evidence_text=text,
                    )
                )
                evidence_by_url.setdefault(_normalize_url(source.url), []).append(text)

    persisted: list[PersistedWebSource] = []
    for url_key, evidence_items in evidence_by_url.items():
        source = source_meta[url_key]
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
    return evidence, persisted, warnings


def _external_source_map(items: Iterable[PersistedWebSource]) -> dict[str, str]:
    return {item.source_reference: item.content for item in items}


def _evidence_for_fields(
    evidence: list[WebEvidence],
    identifiers: set[str],
) -> list[WebEvidence]:
    return [item for item in evidence if item.field_id in identifiers]


def _final_prompt_request(
    profile: ProductProfile,
    initial: AIDecisionPacket,
    batch_fields: list[dict[str, Any]],
    evidence: list[WebEvidence],
) -> dict[str, Any]:
    evidence_by_field: dict[str, list[WebEvidence]] = {}
    for item in evidence:
        evidence_by_field.setdefault(item.field_id, []).append(item)
    prior_by_id = {decision.field_id: decision for decision in initial.decisions}
    profile_text = json.dumps(
        _profile_payload(profile),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    web_text = json.dumps(
        {
            field_id(field): [
                item.as_dict()
                for item in evidence_by_field.get(field_id(field), [])
            ]
            for field in batch_fields
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return {
        "task": "finalize_small_marketplace_field_batch_from_profile_and_web_evidence",
        "system_instruction": (
            "You are a text-only field resolver. Resolve only this small marketplace field batch "
            "from the grounded Product Profile and the web evidence returned for these exact fields. "
            "Preserve genuine conflicts and never invent product facts. Return compact JSON only."
        ),
        "prompt_instruction": (
            "Resolve only these target fields. Do not change any other field. "
            "Return one decision per target field and no prose outside JSON."
        ),
        "product_identity": {
            "sku": profile.identity.sku,
            "model_number": profile.identity.model_number,
            "brand": profile.identity.brand,
        },
        "target_fields": [
            {
                "field_id": field_id(field),
                **field_contract(field),
                "prior_decision": _prior_payload(prior_by_id[field_id(field)]),
            }
            for field in batch_fields
        ],
        "rules": [
            "READY requires strong exact-product evidence and citations.",
            "If credible local/web evidence disagrees for the same attribute and scope and neither clearly supersedes the other, return CONFLICT with cited alternatives.",
            "Never infer negative values from absence. Keep packaging scope, product-body scope, cabin/rear, manual/UI language and compatibility scope distinct.",
            "For web citations use only source_reference values present in WEB_EVIDENCE. For local citations use only underlying source_reference values present in PRODUCT_PROFILE.",
            "Do not stretch generic evidence into a more specialized marketplace field.",
            "If multi_value=false return one value string. If qualifier_options exist, keep the unit only in qualifier.",
            "MISSING is valid when research still does not establish the exact value.",
        ],
        "grounded_sources": [
            {
                "source_id": "product-profile:derived",
                "source_type": "derived_product_profile",
                "kind": "text",
                "origin": "product-profile.json",
                "content": profile_text,
            },
            {
                "source_id": "web-research:derived",
                "source_type": "derived_web_research",
                "kind": "text",
                "origin": "web-research.json",
                "content": web_text,
            },
        ],
        "json_contract": {
            "type": "object",
            "properties": {
                "decisions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["field_id", "status"],
                        "properties": {
                            "field_id": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["ready", "review", "conflict", "missing"],
                            },
                            "values": {"type": "array", "items": {"type": "string"}},
                            "qualifier": {"type": "string"},
                            "confidence": {"type": "number"},
                            "citations": {"type": "array"},
                            "alternatives": {"type": "array"},
                            "reason": {"type": "string"},
                        },
                    },
                },
                "model_summary": {"type": "string"},
            },
            "required": ["decisions"],
        },
    }


def _packet_fingerprint(packet: AIDecisionPacket) -> str:
    raw = json.dumps(
        packet.as_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _final_cache_key(
    provider: JSONTaskProvider,
    cache_namespace: str,
    profile: ProductProfile,
    initial: AIDecisionPacket,
    batch_fields: list[dict[str, Any]],
    evidence: list[WebEvidence],
) -> str:
    payload = {
        "cache_version": WEB_FINAL_CACHE_VERSION,
        "contract_version": WEB_FINAL_CONTRACT_VERSION,
        "provider": provider.name,
        "cache_namespace": cache_namespace,
        "profile_sha256": profile_digest(profile),
        "initial_packet_sha256": _packet_fingerprint(initial),
        "batch_schema_sha256": schema_digest(batch_fields),
        "evidence": [item.as_dict() for item in evidence],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class _FinalBatchRun:
    index: int
    decisions: list[FieldDecision]
    model_calls: int
    cache_hit: bool
    warnings: list[str] = field(default_factory=list)


def _run_final_batch(
    provider: JSONTaskProvider,
    profile: ProductProfile,
    initial: AIDecisionPacket,
    batch_index: int,
    batch_fields: list[dict[str, Any]],
    grounding: GroundingCatalog,
    evidence: list[WebEvidence],
    external: dict[str, str],
    *,
    cache_dir: Path | None,
    cache_namespace: str,
) -> _FinalBatchRun:
    key = _final_cache_key(
        provider,
        cache_namespace,
        profile,
        initial,
        batch_fields,
        evidence,
    )
    cache_path = cache_dir / f"web-final-{key}.json" if cache_dir is not None else None
    if cache_path is not None and cache_path.is_file():
        try:
            cached = AIDecisionPacket.from_mapping(
                json.loads(cache_path.read_text(encoding="utf-8"))
            )
            validated = validate_ai_decision_packet(
                cached,
                batch_fields,
                grounding,
                expected_identity=profile.identity,
                external_sources=external,
            )
            return _FinalBatchRun(
                batch_index,
                validated.decisions,
                0,
                True,
                list(validated.warnings),
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

    try:
        raw = provider.extract_json(
            _final_prompt_request(profile, initial, batch_fields, evidence)
        )
        raw_decisions = raw.get("decisions") if isinstance(raw, dict) else None
        if not isinstance(raw_decisions, list):
            raise ValueError("final web resolve batch 缺少 decisions 数组")
        raw_ids = {
            str(item.get("field_id") or "").strip()
            for item in raw_decisions
            if isinstance(item, dict)
        }
        candidate = AIDecisionPacket(
            identity=profile.identity,
            schema_sha256=schema_digest(batch_fields),
            source_manifest_sha256=initial.source_manifest_sha256,
            decisions=[
                FieldDecision.from_mapping(item, index=index)
                for index, item in enumerate(raw_decisions, start=1)
                if isinstance(item, dict)
            ],
            model_summary=str(raw.get("model_summary") or "").strip(),
            warnings=[],
            extractor=str(raw.get("extractor") or provider.name).strip() or provider.name,
        )
        validated = validate_ai_decision_packet(
            candidate,
            batch_fields,
            grounding,
            expected_identity=profile.identity,
            external_sources=external,
        )
        decisions = [
            item for item in validated.decisions
            if item.field_id in raw_ids
        ]
    except Exception as exc:
        return _FinalBatchRun(
            batch_index,
            [],
            1,
            False,
            [f"final resolve batch {batch_index} failed; local decisions preserved: {exc}"],
        )

    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp = cache_path.with_suffix(cache_path.suffix + ".tmp")
        temp.write_text(
            json.dumps(validated.as_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp.replace(cache_path)
    return _FinalBatchRun(
        batch_index,
        decisions,
        1,
        False,
        list(validated.warnings),
    )


def _run_final_resolution(
    provider: JSONTaskProvider,
    profile: ProductProfile,
    initial: AIDecisionPacket,
    fields: list[dict[str, Any]],
    grounding: GroundingCatalog,
    evidence: list[WebEvidence],
    web_sources: list[PersistedWebSource],
    *,
    batch_size: int,
    concurrency: int,
    cache_dir: Path | None,
    cache_namespace: str,
) -> tuple[AIDecisionPacket, int, int, int, int, float, list[str]]:
    started = time.monotonic()
    evidence_ids = {item.field_id for item in evidence}
    prior_by_id = {decision.field_id: decision for decision in initial.decisions}
    target_fields = [
        item for item in fields
        if field_id(item) in evidence_ids
        and field_id(item) in prior_by_id
        and prior_by_id[field_id(item)].status in WEB_UPDATABLE_STATUSES
    ]
    if not target_fields:
        return initial, 0, 0, 0, 0, time.monotonic() - started, []

    batches = _mechanical_batches(target_fields, int(batch_size))
    external = _external_source_map(web_sources)
    runs: list[_FinalBatchRun] = []
    workers = min(int(concurrency), len(batches))
    with ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="web-final",
    ) as executor:
        futures = {}
        for index, batch in enumerate(batches, start=1):
            ids = {field_id(item) for item in batch}
            batch_evidence = _evidence_for_fields(evidence, ids)
            future = executor.submit(
                _run_final_batch,
                provider,
                profile,
                initial,
                index,
                batch,
                grounding,
                batch_evidence,
                external,
                cache_dir=cache_dir,
                cache_namespace=cache_namespace,
            )
            futures[future] = index
        for future in as_completed(futures):
            runs.append(future.result())
    runs.sort(key=lambda item: item.index)

    updates: dict[str, FieldDecision] = {}
    warnings = list(initial.warnings)
    for run in runs:
        warnings.extend(run.warnings)
        for decision in run.decisions:
            updates[decision.field_id] = decision

    merged = AIDecisionPacket(
        identity=initial.identity,
        schema_sha256=initial.schema_sha256,
        source_manifest_sha256=initial.source_manifest_sha256,
        decisions=[updates.get(item.field_id, item) for item in initial.decisions],
        model_summary=(
            initial.model_summary
            + "\nParallel web-evidence field resolution completed."
        ).strip(),
        warnings=warnings,
        extractor=(initial.extractor + "+parallel-web-final").strip("+"),
    )
    validated = validate_ai_decision_packet(
        merged,
        fields,
        grounding,
        expected_identity=profile.identity,
        external_sources=external,
    )
    return (
        validated,
        sum(run.model_calls for run in runs),
        sum(1 for run in runs if run.cache_hit),
        len(batches),
        sum(1 for run in runs if run.warnings and not run.decisions),
        time.monotonic() - started,
        warnings[len(initial.warnings):],
    )


def run_web_enrichment(
    search_provider: SourcedWebSearchProvider,
    final_provider: JSONTaskProvider,
    initial: AIDecisionPacket,
    fields: Iterable[dict[str, Any]],
    grounding: GroundingCatalog,
    profile: ProductProfile,
    *,
    batch_size: int = 5,
    concurrency: int = 3,
    cache_dir: str | Path | None = None,
    final_cache_namespace: str = "",
) -> WebEnrichmentResult:
    if not 1 <= int(batch_size) <= 12:
        raise ValueError("web batch_size 必须在 1..12。")
    if not 1 <= int(concurrency) <= 8:
        raise ValueError("web concurrency 必须在 1..8。")

    field_list = list(fields)
    targets = _targets(initial, field_list)
    if not targets:
        return WebEnrichmentResult(
            packet=initial,
            target_field_count=0,
            searched=False,
        )

    cache_root = Path(cache_dir) if cache_dir is not None else None
    batches = _mechanical_batches(targets, int(batch_size))
    research_started = time.monotonic()
    runs: list[_ResearchBatchRun] = []
    workers = min(int(concurrency), len(batches))
    with ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="web-research",
    ) as executor:
        futures = {
            executor.submit(
                _run_research_batch,
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
    search_elapsed = time.monotonic() - research_started

    target_ids = {decision.field_id for _, decision in targets}
    evidence, web_sources, warnings = _accepted_evidence(runs, target_ids)
    (
        final_packet,
        final_calls,
        final_cache_hits,
        final_batch_count,
        final_failed_batches,
        final_elapsed,
        final_warnings,
    ) = _run_final_resolution(
        final_provider,
        profile,
        initial,
        field_list,
        grounding,
        evidence,
        web_sources,
        batch_size=int(batch_size),
        concurrency=int(concurrency),
        cache_dir=cache_root,
        cache_namespace=final_cache_namespace,
    )
    warnings.extend(final_warnings)
    return WebEnrichmentResult(
        packet=final_packet,
        web_sources=web_sources,
        evidence=evidence,
        target_field_count=len(targets),
        search_batch_count=len(batches),
        search_model_calls=sum(run.model_calls for run in runs),
        search_cache_hits=sum(1 for run in runs if run.cache_hit),
        search_failed_batches=sum(1 for run in runs if run.warning),
        final_batch_count=final_batch_count,
        final_model_calls=final_calls,
        final_cache_hits=final_cache_hits,
        final_failed_batches=final_failed_batches,
        final_cache_hit=(
            final_batch_count > 0
            and final_cache_hits == final_batch_count
        ),
        searched=True,
        search_elapsed_seconds=search_elapsed,
        final_elapsed_seconds=final_elapsed,
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
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target
