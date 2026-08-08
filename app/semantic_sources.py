from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .evidence_contract import (
    EvidenceContractError,
    EvidencePacket,
    IdentityMismatchError,
    ProductIdentity,
    assert_identity_compatible,
)
from .evidence_validation import is_business_question
from .qa_catalog import QuestionCatalog, QuestionRecord
from .semantic_extraction import (
    SemanticExtractionProvider,
    build_grounded_semantic_request,
    validate_grounded_semantic_packet,
    validate_grounded_semantic_packet_partial,
)
from .semantic_grounding import GroundedSource, GroundingCatalog


SOURCE_CACHE_VERSION = 1
DEFAULT_MAX_REPAIR_ATTEMPTS = 1
ProgressCallback = Callable[[dict[str, Any]], None]


@dataclass(slots=True)
class SemanticSourceFailure:
    source_id: str
    source_references: tuple[str, ...]
    error: str


@dataclass(slots=True)
class SemanticSourceStat:
    source_id: str
    source_type: str
    kind: str
    source_references: tuple[str, ...]
    chunk_count: int
    cache_hit: bool = False
    model_calls: int = 0
    repair_attempts: int = 0
    fact_count: int = 0
    rejected_fact_count: int = 0
    elapsed_seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "kind": self.kind,
            "source_references": list(self.source_references),
            "chunk_count": self.chunk_count,
            "cache_hit": self.cache_hit,
            "model_calls": self.model_calls,
            "repair_attempts": self.repair_attempts,
            "fact_count": self.fact_count,
            "rejected_fact_count": self.rejected_fact_count,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }


@dataclass(slots=True)
class SemanticSourceRunResult:
    packet: EvidencePacket
    total_sources: int
    completed_sources: int
    failures: list[SemanticSourceFailure] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source_stats: list[SemanticSourceStat] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    @property
    def failed_sources(self) -> int:
        return len(self.failures)

    @property
    def partial(self) -> bool:
        return bool(self.failures)

    @property
    def model_calls(self) -> int:
        return sum(item.model_calls for item in self.source_stats)

    @property
    def cache_hits(self) -> int:
        return sum(1 for item in self.source_stats if item.cache_hit)


def build_semantic_pending_catalog(catalog: QuestionCatalog) -> QuestionCatalog:
    """Return only questions that semantic extraction is allowed to answer.

    Customer-provided answers are already grounded and should not consume model
    time. Seller-controlled business questions stay locked out regardless of
    source content.
    """

    pending = [
        question
        for question in catalog.questions
        if not question.has_answer and not is_business_question(question.question)
    ]
    return QuestionCatalog(
        source_path=catalog.source_path,
        sheet_name=catalog.sheet_name,
        header_row=catalog.header_row,
        questions=pending,
        preamble_text=catalog.preamble_text,
    )


def _merge_observed_identity(current: ProductIdentity, observed: ProductIdentity) -> ProductIdentity:
    assert_identity_compatible(current, observed)
    assert_identity_compatible(observed, current)
    return ProductIdentity(
        sku=current.sku or observed.sku,
        model_number=current.model_number or observed.model_number,
        brand=current.brand or observed.brand,
    )


def _question_cache_payload(question: QuestionRecord) -> dict[str, Any]:
    return {
        "number": question.number,
        "question": question.question,
        "explanation": question.explanation,
        "category": question.category,
        "options": list(question.options),
        "unit": question.unit,
        "extra": dict(sorted(question.extra.items())),
    }


def _source_digest(source: GroundedSource) -> str:
    if source.sha256:
        return source.sha256
    if source.content:
        return hashlib.sha256(source.content.encode("utf-8")).hexdigest()
    return hashlib.sha256(
        f"{source.source_id}\n{source.origin}\n{source.image_path}".encode("utf-8")
    ).hexdigest()


