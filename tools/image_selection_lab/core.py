from __future__ import annotations

import hashlib
import html
import json
import math
import os
import random
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol

from app.image_media import has_decodable_image_pixels
from app.listing_image_ranker import (
    _Candidate,
    _run_ownership_request,
    _run_ranking_request,
    build_listing_image_ownership_request,
    build_listing_image_ranking_request,
)
from app.providers.usage_telemetry import USAGE_JOURNAL_ENV, summarize_usage_journal


CASE_SCHEMA_VERSION = 1
RUN_SCHEMA_VERSION = 1
MAX_EVAL_IMAGES = 40

OWNERSHIP_CLASSES = frozenset(
    {
        "EXACT_TARGET",
        "TARGET_PACKAGING_OR_DETAIL",
        "SAME_PRODUCT_OTHER_VARIANT",
        "OTHER_PRODUCT",
        "PAGE_ASSET",
        "UNCERTAIN",
    }
)
AUTO_ALLOWED_CLASSES = frozenset({"EXACT_TARGET", "TARGET_PACKAGING_OR_DETAIL"})
WRONG_PRODUCT_CLASSES = frozenset({"OTHER_PRODUCT", "PAGE_ASSET", "UNCERTAIN"})


class ImageSelectionLabError(RuntimeError):
    pass


class SemanticProvider(Protocol):
    name: str

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass(slots=True, frozen=True)
class GroundTruth:
    ownership: str
    auto_upload_allowed: bool
    relevance_grade: int
    main_image_allowed: bool


@dataclass(slots=True, frozen=True)
class EvalCandidate:
    image_id: str
    file: Path
    truth: GroundTruth
    previous_prediction: dict[str, Any] | None = None


@dataclass(slots=True, frozen=True)
class EvalCase:
    case_id: str
    root: Path
    suites: tuple[str, ...]
    target_product: dict[str, Any]
    candidates: tuple[EvalCandidate, ...]


@dataclass(slots=True, frozen=True)
class StrategyOutput:
    strategy: str
    selected_ids: tuple[str, ...]
    ownership: dict[str, dict[str, Any]]
    gallery: dict[str, dict[str, Any]]
    transport_rejected: tuple[dict[str, Any], ...]
    semantic_calls: int
    status: str


@dataclass(slots=True, frozen=True)
class CaseMetrics:
    wrong_product_leak_count: int
    variant_leak_count: int
    disallowed_leak_count: int
    safe_gallery: bool
    all_negative_case: bool
    all_negative_pass: bool | None
    main_image_hit_at_1: bool | None
    selected_count: int
    allowed_selected_count: int
    allowed_total: int
    precision_at_5: float | None
    recall_at_5: float | None
    ndcg_at_5: float | None


@dataclass(slots=True, frozen=True)
class CaseRun:
    execution_id: str
    case_id: str
    repeat_index: int
    seed: int | None
    candidate_order: tuple[str, ...]
    output: StrategyOutput
    metrics: CaseMetrics
    network_calls: int
    cache_hits: int
    latency_seconds: float


