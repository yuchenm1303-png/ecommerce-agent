from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .ai_decisions import (
    AIDecisionPacket,
    FieldDecision,
    field_contract,
    field_id,
    schema_digest,
    source_manifest_digest,
    validate_ai_decision_packet,
)
from .business_fields import is_business_question
from .product_profile import (
    JSONTaskProvider,
    PROFILE_CONFLICT,
    ProductFact,
    ProductProfile,
    profile_digest,
)
from .semantic_grounding import GroundingCatalog
from .source_bundle import normalize_key


FIELD_MAPPING_CONTRACT_VERSION = 3
FIELD_MAPPING_CACHE_VERSION = 3


MAPPING_SYSTEM_INSTRUCTION = (
    "You map a compact, already-grounded product profile into marketplace fields. "
    "Do not invent, rename or reclassify product facts. Use only facts explicitly present in PRODUCT_PROFILE. "
    "Translation, synonym matching and marketplace option matching are allowed, but fact scope must be compatible with target_scope. "
    "Return compact JSON only."
)

MAPPING_RULES = [
    "Answer only the supplied target field_id values.",
    "READY requires one strongly supported profile answer, at least one underlying original citation copied from the profile, and one or more profile_fact_ids that directly support this exact target meaning and scope.",
    "If target_scope is present, at least one cited profile fact must explicitly match that scope. Generic product scope is not enough for packaging, product-with-bracket, exterior-camera or interior-camera targets.",
    "A profile fact with status=conflict can never authorize READY. Preserve it as CONFLICT/REVIEW, or omit that unresolved subclaim from broad free-text fields.",
    "If no profile fact directly supports the target meaning/scope, return REVIEW or MISSING; do not stretch a generic feature into a specialized marketplace field.",
    "The listing is for the selected variant. If a generic supplier/product fact conflicts with a selected_variant fact, never put the generic conflicting value into READY or into READY free-text summaries.",
    "REVIEW means a plausible mapping exists but the profile does not establish the exact target semantics or scope strongly enough for automatic entry.",
    "CONFLICT means the profile contains credible conflicting candidates for the same target scope; preserve at least two cited alternatives and never choose one silently.",
    "MISSING means the profile cannot answer this field. Do not invent a value.",
    "Never infer No/False/Not included from absence. Negative values need explicit negative evidence in the profile.",
    "Keep packaging dimensions/weight separate from product-body dimensions/weight; cabin/interior is not rear/back; manual language is not device UI language; product brand is not compatible vehicle brand.",
    "Use exact marketplace option text when target options clearly match the cited profile fact.",
    "If multi_value=false, return one string in values. Combine several supported free-text features into one concise string only when every included feature directly belongs to that field meaning and none is unresolved/conflicting.",
    "If qualifier_options exist, put the value/magnitude only in values and the exact unit once in qualifier.",
    "Citations must use the underlying original source_reference values embedded inside the cited profile facts, never the derived profile source_id.",
    "Do not use external web knowledge.",
]


MAPPING_JSON_SCHEMA: dict[str, Any] = {
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
                    "search_queries": {"type": "array", "items": {"type": "string"}},
                    "profile_fact_ids": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "model_summary": {"type": "string"},
    },
    "required": ["decisions"],
}


def _field_is_business(field: dict[str, Any]) -> bool:
    contract = field_contract(field)
    return is_business_question(contract["attribute_key"]) or is_business_question(
        contract["label"]
    )


def _contains_any(value: str, tokens: tuple[str, ...]) -> bool:
    return any(token in value for token in tokens)


def target_scope(field: dict[str, Any]) -> str:
    """Derive only narrow marketplace scopes that are deterministic from the live field contract."""
    contract = field_contract(field)
    key = normalize_key(contract["attribute_key"])
    label = normalize_key(contract["label"])
    section = normalize_key(contract["section_heading"])
    text = f"{key} {label}"

    if _contains_any(text, ("with bracket", "with_bracket", "including bracket")):
        return "product_with_bracket"

    angle_like = _contains_any(text, ("field of view", "field_of_view", "fov", "viewing angle", "viewing_angle"))
    if angle_like and _contains_any(text, ("exterior", "outside", "front camera", "front_camera", "front lens", "front_lens")):
        return "exterior_camera"
    if angle_like and _contains_any(text, ("interior", "cabin", "inside", "interior_camera", "cabin_camera")):
        return "interior_camera"

    dimensional = _contains_any(
        text,
        (
            "length",
            "breadth",
            "width",
            "height",
            "depth",
            "dimension",
            "weight",
        ),
    )
    packaging_marker = _contains_any(
        text,
        ("package", "packaging", "packed", "carton", "shipping"),
    ) or "price stock and shipping information" in section
    if dimensional and packaging_marker:
        return "packaging"
    if dimensional:
        return "product_body"
    return ""