def _cache_key(
    provider: SemanticExtractionProvider,
    cache_namespace: str,
    catalog: QuestionCatalog,
    source_id: str,
    grounding: GroundingCatalog,
) -> str:
    payload = {
        "cache_version": SOURCE_CACHE_VERSION,
        "provider": provider.name,
        "cache_namespace": cache_namespace,
        "source_id": source_id,
        "sources": [
            {
                "source_id": source.source_id,
                "source_type": source.source_type,
                "kind": source.kind,
                "sha256": _source_digest(source),
            }
            for source in grounding.sources
        ],
        "questions": [_question_cache_payload(item) for item in catalog.questions],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _packet_mapping(packet: EvidencePacket) -> dict[str, Any]:
    return {
        "extractor": packet.extractor,
        "product_identity": {
            "sku": packet.identity.sku,
            "model_number": packet.identity.model_number,
            "brand": packet.identity.brand,
        },
        "facts": [
            {
                "key": fact.key,
                "aliases": list(fact.aliases),
                "value": list(fact.value) if isinstance(fact.value, tuple) else fact.value,
                "source_type": fact.source_type,
                "source_reference": fact.source_reference,
                "confidence": fact.confidence,
                "evidence_text": fact.evidence_text,
                "note": fact.note,
            }
            for fact in packet.facts
        ],
        "warnings": list(packet.warnings),
    }


def _cache_path(cache_dir: Path | None, key: str) -> Path | None:
    if cache_dir is None:
        return None
    return cache_dir / f"{key}.json"


def _load_cached_packet(
    path: Path,
    catalog: QuestionCatalog,
    grounding: GroundingCatalog,
    *,
    expected_identity: ProductIdentity,
) -> EvidencePacket:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("cache_version") != SOURCE_CACHE_VERSION:
        raise ValueError("semantic source cache version 不匹配。")
    packet_payload = payload.get("packet")
    if not isinstance(packet_payload, dict):
        raise ValueError("semantic source cache 缺少 packet。")
    return validate_grounded_semantic_packet(
        packet_payload,
        catalog,
        grounding,
        expected_identity=expected_identity,
    )


def _write_cached_packet(path: Path, packet: EvidencePacket) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cache_version": SOURCE_CACHE_VERSION,
        "packet": _packet_mapping(packet),
    }
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _emit(progress: ProgressCallback | None, payload: dict[str, Any]) -> None:
    if progress is not None:
        progress(payload)


def _group_metadata(group: GroundingCatalog) -> tuple[str, str]:
    source_types = sorted({item.source_type for item in group.sources})
    kinds = sorted({item.kind for item in group.sources})
    return ",".join(source_types), ",".join(kinds)


