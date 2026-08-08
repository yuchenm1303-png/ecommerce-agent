from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .ai_decisions import DecisionCitation, source_manifest_digest
from .evidence_contract import ProductIdentity
from .semantic_grounding import GroundedSource, GroundingCatalog, IMAGE_KIND, TEXT_KIND
from .source_bundle import normalize_key


PROFILE_CONTRACT_VERSION = 1
PROFILE_CACHE_VERSION = 1
PROFILE_SUPPORTED = "supported"
PROFILE_CONFLICT = "conflict"
PROFILE_STATUSES = (PROFILE_SUPPORTED, PROFILE_CONFLICT)


class ProductProfileError(ValueError):
    pass


class JSONTaskProvider(Protocol):
    name: str

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        ...


def _normalize_ws(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _citation_grounded(citation: DecisionCitation, source: GroundedSource) -> bool:
    if source.kind == IMAGE_KIND:
        return bool(citation.evidence_text.strip())
    if source.kind != TEXT_KIND:
        return False
    wanted = _normalize_ws(citation.evidence_text)
    return bool(wanted) and wanted in _normalize_ws(source.content)


@dataclass(slots=True, frozen=True)
class ProfileCandidate:
    value: str
    qualifier: str = ""
    citations: tuple[DecisionCitation, ...] = ()

    @classmethod
    def from_mapping(cls, payload: dict[str, Any], *, where: str) -> "ProfileCandidate":
        value = str(payload.get("value") or "").strip()
        qualifier = str(payload.get("qualifier") or "").strip()
        raw_citations = payload.get("citations") or []
        if not value:
            raise ProductProfileError(f"{where}.value 不能为空。")
        if not isinstance(raw_citations, list):
            raise ProductProfileError(f"{where}.citations 必须是数组。")
        citations = tuple(
            DecisionCitation.from_mapping(item, where=f"{where}.citations[{index}]")
            for index, item in enumerate(raw_citations, start=1)
            if isinstance(item, dict)
        )
        return cls(value=value, qualifier=qualifier, citations=citations)

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "qualifier": self.qualifier,
            "citations": [item.as_dict() for item in self.citations],
        }


@dataclass(slots=True, frozen=True)
class ProductFact:
    name: str
    scope: str
    status: str
    candidates: tuple[ProfileCandidate, ...]
    note: str = ""

    @classmethod
    def from_mapping(cls, payload: dict[str, Any], *, index: int) -> "ProductFact":
        name = str(payload.get("name") or "").strip()
        scope = str(payload.get("scope") or "product").strip() or "product"
        status = str(payload.get("status") or PROFILE_SUPPORTED).strip().casefold()
        raw_candidates = payload.get("candidates") or []
        if not name:
            raise ProductProfileError(f"facts[{index}].name 不能为空。")
        if status not in PROFILE_STATUSES:
            raise ProductProfileError(
                f"facts[{index}].status={status!r}；必须是 {PROFILE_STATUSES}。"
            )
        if not isinstance(raw_candidates, list):
            raise ProductProfileError(f"facts[{index}].candidates 必须是数组。")
        candidates = tuple(
            ProfileCandidate.from_mapping(item, where=f"facts[{index}].candidates[{candidate_index}]")
            for candidate_index, item in enumerate(raw_candidates, start=1)
            if isinstance(item, dict)
        )
        if not candidates:
            raise ProductProfileError(f"facts[{index}] 至少需要一个 candidate。")
        return cls(
            name=name,
            scope=scope,
            status=status,
            candidates=candidates,
            note=str(payload.get("note") or "").strip(),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "scope": self.scope,
            "status": self.status,
            "candidates": [item.as_dict() for item in self.candidates],
            "note": self.note,
        }


