from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol

from .ai_decisions import (
    BUSINESS_LOCKED,
    CONFLICT,
    MISSING,
    READY,
    AIDecisionPacket,
    DecisionCitation,
    FieldDecision,
    field_contract,
    field_id,
    schema_digest,
    validate_ai_decision_packet,
)
from .business_fields import is_business_question
from .listing_content_policy import (
    CONTENT_POLICY_VERSION,
    GLOBAL_CONTENT_RULES,
    allow_best_effort_inference,
    field_content_policy,
)
from .semantic_grounding import GroundingCatalog
from .web_enrichment import PersistedWebSource, WebEvidence


INFERENCE_CONTRACT_VERSION = 5
INFERENCE_CACHE_VERSION = 5
INFERENCE_REFERENCE = "model-inference:category-knowledge"
INFERENCE_URL = "model-inference://category-knowledge"
INFERENCE_CONTENT = (
    "Best-effort model inference from the captured product fingerprint, already resolved product facts, "
    "real comparable-product Web evidence, ordinary product-category knowledge, and seller content policy."
)


class JSONTaskProvider(Protocol):
    name: str

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass(slots=True)
class BestEffortInferenceResult:
    packet: AIDecisionPacket
    inference_source: PersistedWebSource
    target_count: int
    ready_count: int
    missing_count: int
    model_calls: int
    cache_hit: bool
    failed: bool
    elapsed_seconds: float
    warning: str = ""


def _is_business(field: dict[str, Any]) -> bool:
    contract = field_contract(field)
    return is_business_question(contract["attribute_key"]) or is_business_question(contract["label"])


def _target(field: dict[str, Any]) -> dict[str, Any]:
    contract = field_contract(field)
    section = contract["section_heading"].casefold()
    output: dict[str, Any] = {
        "field_id": field_id(field),
        "key": contract["attribute_key"],
        "label": contract["label"],
        "section": (
            "S"
            if "price, stock and shipping" in section
            else "P"
            if "product description" in section
            else "A"
        ),
        "multi_value": contract["multi_value"],
    }
    if contract["options"]:
        output["options"] = contract["options"]
    if contract["qualifier_options"]:
        output["qualifier_options"] = contract["qualifier_options"]
    if contract["context_text"]:
        output["context_text"] = contract["context_text"]
    content_policy = field_content_policy(field)
    if content_policy:
        output["content_policy"] = content_policy
    return output


def _resolved_context(
    packet: AIDecisionPacket,
    fields: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_id = {field_id(field): field for field in fields}
    output: list[dict[str, Any]] = []
    for decision in packet.decisions:
        if decision.status not in {READY, CONFLICT}:
            continue
        field = by_id.get(decision.field_id)
        if field is None or _is_business(field):
            continue
        item: dict[str, Any] = {
            "key": field_contract(field)["attribute_key"],
            "status": decision.status,
        }
        if decision.status == READY:
            item["values"] = list(decision.values)
            if decision.qualifier:
                item["qualifier"] = decision.qualifier
        else:
            item["alternatives"] = [list(value.values) for value in decision.alternatives]
        output.append(item)
    return output


def _web_context(evidence: Iterable[WebEvidence], *, max_chars: int = 4_000) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    used = 0
    for item in evidence:
        text = item.evidence_text.strip()
        if not text:
            continue
        remaining = max_chars - used
        if remaining <= 0:
            break
        clipped = text[:remaining]
        output.append(
            {
                "field_id": item.field_id,
                "source_reference": item.source_reference,
                "evidence": clipped,
            }
        )
        used += len(clipped)
    return output


def _json_schema(targets: list[dict[str, Any]]) -> dict[str, Any]:
    identifiers = [item["field_id"] for item in targets]
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "field_id": {"type": "string", "enum": identifiers},
                        "status": {"type": "string", "enum": [READY, MISSING]},
                        "values": {"type": "array", "items": {"type": "string"}},
                        "qualifier": {"type": "string"},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "reason": {"type": "string"},
                    },
                    "required": ["field_id", "status", "values", "qualifier", "confidence", "reason"],
                },
            },
            "summary": {"type": "string"},
        },
        "required": ["decisions", "summary"],
    }