@dataclass(slots=True, frozen=True)
class EvaluationRun:
    run_id: str
    strategy: str
    mode: str
    case_runs: tuple[CaseRun, ...]
    aggregate: dict[str, Any]
    usage: dict[str, Any]
    estimated_cost: dict[str, Any]
    run_dir: Path
    report_path: Path


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(payload: Any) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _require_dict(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ImageSelectionLabError(f"{name} must be an object")
    return dict(value)


def _parse_ground_truth(value: Any, *, case_id: str, image_id: str) -> GroundTruth:
    if not isinstance(value, dict):
        raise ImageSelectionLabError(
            f"case {case_id} candidate {image_id} is not labelled; ground_truth is required before evaluation"
        )
    ownership = str(value.get("ownership") or "").strip().upper()
    if ownership not in OWNERSHIP_CLASSES:
        raise ImageSelectionLabError(
            f"case {case_id} candidate {image_id} has invalid ownership={ownership!r}"
        )
    allowed = value.get("auto_upload_allowed")
    main_allowed = value.get("main_image_allowed")
    if type(allowed) is not bool or type(main_allowed) is not bool:
        raise ImageSelectionLabError(
            f"case {case_id} candidate {image_id} ground_truth booleans are required"
        )
    try:
        grade = int(value.get("relevance_grade"))
    except (TypeError, ValueError) as exc:
        raise ImageSelectionLabError(
            f"case {case_id} candidate {image_id} relevance_grade must be 0..3"
        ) from exc
    if grade not in {0, 1, 2, 3}:
        raise ImageSelectionLabError(
            f"case {case_id} candidate {image_id} relevance_grade must be 0..3"
        )
    if allowed != (ownership in AUTO_ALLOWED_CLASSES):
        raise ImageSelectionLabError(
            f"case {case_id} candidate {image_id} auto_upload_allowed disagrees with ownership"
        )
    if main_allowed and not allowed:
        raise ImageSelectionLabError(
            f"case {case_id} candidate {image_id} cannot be main-image-allowed while auto upload is forbidden"
        )
    return GroundTruth(
        ownership=ownership,
        auto_upload_allowed=allowed,
        relevance_grade=grade,
        main_image_allowed=main_allowed,
    )


def load_case(case_json: str | Path) -> EvalCase:
    path = Path(case_json).expanduser().resolve()
    payload = _require_dict(_read_json(path), name=str(path))
    if int(payload.get("schema_version") or 0) != CASE_SCHEMA_VERSION:
        raise ImageSelectionLabError(
            f"unsupported case schema_version={payload.get('schema_version')!r}: {path}"
        )
    case_id = str(payload.get("case_id") or "").strip()
    if not case_id:
        raise ImageSelectionLabError(f"case_id is required: {path}")
    target = _require_dict(payload.get("target_product"), name=f"{case_id}.target_product")
    if not target:
        raise ImageSelectionLabError(f"case {case_id} target_product cannot be empty")
    raw_candidates = payload.get("candidates")
    if not isinstance(raw_candidates, list) or not raw_candidates:
        raise ImageSelectionLabError(f"case {case_id} requires at least one candidate")
    if len(raw_candidates) > MAX_EVAL_IMAGES:
        raise ImageSelectionLabError(
            f"case {case_id} has {len(raw_candidates)} candidates; max={MAX_EVAL_IMAGES}"
        )

    candidates: list[EvalCandidate] = []
    seen_ids: set[str] = set()
    for raw in raw_candidates:
        if not isinstance(raw, dict):
            raise ImageSelectionLabError(f"case {case_id} candidate entries must be objects")
        image_id = str(raw.get("image_id") or "").strip()
        if not image_id or image_id in seen_ids:
            raise ImageSelectionLabError(f"case {case_id} has missing or duplicate image_id={image_id!r}")
        seen_ids.add(image_id)
        relative = str(raw.get("file") or "").strip()
        if not relative:
            raise ImageSelectionLabError(f"case {case_id} candidate {image_id} file is required")
        image_path = (path.parent / relative).resolve()
        if not image_path.is_file():
            raise ImageSelectionLabError(
                f"case {case_id} candidate {image_id} image does not exist: {image_path}"
            )
        truth = _parse_ground_truth(raw.get("ground_truth"), case_id=case_id, image_id=image_id)
        previous = raw.get("previous_prediction")
        candidates.append(
            EvalCandidate(
                image_id=image_id,
                file=image_path,
                truth=truth,
                previous_prediction=dict(previous) if isinstance(previous, dict) else None,
            )
        )

    suites_raw = payload.get("suite") or payload.get("suites") or ["all"]
    if isinstance(suites_raw, str):
        suites_raw = [suites_raw]
    suites = tuple(
        dict.fromkeys(str(item or "").strip() for item in suites_raw if str(item or "").strip())
    ) or ("all",)
    return EvalCase(
        case_id=case_id,
        root=path.parent,
        suites=suites,
        target_product=target,
        candidates=tuple(candidates),
    )


def discover_cases(cases_root: str | Path, *, suite: str = "all") -> list[EvalCase]:
    root = Path(cases_root).expanduser().resolve()
    if not root.is_dir():
        raise ImageSelectionLabError(f"cases directory does not exist: {root}")
    cases: list[EvalCase] = []
    for path in sorted(root.glob("*/case.json")):
        case = load_case(path)
        if suite == "all" or suite in case.suites or "all" in case.suites:
            cases.append(case)
    if not cases:
        raise ImageSelectionLabError(f"no labelled cases found for suite={suite!r} under {root}")
    return cases


def _safe_request_for_cache(request: dict[str, Any]) -> dict[str, Any]:
    payload = {key: value for key, value in request.items() if key != "grounded_sources"}
    safe_sources: list[dict[str, Any]] = []
    for source in request.get("grounded_sources") or []:
        if not isinstance(source, dict):
            continue
        safe_sources.append(
            {
                "source_id": source.get("source_id"),
                "source_type": source.get("source_type"),
                "kind": source.get("kind"),
                "sha256": source.get("sha256"),
                "source_index": source.get("source_index"),
            }
        )
    payload["grounded_sources"] = safe_sources
    return payload


class CachedProvider:
    """Cache semantic JSON responses without ever persisting credentials or local image paths."""

    def __init__(
        self,
        *,
        delegate: SemanticProvider | None,
        mode: str,
        cache_dir: Path,
        strategy: str,
        provider_identity: dict[str, Any],
        refresh_cache: bool = False,
    ) -> None:
        if mode not in {"live", "replay"}:
            raise ImageSelectionLabError("mode must be live or replay")
        self._delegate = delegate
        self.mode = mode
        self.cache_dir = cache_dir
        self.strategy = strategy
        self.provider_identity = dict(provider_identity)
        self.refresh_cache = bool(refresh_cache)
        self.semantic_calls = 0
        self.network_calls = 0
        self.cache_hits = 0
        self.network_latency_seconds = 0.0
        self.name = str(
            getattr(delegate, "name", None)
            or self.provider_identity.get("provider")
            or "replay-provider"
        )
        self.model = str(
            getattr(delegate, "model", None)
            or self.provider_identity.get("model")
            or "unknown"
        )

    def __getattr__(self, name: str) -> Any:
        if self._delegate is None:
            raise AttributeError(name)
        return getattr(self._delegate, name)

    def _cache_key(self, request_payload: dict[str, Any]) -> str:
        return _canonical_hash(
            {
                "cache_schema_version": 1,
                "strategy": self.strategy,
                "provider": self.provider_identity,
                "request": _safe_request_for_cache(request_payload),
            }
        )

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        self.semantic_calls += 1
        key = self._cache_key(request_payload)
        path = self.cache_dir / self.strategy / key[:2] / f"{key}.json"
        if path.is_file() and not self.refresh_cache:
            cached = _require_dict(_read_json(path), name=str(path))
            response = cached.get("response")
            if not isinstance(response, dict):
                raise ImageSelectionLabError(f"invalid cached response: {path}")
            self.cache_hits += 1
            return dict(response)
        if self.mode == "replay":
            raise ImageSelectionLabError(
                "replay cache miss; replay mode never performs an AI network request: " + key
            )
        if self._delegate is None:
            raise ImageSelectionLabError("live mode requires a configured semantic provider")

        started = time.monotonic()
        response = self._delegate.extract_json(request_payload)
        elapsed = max(0.0, time.monotonic() - started)
        self.network_calls += 1
        self.network_latency_seconds += elapsed
        _write_json(
            path,
            {
                "schema_version": 1,
                "cache_key": key,
                "strategy": self.strategy,
                "provider": self.provider_identity,
                "task": str(request_payload.get("task") or ""),
                "request_fingerprint": _canonical_hash(_safe_request_for_cache(request_payload)),
                "response": response,
            },
        )
        return dict(response)


class ImageSelectionStrategy(Protocol):
    name: str

    def run(self, case: EvalCase, provider: CachedProvider, candidate_order: tuple[str, ...]) -> StrategyOutput:
        ...


class CurrentV6Strategy:
    """Exact local parity adapter for the production v6 Ownership -> Gallery prompts/parsers."""

    name = "current-v6"

    @staticmethod
    def _production_candidates(
        case: EvalCase, candidate_order: tuple[str, ...]
    ) -> tuple[list[_Candidate], list[dict[str, Any]]]:
        by_id = {candidate.image_id: candidate for candidate in case.candidates}
        candidates: list[_Candidate] = []
        rejected: list[dict[str, Any]] = []
        seen_hashes: set[str] = set()
        for source_index, image_id in enumerate(candidate_order, start=1):
            candidate = by_id[image_id]
            if not has_decodable_image_pixels(candidate.file):
                rejected.append(
                    {"image_id": image_id, "path": str(candidate.file), "reason": "not_decodable"}
                )
                continue
            digest = _sha256_file(candidate.file)
            if digest in seen_hashes:
                rejected.append(
                    {
                        "image_id": image_id,
                        "path": str(candidate.file),
                        "reason": "exact_duplicate",
                        "sha256": digest,
                    }
                )
                continue
            seen_hashes.add(digest)
            candidates.append(
                _Candidate(
                    image_id=image_id,
                    path=candidate.file,
                    sha256=digest,
                    source_index=source_index,
                )
            )
        return candidates, rejected

    def run(
        self,
        case: EvalCase,
        provider: CachedProvider,
        candidate_order: tuple[str, ...],
    ) -> StrategyOutput:
        candidates, transport_rejected = self._production_candidates(case, candidate_order)
        if not candidates:
            return StrategyOutput(
                strategy=self.name,
                selected_ids=(),
                ownership={},
                gallery={},
                transport_rejected=tuple(transport_rejected),
                semantic_calls=0,
                status="no_transport_candidates",
            )

        ownership_request = build_listing_image_ownership_request(
            product_context=case.target_product,
            candidates=candidates,
        )
        ownership = _run_ownership_request(provider, ownership_request, candidates)
        ownership_json = {
            image_id: decision.as_dict() for image_id, decision in ownership.decisions.items()
        }
        eligible = [
            candidate for candidate in candidates if candidate.image_id in set(ownership.eligible_ids)
        ]
        if not eligible:
            return StrategyOutput(
                strategy=self.name,
                selected_ids=(),
                ownership=ownership_json,
                gallery={},
                transport_rejected=tuple(transport_rejected),
                semantic_calls=1,
                status="ai_ownership_empty",
            )

        ranking_request = build_listing_image_ranking_request(
            product_context=case.target_product,
            candidates=eligible,
            ownership=ownership,
        )
        ranking = _run_ranking_request(provider, ranking_request, eligible)
        gallery_json = {
            image_id: decision.as_dict() for image_id, decision in ranking.decisions.items()
        }
        return StrategyOutput(
            strategy=self.name,
            selected_ids=ranking.selected_ids,
            ownership=ownership_json,
            gallery=gallery_json,
            transport_rejected=tuple(transport_rejected),
            semantic_calls=2,
            status="ai_ranked" if ranking.selected_ids else "ai_ranked_empty",
        )


class QwenVLRerankV1Strategy:
    """Reserved experiment slot. Deliberately not wired to production until the benchmark justifies it."""

    name = "qwen-vl-rerank-v1"

    def run(
        self,
        case: EvalCase,
        provider: CachedProvider,
        candidate_order: tuple[str, ...],
    ) -> StrategyOutput:
        raise ImageSelectionLabError(
            "qwen-vl-rerank-v1 is intentionally reserved but not implemented yet; "
            "first establish a labelled current-v6 baseline"
        )


STRATEGIES: dict[str, ImageSelectionStrategy] = {
    CurrentV6Strategy.name: CurrentV6Strategy(),
    QwenVLRerankV1Strategy.name: QwenVLRerankV1Strategy(),
}


def _dcg(grades: Iterable[int]) -> float:
    return sum((2**grade - 1) / math.log2(index + 2) for index, grade in enumerate(grades))


def score_case(case: EvalCase, output: StrategyOutput) -> CaseMetrics:
    truth = {candidate.image_id: candidate.truth for candidate in case.candidates}
    selected = list(output.selected_ids[:5])
    unknown = [image_id for image_id in selected if image_id not in truth]
    if unknown:
        raise ImageSelectionLabError(
            f"strategy {output.strategy} selected unknown ids in case {case.case_id}: {unknown}"
        )

    wrong = sum(1 for image_id in selected if truth[image_id].ownership in WRONG_PRODUCT_CLASSES)
    variants = sum(
        1
        for image_id in selected
        if truth[image_id].ownership == "SAME_PRODUCT_OTHER_VARIANT"
    )
    disallowed = sum(1 for image_id in selected if not truth[image_id].auto_upload_allowed)
    allowed_selected = sum(1 for image_id in selected if truth[image_id].auto_upload_allowed)
    allowed_total = sum(1 for item in truth.values() if item.auto_upload_allowed)
    all_negative = allowed_total == 0

    precision = allowed_selected / len(selected) if selected else None
    recall = allowed_selected / allowed_total if allowed_total else None
    selected_grades = [truth[image_id].relevance_grade for image_id in selected]
    ideal_grades = sorted(
        (item.relevance_grade for item in truth.values() if item.auto_upload_allowed), reverse=True
    )[:5]
    ideal_dcg = _dcg(ideal_grades)
    ndcg = (_dcg(selected_grades) / ideal_dcg) if ideal_dcg > 0 else None

    if allowed_total == 0:
        main_hit = None
    elif not selected:
        main_hit = False
    else:
        main_hit = truth[selected[0]].main_image_allowed

    return CaseMetrics(
        wrong_product_leak_count=wrong,
        variant_leak_count=variants,
        disallowed_leak_count=disallowed,
        safe_gallery=disallowed == 0,
        all_negative_case=all_negative,
        all_negative_pass=(len(selected) == 0) if all_negative else None,
        main_image_hit_at_1=main_hit,
        selected_count=len(selected),
        allowed_selected_count=allowed_selected,
        allowed_total=allowed_total,
        precision_at_5=precision,
        recall_at_5=recall,
        ndcg_at_5=ndcg,
    )


def _mean(values: Iterable[float | None]) -> float | None:
    actual = [float(value) for value in values if value is not None]
    return (sum(actual) / len(actual)) if actual else None


def _selection_stability(case_runs: list[CaseRun]) -> dict[str, Any]:
    by_case: dict[str, list[CaseRun]] = {}
    for run in case_runs:
        by_case.setdefault(run.case_id, []).append(run)
    jaccards: list[float] = []
    stable_top1: list[bool] = []
    for runs in by_case.values():
        if len(runs) < 2:
            continue
        for left_index in range(len(runs)):
            for right_index in range(left_index + 1, len(runs)):
                left = set(runs[left_index].output.selected_ids)
                right = set(runs[right_index].output.selected_ids)
                union = left | right
                jaccards.append(len(left & right) / len(union) if union else 1.0)
        top1 = [run.output.selected_ids[0] if run.output.selected_ids else "" for run in runs]
        stable_top1.append(len(set(top1)) == 1)
    return {
        "selection_jaccard": _mean(jaccards),
        "top1_stability_rate": (
            sum(1 for value in stable_top1 if value) / len(stable_top1) if stable_top1 else None
        ),
    }


def aggregate_metrics(case_runs: list[CaseRun]) -> dict[str, Any]:
    metrics = [run.metrics for run in case_runs]
    selected_total = sum(item.selected_count for item in metrics)
    allowed_selected_total = sum(item.allowed_selected_count for item in metrics)
    allowed_total = sum(item.allowed_total for item in metrics)
    negative = [item for item in metrics if item.all_negative_case]
    main = [item.main_image_hit_at_1 for item in metrics if item.main_image_hit_at_1 is not None]
    safe_count = sum(1 for item in metrics if item.safe_gallery)
    stability = _selection_stability(case_runs)
    return {
        "executions": len(metrics),
        "unique_cases": len({run.case_id for run in case_runs}),
        "wrong_product_leak_count": sum(item.wrong_product_leak_count for item in metrics),
        "wrong_product_leak_rate": (
            sum(item.wrong_product_leak_count for item in metrics) / selected_total
            if selected_total
            else 0.0
        ),
        "variant_leak_count": sum(item.variant_leak_count for item in metrics),
        "variant_leak_rate": (
            sum(item.variant_leak_count for item in metrics) / selected_total if selected_total else 0.0
        ),
        "disallowed_leak_count": sum(item.disallowed_leak_count for item in metrics),
        "safe_gallery_rate": safe_count / len(metrics) if metrics else 0.0,
        "all_negative_case_pass_rate": (
            sum(1 for item in negative if item.all_negative_pass) / len(negative) if negative else None
        ),
        "main_image_hit_at_1": (
            sum(1 for value in main if value) / len(main) if main else None
        ),
        "precision_at_5_micro": (
            allowed_selected_total / selected_total if selected_total else None
        ),
        "recall_at_5_micro": allowed_selected_total / allowed_total if allowed_total else None,
        "ndcg_at_5_mean": _mean(item.ndcg_at_5 for item in metrics),
        "semantic_calls": sum(run.output.semantic_calls for run in case_runs),
        "network_calls": sum(run.network_calls for run in case_runs),
        "cache_hits": sum(run.cache_hits for run in case_runs),
        "latency_seconds": round(sum(run.latency_seconds for run in case_runs), 3),
        **stability,
        "safety_gate_pass": (
            all(item.safe_gallery for item in metrics)
            and all(item.all_negative_pass is not False for item in metrics)
        ),
    }


def _estimate_cost(
    usage: dict[str, Any], *, input_price_per_million: float, output_price_per_million: float
) -> dict[str, Any]:
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    if input_price_per_million <= 0 and output_price_per_million <= 0:
        return {
            "currency": "CNY",
            "available": False,
            "reason": "pricing_not_supplied",
            "amount": None,
        }
    amount = (
        input_tokens * input_price_per_million / 1_000_000
        + output_tokens * output_price_per_million / 1_000_000
    )
    return {
        "currency": "CNY",
        "available": True,
        "input_price_per_million": input_price_per_million,
        "output_price_per_million": output_price_per_million,
        "amount": round(amount, 6),
    }


def _format_metric(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, bool):
        return "PASS" if value else "FAIL"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def write_html_report(
    *,
    run_dir: Path,
    strategy: str,
    mode: str,
    case_runs: list[CaseRun],
    cases: dict[str, EvalCase],
    aggregate: dict[str, Any],
    usage: dict[str, Any],
    estimated_cost: dict[str, Any],
) -> Path:
    rows: list[str] = []
    for run in case_runs:
        case = cases[run.case_id]
        selected_position = {
            image_id: index + 1 for index, image_id in enumerate(run.output.selected_ids)
        }
        ownership = run.output.ownership
        gallery = run.output.gallery
        cards: list[str] = []
        for candidate in case.candidates:
            truth = candidate.truth
            own = ownership.get(candidate.image_id) or {}
            gal = gallery.get(candidate.image_id) or {}
            position = selected_position.get(candidate.image_id)
            selected_label = f"SELECTED #{position}" if position else "not selected"
            bad = bool(position and not truth.auto_upload_allowed)
            cards.append(
                "<article class='image-card " + ("bad" if bad else "") + "'>"
                f"<img src='{html.escape(candidate.file.as_uri())}' loading='lazy'>"
                f"<h4>{html.escape(candidate.image_id)} · {html.escape(selected_label)}</h4>"
                f"<div><b>GT</b> {html.escape(truth.ownership)} · grade={truth.relevance_grade}</div>"
                f"<div><b>AI ownership</b> {html.escape(str(own.get('classification') or 'N/A'))}</div>"
                f"<div><b>AI confidence</b> {html.escape(str(own.get('confidence') if own else 'N/A'))}</div>"
                f"<div><b>Gallery</b> {html.escape(str(gal.get('selected') if gal else 'N/A'))}</div>"
                f"<div class='reason'>{html.escape(str(gal.get('reason') or own.get('reason') or ''))}</div>"
                "</article>"
            )
        metric_text = " · ".join(
            [
                f"safe={run.metrics.safe_gallery}",
                f"wrong={run.metrics.wrong_product_leak_count}",
                f"variant={run.metrics.variant_leak_count}",
                f"P@5={_format_metric(run.metrics.precision_at_5)}",
                f"R@5={_format_metric(run.metrics.recall_at_5)}",
                f"NDCG@5={_format_metric(run.metrics.ndcg_at_5)}",
                f"network={run.network_calls}",
                f"cache={run.cache_hits}",
            ]
        )
        rows.append(
            f"<section><h2>{html.escape(run.execution_id)}</h2>"
            f"<pre>{html.escape(json.dumps(case.target_product, ensure_ascii=False, indent=2))}</pre>"
            f"<p>{html.escape(metric_text)}</p><div class='grid'>{''.join(cards)}</div></section>"
        )

    summary_items = "".join(
        f"<tr><td>{html.escape(str(key))}</td><td>{html.escape(_format_metric(value))}</td></tr>"
        for key, value in aggregate.items()
    )
    usage_items = "".join(
        f"<tr><td>{html.escape(str(key))}</td><td>{html.escape(_format_metric(value))}</td></tr>"
        for key, value in usage.items()
        if key not in {"by_stage", "by_model", "by_task"}
    )
    cost_text = html.escape(json.dumps(estimated_cost, ensure_ascii=False))
    document = f"""<!doctype html>
<html><head><meta charset='utf-8'><title>Image Selection Lab</title>
<style>
body{{font-family:system-ui,-apple-system,Segoe UI,sans-serif;margin:24px;background:#f6f7f9;color:#171717}}
h1,h2{{margin-bottom:8px}} table{{border-collapse:collapse;background:white}}td{{padding:6px 10px;border:1px solid #ddd}}
section{{margin:28px 0;padding:18px;background:white;border-radius:12px}}pre{{white-space:pre-wrap;background:#f3f4f6;padding:10px;border-radius:8px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:12px}}
.image-card{{border:1px solid #ddd;border-radius:10px;padding:10px;background:#fff}}.image-card.bad{{border:3px solid #b00020}}
.image-card img{{width:100%;height:180px;object-fit:contain;background:#fafafa;border-radius:6px}}.image-card h4{{margin:8px 0 6px}}
.reason{{font-size:12px;color:#555;margin-top:6px;word-break:break-word}}
</style></head><body>
<h1>Image Selection Lab</h1><p>strategy={html.escape(strategy)} · mode={html.escape(mode)}</p>
<h2>Aggregate</h2><table>{summary_items}</table>
<h2>AI usage</h2><table>{usage_items}</table><p>Estimated cost: {cost_text}</p>
{''.join(rows)}
</body></html>"""
    target = run_dir / "report.html"
    target.write_text(document, encoding="utf-8")
    return target


def run_evaluation(
    *,
    cases: list[EvalCase],
    strategy_name: str,
    mode: str,
    delegate_provider: SemanticProvider | None,
    provider_identity: dict[str, Any],
    cache_dir: str | Path,
    runs_dir: str | Path,
    repeat: int = 1,
    shuffle: bool = False,
    seed: int = 17,
    refresh_cache: bool = False,
    input_price_per_million: float = 0.0,
    output_price_per_million: float = 0.0,
) -> EvaluationRun:
    if strategy_name not in STRATEGIES:
        raise ImageSelectionLabError(
            f"unknown strategy={strategy_name!r}; available={','.join(sorted(STRATEGIES))}"
        )
    if repeat < 1:
        raise ImageSelectionLabError("repeat must be >= 1")
    strategy = STRATEGIES[strategy_name]
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{now}_{strategy_name}_{mode}"
    run_dir = Path(runs_dir).expanduser().resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    cache_provider = CachedProvider(
        delegate=delegate_provider,
        mode=mode,
        cache_dir=Path(cache_dir).expanduser().resolve(),
        strategy=strategy_name,
        provider_identity=provider_identity,
        refresh_cache=refresh_cache,
    )

    previous_journal = os.environ.get(USAGE_JOURNAL_ENV)
    journal = run_dir / "ai-usage.jsonl"
    os.environ[USAGE_JOURNAL_ENV] = str(journal)
    case_runs: list[CaseRun] = []
    try:
        for case in cases:
            base_order = [candidate.image_id for candidate in case.candidates]
            for repeat_index in range(repeat):
                run_seed = seed + repeat_index if shuffle else None
                order = list(base_order)
                if shuffle:
                    random.Random(run_seed).shuffle(order)
                before_network = cache_provider.network_calls
                before_hits = cache_provider.cache_hits
                started = time.monotonic()
                output = strategy.run(case, cache_provider, tuple(order))
                elapsed = max(0.0, time.monotonic() - started)
                metrics = score_case(case, output)
                case_runs.append(
                    CaseRun(
                        execution_id=(
                            case.case_id
                            if repeat == 1
                            else f"{case.case_id}#r{repeat_index + 1}"
                        ),
                        case_id=case.case_id,
                        repeat_index=repeat_index,
                        seed=run_seed,
                        candidate_order=tuple(order),
                        output=output,
                        metrics=metrics,
                        network_calls=cache_provider.network_calls - before_network,
                        cache_hits=cache_provider.cache_hits - before_hits,
                        latency_seconds=round(elapsed, 3),
                    )
                )
    finally:
        if previous_journal is None:
            os.environ.pop(USAGE_JOURNAL_ENV, None)
        else:
            os.environ[USAGE_JOURNAL_ENV] = previous_journal

    aggregate = aggregate_metrics(case_runs)
    usage = summarize_usage_journal(journal)
    estimated_cost = _estimate_cost(
        usage,
        input_price_per_million=input_price_per_million,
        output_price_per_million=output_price_per_million,
    )
    cases_by_id = {case.case_id: case for case in cases}
    report = write_html_report(
        run_dir=run_dir,
        strategy=strategy_name,
        mode=mode,
        case_runs=case_runs,
        cases=cases_by_id,
        aggregate=aggregate,
        usage=usage,
        estimated_cost=estimated_cost,
    )
    payload = {
        "schema_version": RUN_SCHEMA_VERSION,
        "run_id": run_id,
        "strategy": strategy_name,
        "mode": mode,
        "provider": provider_identity,
        "aggregate": aggregate,
        "usage": usage,
        "estimated_cost": estimated_cost,
        "case_runs": [
            {
                "execution_id": run.execution_id,
                "case_id": run.case_id,
                "repeat_index": run.repeat_index,
                "seed": run.seed,
                "candidate_order": list(run.candidate_order),
                "output": {
                    **asdict(run.output),
                    "selected_ids": list(run.output.selected_ids),
                    "transport_rejected": list(run.output.transport_rejected),
                },
                "metrics": asdict(run.metrics),
                "network_calls": run.network_calls,
                "cache_hits": run.cache_hits,
                "latency_seconds": run.latency_seconds,
            }
            for run in case_runs
        ],
    }
    _write_json(run_dir / "run.json", payload)
    return EvaluationRun(
        run_id=run_id,
        strategy=strategy_name,
        mode=mode,
        case_runs=tuple(case_runs),
        aggregate=aggregate,
        usage=usage,
        estimated_cost=estimated_cost,
        run_dir=run_dir,
        report_path=report,
    )


def import_case_from_selection(
    *,
    selection_json: str | Path,
    cases_root: str | Path,
    case_id: str,
) -> Path:
    source = Path(selection_json).expanduser().resolve()
    payload = _require_dict(_read_json(source), name=str(source))
    target = payload.get("target_product_identity")
    if not isinstance(target, dict) or not target:
        raise ImageSelectionLabError("selection report has no target_product_identity")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ImageSelectionLabError("selection report has no candidates")

    destination = Path(cases_root).expanduser().resolve() / case_id
    if destination.exists():
        raise ImageSelectionLabError(f"case destination already exists: {destination}")
    images_dir = destination / "images"
    images_dir.mkdir(parents=True, exist_ok=False)
    case_candidates: list[dict[str, Any]] = []
    try:
        for index, raw in enumerate(candidates, start=1):
            if not isinstance(raw, dict):
                continue
            old_path = Path(str(raw.get("path") or "")).expanduser().resolve()
            if not old_path.is_file():
                raise ImageSelectionLabError(f"candidate image missing during import: {old_path}")
            suffix = old_path.suffix.lower() or ".jpg"
            image_id = f"img_{index:03d}"
            new_path = images_dir / f"{image_id}{suffix}"
            shutil.copy2(old_path, new_path)
            old_gallery = raw.get("gallery") if isinstance(raw.get("gallery"), dict) else {}
            old_ownership = raw.get("ownership") if isinstance(raw.get("ownership"), dict) else {}
            case_candidates.append(
                {
                    "image_id": image_id,
                    "file": f"images/{new_path.name}",
                    "ground_truth": None,
                    "previous_prediction": {
                        "source_image_id": raw.get("image_id"),
                        "ownership": old_ownership,
                        "gallery": old_gallery,
                        "selected": raw.get("image_id") in set(payload.get("selected_ids") or []),
                    },
                }
            )
        case_payload = {
            "schema_version": CASE_SCHEMA_VERSION,
            "case_id": case_id,
            "suite": ["regression"],
            "label_status": "needs_review",
            "target_product": target,
            "imported_from": source.name,
            "candidates": case_candidates,
        }
        case_path = destination / "case.json"
        _write_json(case_path, case_payload)
        return case_path
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


__all__ = [
    "AUTO_ALLOWED_CLASSES",
    "CASE_SCHEMA_VERSION",
    "CachedProvider",
    "CurrentV6Strategy",
    "EvalCase",
    "EvaluationRun",
    "GroundTruth",
    "ImageSelectionLabError",
    "QwenVLRerankV1Strategy",
    "STRATEGIES",
    "aggregate_metrics",
    "discover_cases",
    "import_case_from_selection",
    "load_case",
    "run_evaluation",
    "score_case",
]