@dataclass(slots=True)
class ProductProfile:
    identity: ProductIdentity
    source_manifest_sha256: str
    facts: list[ProductFact]
    summary: str = ""
    warnings: list[str] = field(default_factory=list)
    extractor: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": PROFILE_CONTRACT_VERSION,
            "extractor": self.extractor,
            "product_identity": {
                "sku": self.identity.sku,
                "model_number": self.identity.model_number,
                "brand": self.identity.brand,
            },
            "source_manifest_sha256": self.source_manifest_sha256,
            "facts": [item.as_dict() for item in self.facts],
            "summary": self.summary,
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "ProductProfile":
        if not isinstance(payload, dict):
            raise ProductProfileError("product profile 顶层必须是 object。")
        version = int(payload.get("contract_version", PROFILE_CONTRACT_VERSION))
        if version != PROFILE_CONTRACT_VERSION:
            raise ProductProfileError(f"product profile contract_version={version} 不受支持。")
        raw_facts = payload.get("facts") or []
        raw_warnings = payload.get("warnings") or []
        if not isinstance(raw_facts, list) or not isinstance(raw_warnings, list):
            raise ProductProfileError("product profile facts/warnings 格式无效。")
        return cls(
            identity=ProductIdentity.from_mapping(payload.get("product_identity")),
            source_manifest_sha256=str(payload.get("source_manifest_sha256") or "").strip(),
            facts=[
                ProductFact.from_mapping(item, index=index)
                for index, item in enumerate(raw_facts, start=1)
                if isinstance(item, dict)
            ],
            summary=str(payload.get("summary") or "").strip(),
            warnings=[str(item) for item in raw_warnings],
            extractor=str(payload.get("extractor") or "").strip(),
        )


PROFILE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "status", "candidates"],
                "properties": {
                    "name": {"type": "string"},
                    "scope": {"type": "string"},
                    "status": {"type": "string", "enum": list(PROFILE_STATUSES)},
                    "candidates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["value", "citations"],
                            "properties": {
                                "value": {"type": "string"},
                                "qualifier": {"type": "string"},
                                "citations": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "required": ["source_reference", "evidence_text"],
                                        "properties": {
                                            "source_reference": {"type": "string"},
                                            "evidence_text": {"type": "string"},
                                        },
                                    },
                                },
                            },
                        },
                    },
                    "note": {"type": "string"},
                },
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["facts"],
}


PROFILE_SYSTEM_INSTRUCTION = (
    "You are the product-understanding stage of a marketplace listing pipeline. "
    "Read all local customer, supplier and image evidence jointly and build a compact factual product profile. "
    "Do not map facts to marketplace fields. Preserve source disagreements instead of choosing a winner. "
    "Return JSON only."
)

PROFILE_RULES = [
    "Extract only facts supported by the supplied sources; omit unknown facts entirely.",
    "A supported fact normally has one cited candidate. A real disagreement for the same attribute and scope must use status=conflict with at least two cited candidates.",
    "Keep scope explicit when it matters: selected variant, product body, packaging, manual/documentation, compatibility, seller/business, or another precise scope.",
    "Preserve the selected variant exactly. Never turn cabin/interior into rear/back, manual language into device UI language, packaging dimensions into product dimensions, or product brand into compatible vehicle brand.",
    "Never infer No/False/Not included from absence. Negative facts require explicit negative evidence.",
    "For text citations quote the smallest exact supporting excerpt. For images describe the exact visible evidence. Use only supplied source_id values.",
    "Prefer compact semantic facts over prose. Do not repeat the same fact under synonyms.",
    "Do not use web knowledge in this stage.",
]


def build_product_profile_request(
    grounding: GroundingCatalog,
    *,
    identity: ProductIdentity = ProductIdentity(),
) -> dict[str, Any]:
    return {
        "task": "understand_product_from_local_evidence",
        "system_instruction": PROFILE_SYSTEM_INSTRUCTION,
        "prompt_instruction": (
            "Read the complete local evidence once and return a compact product profile. "
            "Do not answer marketplace fields and do not explain your reasoning outside JSON."
        ),
        "product_identity": {
            "sku": identity.sku,
            "model_number": identity.model_number,
            "brand": identity.brand,
        },
        "target_fields": [],
        "rules": list(PROFILE_RULES),
        "grounded_sources": grounding.as_request_list(),
        "json_contract": PROFILE_JSON_SCHEMA,
    }