def build_best_effort_inference_request(
    packet: AIDecisionPacket,
    fields: Iterable[dict[str, Any]],
    *,
    product_fingerprint: str,
    web_evidence: Iterable[WebEvidence] = (),
) -> dict[str, Any]:
    field_list = list(fields)
    by_id = {field_id(field): field for field in field_list}
    target_fields = [
        by_id[decision.field_id]
        for decision in packet.decisions
        if decision.status == MISSING
        and decision.field_id in by_id
        and not _is_business(by_id[decision.field_id])
        and allow_best_effort_inference(by_id[decision.field_id])
    ]
    targets = [_target(field) for field in target_fields]
    return {
        "task": "best_effort_infer_remaining_marketplace_fields",
        "system_instruction": (
            "Improve remaining marketplace product fields using already resolved facts, real comparable-product "
            "evidence and ordinary product knowledge without inventing unsupported identifiers, compliance facts, "
            "package contents or seller promises. Return JSON only."
        ),
        "prompt_instruction": (
            "Return exactly one decision per target. Prefer a useful READY answer only when the available context "
            "and target content_policy can responsibly support it; otherwise return MISSING. Use lower confidence "
            "for inference. Preserve physical scope and obey each target's multi_value/options/qualifier/content_policy contract exactly."
        ),
        "evidence_policy": "best_effort",
        "content_policy_version": CONTENT_POLICY_VERSION,
        "context": {
            "product_fingerprint": product_fingerprint,
            "resolved_fields": _resolved_context(packet, field_list),
            "web_evidence": _web_context(web_evidence),
        },
        "target_fields": targets,
        "rules": [
            "Use direct/resolved facts first, then real comparable-product evidence, then reasonable category knowledge only when the target content_policy does not forbid inference.",
            "Negative Yes/No values may be inferred only when the target policy allows best-effort inference and the resolved/comparable context supports the conclusion.",
            "Do not contradict resolved READY or CONFLICT fields.",
            "Never assert either alternative of a resolved CONFLICT inside a generated description or neighboring field.",
            "Keep packaging/product body/mount dimensions and front/cabin/rear scopes separate. Packaging dimensions may fill only section S logistics fields, never product-body Width/Height/Depth or mount dimensions.",
            "For scoped dimensions, map keys literally without rotating axes: length->length, breadth->breadth, height->height, weight->weight.",
            "An unscoped viewing angle must not be assigned to Exterior or Interior Field of View.",
            "If multi_value=false, return exactly one value string. If several compatible facets belong in a free-text field, combine them into one concise readable string rather than returning several values.",
            "For options, return one exact allowed value. For qualifier_options, qualifier must be one exact allowed qualifier. If qualifier_options are absent, qualifier must be empty unless context_text explicitly renders a fixed physical unit.",
            "qualifier is only a marketplace unit/qualifier, never explanation, scope commentary, confidence text or field description.",
            "For numeric targets with a qualifier option or fixed unit in context_text, return a bare finite number in values and the unit in qualifier; never embed the unit token inside the numeric value.",
            "Never invent exact identifiers, certifications/compliance claims, exact package contents, legal entities, seller policies or seller-operated facts. If a target policy requires exact evidence, it will not be present in this best-effort target set.",
            *GLOBAL_CONTENT_RULES,
        ],
        "grounded_sources": [],
        "all_marketplace_fields": [],
        "json_contract": _json_schema(targets),
        "strict_json_schema": True,
    }