def run_grounded_semantic_sources(
    provider: SemanticExtractionProvider,
    catalog: QuestionCatalog,
    grounding: GroundingCatalog,
    *,
    expected_identity: ProductIdentity = ProductIdentity(),
    continue_on_source_error: bool = True,
    max_repair_attempts: int = DEFAULT_MAX_REPAIR_ATTEMPTS,
    cache_dir: str | Path | None = None,
    cache_namespace: str = "",
    progress: ProgressCallback | None = None,
) -> SemanticSourceRunResult:
    """Extract every logical grounded source at most once in the normal path.

    The old implementation multiplied calls by ``question_batch × source_chunk``.
    Production extraction is source-first instead: each image is seen once and
    all text chunks from one original page/file are sent together once, against
    the complete pending question set.

    A validated content-addressed cache makes identical retries zero-call. One
    optional repair is reserved for a source response whose candidates are all
    rejected by strict validation. If some facts validate, invalid siblings are
    dropped fail-closed and the source is *not* re-run.
    """

    if max_repair_attempts < 0 or max_repair_attempts > 1:
        raise ValueError("max_repair_attempts 必须是 0 或 1。")

    pending = build_semantic_pending_catalog(catalog)
    groups = grounding.logical_groups()
    total_sources = len(groups)
    start_all = time.monotonic()
    cache_root = Path(cache_dir) if cache_dir is not None else None

    all_facts = []
    warnings: list[str] = []
    failures: list[SemanticSourceFailure] = []
    stats: list[SemanticSourceStat] = []
    observed_identity = expected_identity
    completed = 0

    if not pending.questions:
        return SemanticSourceRunResult(
            packet=EvidencePacket(
                identity=observed_identity,
                facts=[],
                extractor=f"semantic-source-first:{provider.name}",
                warnings=[],
            ),
            total_sources=total_sources,
            completed_sources=0,
            elapsed_seconds=time.monotonic() - start_all,
        )

    for index, (source_id, group) in enumerate(groups, start=1):
        source_type, kind = _group_metadata(group)
        source_refs = tuple(item.source_id for item in group.sources)
        stat = SemanticSourceStat(
            source_id=source_id,
            source_type=source_type,
            kind=kind,
            source_references=source_refs,
            chunk_count=len(group.sources),
        )
        stats.append(stat)
        started = time.monotonic()
        _emit(
            progress,
            {
                "event": "source_start",
                "index": index,
                "total": total_sources,
                "source_id": source_id,
                "source_type": source_type,
                "kind": kind,
                "chunk_count": len(group.sources),
            },
        )

        key = _cache_key(provider, cache_namespace, pending, source_id, group)
        path = _cache_path(cache_root, key)
        packet: EvidencePacket | None = None
        rejected_count = 0

        if path is not None and path.is_file():
            try:
                packet = _load_cached_packet(
                    path,
                    pending,
                    group,
                    expected_identity=expected_identity,
                )
                stat.cache_hit = True
                _emit(
                    progress,
                    {
                        "event": "source_cache_hit",
                        "index": index,
                        "total": total_sources,
                        "source_id": source_id,
                    },
                )
            except (OSError, ValueError, EvidenceContractError, json.JSONDecodeError) as exc:
                warnings.append(f"semantic cache ignored for {source_id}: {exc}")

        failure_error = ""
        if packet is None:
            request = build_grounded_semantic_request(
                pending,
                group,
                identity=expected_identity,
            )
            # Keep one transport field for current providers/logs, but it now
            # identifies a source pass rather than a question batch.
            request["batch_id"] = f"source-{index:03d}:{source_id}"
            request["source_pass_id"] = source_id

            for attempt_index in range(max_repair_attempts + 1):
                if attempt_index:
                    stat.repair_attempts += 1
                stat.model_calls += 1
                try:
                    raw = provider.extract_json(request)
                    if not isinstance(raw, dict):
                        raise EvidenceContractError(
                            f"semantic provider {provider.name!r} 未返回 JSON object。"
                        )
                    validation = validate_grounded_semantic_packet_partial(
                        raw,
                        pending,
                        group,
                        expected_identity=expected_identity,
                    )
                except IdentityMismatchError:
                    raise
                except Exception as exc:
                    failure_error = str(exc)
                    break

                rejected_count = validation.rejected_fact_count
                if validation.packet.facts or rejected_count == 0:
                    packet = validation.packet
                    break

                failure_error = "; ".join(validation.rejected_facts)
                if attempt_index >= max_repair_attempts:
                    break
                request["validation_error"] = (
                    "All candidate facts from this source were rejected individually: "
                    + failure_error
                    + ". Return only facts you can fully ground; omit everything else."
                )

        if packet is None:
            stat.rejected_fact_count = rejected_count
            stat.elapsed_seconds = time.monotonic() - started
            failure = SemanticSourceFailure(
                source_id=source_id,
                source_references=source_refs,
                error=failure_error or "source extraction returned no valid packet",
            )
            failures.append(failure)
            _emit(
                progress,
                {
                    "event": "source_failed",
                    "index": index,
                    "total": total_sources,
                    "source_id": source_id,
                    "elapsed_seconds": stat.elapsed_seconds,
                    "error": failure.error,
                },
            )
            if not continue_on_source_error:
                raise EvidenceContractError(
                    f"semantic source {source_id} failed: {failure.error}"
                )
            continue

        if path is not None and not stat.cache_hit:
            _write_cached_packet(path, packet)

        observed_identity = _merge_observed_identity(observed_identity, packet.identity)
        completed += 1
        all_facts.extend(packet.facts)
        warnings.extend(packet.warnings)
        stat.fact_count = len(packet.facts)
        stat.rejected_fact_count = rejected_count
        stat.elapsed_seconds = time.monotonic() - started
        _emit(
            progress,
            {
                "event": "source_complete",
                "index": index,
                "total": total_sources,
                "source_id": source_id,
                "cache_hit": stat.cache_hit,
                "model_calls": stat.model_calls,
                "facts": stat.fact_count,
                "rejected_facts": stat.rejected_fact_count,
                "elapsed_seconds": stat.elapsed_seconds,
            },
        )

    if failures:
        warnings.extend(
            f"source {item.source_id} failed; its unanswered questions remain blocked: {item.error}"
            for item in failures
        )

    return SemanticSourceRunResult(
        packet=EvidencePacket(
            identity=observed_identity,
            facts=all_facts,
            extractor=f"semantic-source-first:{provider.name}",
            warnings=warnings,
        ),
        total_sources=total_sources,
        completed_sources=completed,
        failures=failures,
        warnings=warnings,
        source_stats=stats,
        elapsed_seconds=time.monotonic() - start_all,
    )