def _validate_profile(
    profile: ProductProfile,
    grounding: GroundingCatalog,
    *,
    expected_identity: ProductIdentity,
) -> ProductProfile:
    expected_source_digest = source_manifest_digest(grounding)
    warnings = list(profile.warnings)
    validated_facts: list[ProductFact] = []

    for fact in profile.facts:
        candidates: list[ProfileCandidate] = []
        seen_values: set[tuple[str, str]] = set()
        for candidate in fact.candidates:
            citations: list[DecisionCitation] = []
            seen_citations: set[tuple[str, str]] = set()
            for citation in candidate.citations:
                source = grounding.by_id(citation.source_reference)
                if source is None or not _citation_grounded(citation, source):
                    warnings.append(
                        f"profile fact {fact.name!r}: ungrounded citation {citation.source_reference!r} dropped"
                    )
                    continue
                fingerprint = (citation.source_reference, _normalize_ws(citation.evidence_text))
                if fingerprint in seen_citations:
                    continue
                seen_citations.add(fingerprint)
                citations.append(citation)
            if not citations:
                warnings.append(f"profile fact {fact.name!r}: uncited candidate dropped")
                continue
            value_key = (normalize_key(candidate.value), normalize_key(candidate.qualifier))
            if value_key in seen_values:
                continue
            seen_values.add(value_key)
            candidates.append(
                ProfileCandidate(
                    value=candidate.value,
                    qualifier=candidate.qualifier,
                    citations=tuple(citations),
                )
            )
        if not candidates:
            continue
        status = fact.status
        if status == PROFILE_CONFLICT and len(candidates) < 2:
            warnings.append(f"profile fact {fact.name!r}: malformed conflict reduced to supported")
            status = PROFILE_SUPPORTED
        validated_facts.append(
            ProductFact(
                name=fact.name,
                scope=fact.scope,
                status=status,
                candidates=tuple(candidates),
                note=fact.note,
            )
        )

    return ProductProfile(
        identity=expected_identity,
        source_manifest_sha256=expected_source_digest,
        facts=validated_facts,
        summary=profile.summary,
        warnings=warnings,
        extractor=profile.extractor,
    )


def profile_digest(profile: ProductProfile) -> str:
    raw = json.dumps(profile.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def profile_contract_digest() -> str:
    raw = json.dumps(
        {
            "version": PROFILE_CONTRACT_VERSION,
            "system": PROFILE_SYSTEM_INSTRUCTION,
            "rules": PROFILE_RULES,
            "schema": PROFILE_JSON_SCHEMA,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_key(
    provider: JSONTaskProvider,
    cache_namespace: str,
    grounding: GroundingCatalog,
    identity: ProductIdentity,
) -> str:
    payload = {
        "cache_version": PROFILE_CACHE_VERSION,
        "contract_sha256": profile_contract_digest(),
        "provider": provider.name,
        "cache_namespace": cache_namespace,
        "identity": {
            "sku": identity.sku,
            "model_number": identity.model_number,
            "brand": identity.brand,
        },
        "source_manifest_sha256": source_manifest_digest(grounding),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class ProductProfileRunResult:
    profile: ProductProfile
    model_calls: int
    cache_hit: bool
    elapsed_seconds: float


def run_product_profile(
    provider: JSONTaskProvider,
    grounding: GroundingCatalog,
    *,
    expected_identity: ProductIdentity = ProductIdentity(),
    cache_dir: str | Path | None = None,
    cache_namespace: str = "",
) -> ProductProfileRunResult:
    started = time.monotonic()
    cache_root = Path(cache_dir) if cache_dir is not None else None
    key = _cache_key(provider, cache_namespace, grounding, expected_identity)
    cache_path = cache_root / f"product-profile-{key}.json" if cache_root is not None else None

    if cache_path is not None and cache_path.is_file():
        try:
            cached = ProductProfile.from_mapping(json.loads(cache_path.read_text(encoding="utf-8")))
            if cached.source_manifest_sha256 == source_manifest_digest(grounding):
                validated = _validate_profile(cached, grounding, expected_identity=expected_identity)
                return ProductProfileRunResult(
                    profile=validated,
                    model_calls=0,
                    cache_hit=True,
                    elapsed_seconds=time.monotonic() - started,
                )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

    raw = provider.extract_json(
        build_product_profile_request(grounding, identity=expected_identity)
    )
    if not isinstance(raw, dict) or not isinstance(raw.get("facts"), list):
        raise ProductProfileError("product-understanding AI output 缺少 facts 数组。")
    candidate = ProductProfile(
        identity=expected_identity,
        source_manifest_sha256=source_manifest_digest(grounding),
        facts=[
            ProductFact.from_mapping(item, index=index)
            for index, item in enumerate(raw.get("facts") or [], start=1)
            if isinstance(item, dict)
        ],
        summary=str(raw.get("summary") or "").strip(),
        warnings=[],
        extractor=str(raw.get("extractor") or provider.name).strip() or provider.name,
    )
    validated = _validate_profile(candidate, grounding, expected_identity=expected_identity)

    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp = cache_path.with_suffix(cache_path.suffix + ".tmp")
        temp.write_text(json.dumps(validated.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(cache_path)

    return ProductProfileRunResult(
        profile=validated,
        model_calls=1,
        cache_hit=False,
        elapsed_seconds=time.monotonic() - started,
    )


def write_product_profile(profile: ProductProfile, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(profile.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return target