def _cache_key(
    provider: JSONTaskProvider,
    request: dict[str, Any],
    cache_namespace: str,
) -> str:
    raw = json.dumps(
        {
            "cache_version": INFERENCE_CACHE_VERSION,
            "contract_version": INFERENCE_CONTRACT_VERSION,
            "provider": provider.name,
            "namespace": cache_namespace,
            "request": request,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_updates(raw: Any, target_ids: set[str]) -> list[FieldDecision]:
    if not isinstance(raw, dict) or not isinstance(raw.get("decisions"), list):
        raise ValueError("best-effort inference response requires decisions array")
    output: list[FieldDecision] = []
    seen: set[str] = set()
    citation = DecisionCitation(INFERENCE_REFERENCE, INFERENCE_CONTENT)
    for index, item in enumerate(raw["decisions"], start=1):
        if not isinstance(item, dict):
            continue
        identifier = str(item.get("field_id") or "").strip()
        if identifier not in target_ids or identifier in seen:
            continue
        seen.add(identifier)
        status = str(item.get("status") or MISSING).strip().casefold()
        values = [str(value).strip() for value in item.get("values") or [] if str(value).strip()]
        if status == READY and values:
            output.append(
                FieldDecision(
                    field_id=identifier,
                    status=READY,
                    values=values,
                    qualifier=str(item.get("qualifier") or "").strip(),
                    confidence=min(float(item.get("confidence", 0.6)), 0.75),
                    citations=[citation],
                    reason=(str(item.get("reason") or "").strip() or "best-effort model inference"),
                )
            )
        else:
            output.append(
                FieldDecision(
                    field_id=identifier,
                    status=MISSING,
                    reason=str(item.get("reason") or "").strip(),
                )
            )
    return output


def run_best_effort_inference(
    provider: JSONTaskProvider,
    packet: AIDecisionPacket,
    fields: Iterable[dict[str, Any]],
    grounding: GroundingCatalog,
    *,
    product_fingerprint: str,
    web_sources: Iterable[PersistedWebSource] = (),
    web_evidence: Iterable[WebEvidence] = (),
    cache_dir: str | Path | None = None,
    cache_namespace: str = "",
) -> BestEffortInferenceResult:
    started = time.monotonic()
    field_list = list(fields)
    source_list = list(web_sources)
    evidence_list = list(web_evidence)
    inference_source = PersistedWebSource(
        source_reference=INFERENCE_REFERENCE,
        url=INFERENCE_URL,
        title="Best-effort model inference",
        site_name="local-model",
        content=INFERENCE_CONTENT,
    )
    request = build_best_effort_inference_request(
        packet,
        field_list,
        product_fingerprint=product_fingerprint,
        web_evidence=evidence_list,
    )
    target_ids = {item["field_id"] for item in request["target_fields"]}
    if not target_ids:
        return BestEffortInferenceResult(
            packet, inference_source, 0, 0, 0, 0, False, False, time.monotonic() - started
        )

    key = _cache_key(provider, request, cache_namespace)
    cache_path = Path(cache_dir) / f"best-effort-inference-{key}.json" if cache_dir is not None else None
    cache_hit = False
    model_calls = 0
    try:
        if cache_path is not None and cache_path.is_file():
            raw = json.loads(cache_path.read_text(encoding="utf-8"))
            cache_hit = True
        else:
            raw = provider.extract_json(request)
            model_calls = 1
            if cache_path is not None:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = cache_path.with_suffix(cache_path.suffix + ".tmp")
                temporary.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
                temporary.replace(cache_path)
        updates = _parse_updates(raw, target_ids)
    except Exception as exc:
        return BestEffortInferenceResult(
            packet,
            inference_source,
            len(target_ids),
            0,
            len(target_ids),
            max(model_calls, 1),
            cache_hit,
            True,
            time.monotonic() - started,
            str(exc),
        )

    external = {source.source_reference: source.content for source in source_list}
    external[INFERENCE_REFERENCE] = INFERENCE_CONTENT
    updates_by_id = {item.field_id: item for item in updates}
    merged = AIDecisionPacket(
        identity=packet.identity,
        schema_sha256=packet.schema_sha256,
        source_manifest_sha256=packet.source_manifest_sha256,
        decisions=[updates_by_id.get(item.field_id, item) for item in packet.decisions],
        model_summary=(packet.model_summary + "\nBest-effort text-only inference filled policy-eligible remaining product fields.").strip(),
        warnings=list(packet.warnings),
        extractor=(packet.extractor + "+best-effort-inference").strip("+"),
    )
    final_packet = validate_ai_decision_packet(
        merged,
        field_list,
        grounding,
        expected_identity=packet.identity,
        external_sources=external,
    )
    inferred = [item for item in final_packet.decisions if item.field_id in target_ids]
    ready_count = sum(item.status == READY for item in inferred)
    missing_count = sum(item.status == MISSING for item in inferred)
    return BestEffortInferenceResult(
        final_packet,
        inference_source,
        len(target_ids),
        ready_count,
        missing_count,
        model_calls,
        cache_hit,
        False,
        time.monotonic() - started,
    )