def _target_payload(field: dict[str, Any]) -> dict[str, Any]:
    contract = field_contract(field)
    payload: dict[str, Any] = {
        "field_id": field_id(field),
        "attribute_key": contract["attribute_key"],
        "label": contract["label"],
        "section_heading": contract["section_heading"],
        "required": contract["required"],
        "multi_value": contract["multi_value"],
    }
    scope = target_scope(field)
    if scope:
        payload["target_scope"] = scope
    if contract["options"]:
        payload["options"] = contract["options"]
    if contract["qualifier_options"]:
        payload["qualifier_options"] = contract["qualifier_options"]
    if contract["help_text"]:
        payload["help_text"] = contract["help_text"]
    return payload


def profile_fact_id(fact: ProductFact) -> str:
    raw = json.dumps(
        fact.as_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "pf_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _profile_facts(profile: ProductProfile) -> list[dict[str, Any]]:
    return [
        {"fact_id": profile_fact_id(fact), **fact.as_dict()}
        for fact in profile.facts
    ]


def _profile_source(profile: ProductProfile) -> dict[str, Any]:
    return {
        "source_id": "product-profile:derived",
        "source_type": "derived_product_profile",
        "kind": "text",
        "origin": "product-profile.json",
        "content": json.dumps(
            {
                "summary": profile.summary,
                "facts": _profile_facts(profile),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }


def build_field_mapping_request(
    batch_fields: Iterable[dict[str, Any]],
    profile: ProductProfile,
) -> dict[str, Any]:
    fields = list(batch_fields)
    return {
        "task": "map_product_profile_to_marketplace_fields",
        "system_instruction": MAPPING_SYSTEM_INSTRUCTION,
        "prompt_instruction": (
            "Map this small target-field batch from PRODUCT_PROFILE only. "
            "For every READY decision include profile_fact_ids copied exactly from PRODUCT_PROFILE, and verify their fact scope against target_scope before answering. "
            "Return one decision for each target field and no prose outside JSON."
        ),
        "product_identity": {
            "sku": profile.identity.sku,
            "model_number": profile.identity.model_number,
            "brand": profile.identity.brand,
        },
        "target_fields": [_target_payload(field) for field in fields],
        "rules": list(MAPPING_RULES),
        "grounded_sources": [_profile_source(profile)],
        "json_contract": MAPPING_JSON_SCHEMA,
    }


def mapping_contract_digest() -> str:
    raw = json.dumps(
        {
            "version": FIELD_MAPPING_CONTRACT_VERSION,
            "system": MAPPING_SYSTEM_INSTRUCTION,
            "rules": MAPPING_RULES,
            "schema": MAPPING_JSON_SCHEMA,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_key(
    provider: JSONTaskProvider,
    cache_namespace: str,
    batch_fields: list[dict[str, Any]],
    profile: ProductProfile,
) -> str:
    payload = {
        "cache_version": FIELD_MAPPING_CACHE_VERSION,
        "contract_sha256": mapping_contract_digest(),
        "provider": provider.name,
        "cache_namespace": cache_namespace,
        "profile_sha256": profile_digest(profile),
        "batch_schema_sha256": schema_digest(batch_fields),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _scope_compatible(fact_scope: str, required_scope: str) -> bool:
    if not required_scope:
        return True
    scope = normalize_key(fact_scope)
    if required_scope == "packaging":
        return _contains_any(scope, ("packag", "carton", "shipping", "packed"))
    if required_scope == "product_body":
        return _contains_any(scope, ("product body", "product_body", "device body", "device_body", "unit body", "unit_body"))
    if required_scope == "product_with_bracket":
        return _contains_any(scope, ("with bracket", "with_bracket", "including bracket", "mounted"))
    if required_scope == "exterior_camera":
        return _contains_any(scope, ("exterior", "outside", "front camera", "front_camera", "front lens", "front_lens"))
    if required_scope == "interior_camera":
        return _contains_any(scope, ("interior", "cabin", "inside", "interior_camera", "cabin_camera"))
    return False


def _citation_fingerprint(source_reference: str, evidence_text: str) -> tuple[str, str]:
    return source_reference.strip(), normalize_key(evidence_text)


def _fact_citations(fact: ProductFact) -> set[tuple[str, str]]:
    return {
        _citation_fingerprint(citation.source_reference, citation.evidence_text)
        for candidate in fact.candidates
        for citation in candidate.citations
    }


def _raw_decision_citations(item: dict[str, Any]) -> set[tuple[str, str]]:
    output: set[tuple[str, str]] = set()
    raw = item.get("citations") or []
    if not isinstance(raw, list):
        return output
    for citation in raw:
        if not isinstance(citation, dict):
            continue
        reference = str(citation.get("source_reference") or "").strip()
        evidence = str(citation.get("evidence_text") or citation.get("evidence") or "").strip()
        if reference and evidence:
            output.add(_citation_fingerprint(reference, evidence))
    return output


def _downgrade_ready(item: dict[str, Any], guard: str) -> None:
    item["status"] = "review"
    reason = str(item.get("reason") or "").strip()
    item["reason"] = f"{reason} | {guard}".strip(" |")


def _enforce_profile_fact_refs(
    raw_decisions: list[Any],
    batch_fields: list[dict[str, Any]],
    profile: ProductProfile,
) -> list[dict[str, Any]]:
    facts = {profile_fact_id(fact): fact for fact in profile.facts}
    fields = {field_id(field): field for field in batch_fields}
    output: list[dict[str, Any]] = []
    for raw in raw_decisions:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        if str(item.get("status") or "").strip().casefold() != "ready":
            output.append(item)
            continue

        raw_ids = item.get("profile_fact_ids") or []
        ids = (
            [str(value).strip() for value in raw_ids if str(value).strip()]
            if isinstance(raw_ids, list)
            else []
        )
        if not ids or any(value not in facts for value in ids):
            _downgrade_ready(
                item,
                "READY rejected: missing or invalid profile_fact_ids; local mapping may only use explicit Product Profile facts.",
            )
            output.append(item)
            continue

        referenced = [facts[value] for value in ids]
        if any(fact.status == PROFILE_CONFLICT for fact in referenced):
            _downgrade_ready(
                item,
                "READY rejected: a cited Product Profile fact is unresolved conflict.",
            )
            output.append(item)
            continue

        target = fields.get(str(item.get("field_id") or "").strip())
        required_scope = target_scope(target) if target is not None else ""
        if required_scope and not any(
            _scope_compatible(fact.scope, required_scope) for fact in referenced
        ):
            _downgrade_ready(
                item,
                f"READY rejected: cited fact scope does not support target_scope={required_scope}.",
            )
            output.append(item)
            continue

        claimed_citations = set().union(*(_fact_citations(fact) for fact in referenced))
        decision_citations = _raw_decision_citations(item)
        if decision_citations and not decision_citations.issubset(claimed_citations):
            _downgrade_ready(
                item,
                "READY rejected: decision citation is not contained in the claimed profile_fact_ids.",
            )
        output.append(item)
    return output


@dataclass(slots=True)
class _BatchRun:
    index: int
    decisions: list[FieldDecision]
    model_calls: int
    cache_hit: bool
    warning: str = ""


def _run_batch(
    provider: JSONTaskProvider,
    batch_index: int,
    batch_fields: list[dict[str, Any]],
    profile: ProductProfile,
    grounding: GroundingCatalog,
    *,
    cache_dir: Path | None,
    cache_namespace: str,
) -> _BatchRun:
    key = _cache_key(provider, cache_namespace, batch_fields, profile)
    cache_path = cache_dir / f"field-map-{key}.json" if cache_dir is not None else None
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
            )
            return _BatchRun(batch_index, validated.decisions, 0, True)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

    try:
        raw = provider.extract_json(build_field_mapping_request(batch_fields, profile))
        raw_decisions = raw.get("decisions") if isinstance(raw, dict) else None
        if not isinstance(raw_decisions, list):
            raise ValueError("field mapping AI output 缺少 decisions 数组")
        guarded = _enforce_profile_fact_refs(raw_decisions, batch_fields, profile)
        packet = AIDecisionPacket(
            identity=profile.identity,
            schema_sha256=schema_digest(batch_fields),
            source_manifest_sha256=source_manifest_digest(grounding),
            decisions=[
                FieldDecision.from_mapping(item, index=index)
                for index, item in enumerate(guarded, start=1)
            ],
            model_summary=str(raw.get("model_summary") or "").strip(),
            warnings=[],
            extractor=str(raw.get("extractor") or provider.name).strip() or provider.name,
        )
        validated = validate_ai_decision_packet(
            packet,
            batch_fields,
            grounding,
            expected_identity=profile.identity,
        )
    except Exception as exc:
        return _BatchRun(
            batch_index,
            [],
            1,
            False,
            warning=f"mapping batch {batch_index} failed: {exc}",
        )

    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp = cache_path.with_suffix(cache_path.suffix + ".tmp")
        temp.write_text(
            json.dumps(validated.as_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp.replace(cache_path)
    return _BatchRun(batch_index, validated.decisions, 1, False)


def _mechanical_batches(
    fields: list[dict[str, Any]],
    batch_size: int,
) -> list[list[dict[str, Any]]]:
    return [
        fields[index : index + batch_size]
        for index in range(0, len(fields), batch_size)
    ]


@dataclass(slots=True)
class FieldMappingRunResult:
    packet: AIDecisionPacket
    model_calls: int
    cache_hits: int
    batch_count: int
    failed_batches: int
    elapsed_seconds: float


def run_field_mapping(
    provider: JSONTaskProvider,
    fields: Iterable[dict[str, Any]],
    profile: ProductProfile,
    grounding: GroundingCatalog,
    *,
    batch_size: int = 12,
    concurrency: int = 4,
    cache_dir: str | Path | None = None,
    cache_namespace: str = "",
) -> FieldMappingRunResult:
    if not 1 <= int(batch_size) <= 30:
        raise ValueError("batch_size 必须在 1..30。")
    if not 1 <= int(concurrency) <= 12:
        raise ValueError("concurrency 必须在 1..12。")

    started = time.monotonic()
    field_list = list(fields)
    non_business = [field for field in field_list if not _field_is_business(field)]
    batches = _mechanical_batches(non_business, int(batch_size))
    cache_root = Path(cache_dir) if cache_dir is not None else None

    runs: list[_BatchRun] = []
    if batches:
        workers = min(int(concurrency), len(batches))
        with ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="field-map",
        ) as executor:
            futures = {
                executor.submit(
                    _run_batch,
                    provider,
                    index,
                    batch,
                    profile,
                    grounding,
                    cache_dir=cache_root,
                    cache_namespace=cache_namespace,
                ): index
                for index, batch in enumerate(batches, start=1)
            }
            for future in as_completed(futures):
                runs.append(future.result())
    runs.sort(key=lambda item: item.index)

    decisions: list[FieldDecision] = []
    warnings: list[str] = []
    for run in runs:
        decisions.extend(run.decisions)
        if run.warning:
            warnings.append(run.warning)

    candidate = AIDecisionPacket(
        identity=profile.identity,
        schema_sha256=schema_digest(field_list),
        source_manifest_sha256=source_manifest_digest(grounding),
        decisions=decisions,
        model_summary="Mapped Makro live fields from compact Product Profile facts.",
        warnings=warnings,
        extractor=f"{profile.extractor}+parallel-field-mapping".strip("+"),
    )
    validated = validate_ai_decision_packet(
        candidate,
        field_list,
        grounding,
        expected_identity=profile.identity,
    )
    return FieldMappingRunResult(
        packet=validated,
        model_calls=sum(run.model_calls for run in runs),
        cache_hits=sum(1 for run in runs if run.cache_hit),
        batch_count=len(batches),
        failed_batches=sum(1 for run in runs if run.warning),
        elapsed_seconds=time.monotonic() - started,
    )
