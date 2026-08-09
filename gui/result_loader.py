from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.ai_decisions import field_id


@dataclass(slots=True)
class FieldRow:
    field_id: str
    field_name: str
    ai_result: str
    ai_status: str
    final_status: str
    blocked_reason: str
    source: str


@dataclass(slots=True)
class WebCandidate:
    url: str
    title: str
    match: str
    reason: str
    identity_evidence: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PhaseStats:
    batch_count: int = 0
    model_calls: int = 0
    cache_hits: int = 0
    failed_batches: int = 0
    source_cache_hit: bool = False
    web_batch_count: int = 0
    web_model_calls: int = 0
    web_cache_hits: int = 0
    web_failed_batches: int = 0


@dataclass(slots=True)
class SafetyState:
    writes_performed: int = 0
    save_clicked: bool = False
    send_to_qc_clicked: bool = False

    @property
    def safe(self) -> bool:
        return (
            self.writes_performed == 0
            and not self.save_clicked
            and not self.send_to_qc_clicked
        )


@dataclass(slots=True)
class RunResult:
    run_dir: Path
    ready: int = 0
    missing: int = 0
    conflict: int = 0
    blocked: int = 0
    cold: PhaseStats = field(default_factory=PhaseStats)
    hot: PhaseStats = field(default_factory=PhaseStats)
    safety: SafetyState = field(default_factory=SafetyState)
    fields: list[FieldRow] = field(default_factory=list)
    web_candidates: list[WebCandidate] = field(default_factory=list)
    product_url: str = ""
    live_field_count: int = 0
    plan_summary: dict[str, Any] = field(default_factory=dict)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _latest_file(root: Path, pattern: str) -> Path | None:
    if not root.exists():
        return None
    matches = [path for path in root.glob(pattern) if path.is_file()]
    return max(matches, key=lambda path: path.stat().st_mtime_ns) if matches else None


def latest_live_schema(run_dir: Path) -> Path | None:
    return _latest_file(run_dir / "01-live-schema", "live-scan-*/live-schema.json")


def latest_resolver_manifest(run_dir: Path, phase_dir: str) -> Path | None:
    return _latest_file(run_dir / phase_dir, "resolve-ai-*/run-manifest.json")


def latest_fill_plan(run_dir: Path) -> Path | None:
    return _latest_file(run_dir / "04-fill-plan", "plan-*/fill-plan.json")


def latest_plan_manifest(run_dir: Path) -> Path | None:
    return _latest_file(run_dir / "04-fill-plan", "plan-*/manifest.json")


def latest_scan_manifest(run_dir: Path) -> Path | None:
    return _latest_file(run_dir / "01-live-schema", "live-scan-*/manifest.json")


def _path_from_manifest(manifest: dict[str, Any], dotted: str) -> Path | None:
    value: Any = manifest
    for key in dotted.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    text = str(value or "").strip()
    return Path(text) if text else None


def _phase_stats(manifest: dict[str, Any]) -> PhaseStats:
    local = manifest.get("local_fill") or {}
    web = manifest.get("web_fill") or {}
    source = manifest.get("source_capture") or {}
    return PhaseStats(
        batch_count=int(local.get("batch_count") or 0),
        model_calls=int(local.get("model_calls") or 0),
        cache_hits=int(local.get("cache_hits") or 0),
        failed_batches=int(local.get("failed_batches") or 0),
        source_cache_hit=bool(source.get("source_cache_hit")),
        web_batch_count=int(web.get("batch_count") or 0),
        web_model_calls=int(web.get("model_calls") or 0),
        web_cache_hits=int(web.get("cache_hits") or 0),
        web_failed_batches=int(web.get("failed_batches") or 0),
    )


def _safety_from_manifests(manifests: list[dict[str, Any]]) -> SafetyState:
    return SafetyState(
        writes_performed=sum(int(item.get("writes_performed") or 0) for item in manifests),
        save_clicked=any(bool(item.get("save_clicked")) for item in manifests),
        send_to_qc_clicked=any(bool(item.get("send_to_qc_clicked")) for item in manifests),
    )


def _decision_text(decision: dict[str, Any]) -> str:
    status = str(decision.get("status") or "").casefold()
    values = [str(value) for value in decision.get("values") or [] if str(value).strip()]
    qualifier = str(decision.get("qualifier") or "").strip()
    if status == "ready":
        text = " + ".join(values) if values else "—"
        return f"{text} {qualifier}".strip()
    if status == "conflict":
        alternatives: list[str] = []
        for alternative in decision.get("alternatives") or []:
            if not isinstance(alternative, dict):
                continue
            alt_values = [str(value) for value in alternative.get("values") or [] if str(value).strip()]
            alt_qualifier = str(alternative.get("qualifier") or "").strip()
            text = " + ".join(alt_values)
            alternatives.append(f"{text} {alt_qualifier}".strip())
        return " ↔ ".join(value for value in alternatives if value) or "CONFLICT"
    if status == "missing":
        return "MISSING"
    if status == "business_locked":
        return "BUSINESS LOCKED"
    if status == "review":
        text = " + ".join(values) if values else "REVIEW"
        return f"{text} {qualifier}".strip()
    return "—"


def _decision_sources(decision: dict[str, Any], web_by_ref: dict[str, str]) -> str:
    refs: list[str] = []
    for citation in decision.get("citations") or []:
        if isinstance(citation, dict):
            ref = str(citation.get("source_reference") or "").strip()
            if ref:
                refs.append(web_by_ref.get(ref, ref))
    for alternative in decision.get("alternatives") or []:
        if not isinstance(alternative, dict):
            continue
        for citation in alternative.get("citations") or []:
            if isinstance(citation, dict):
                ref = str(citation.get("source_reference") or "").strip()
                if ref:
                    refs.append(web_by_ref.get(ref, ref))
    unique: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            unique.append(ref)
    return " | ".join(unique)


def _web_candidates(run_dir: Path) -> list[WebCandidate]:
    cache_root = run_dir / "_cache" / "semantic"
    if not cache_root.exists():
        return []
    output: list[WebCandidate] = []
    seen: set[tuple[str, str]] = set()
    for path in sorted(cache_root.glob("web-product-research-*.json")):
        try:
            payload = _read_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        result_payload = payload.get("payload") or {}
        sources = {
            str(item.get("url") or "").rstrip("/"): item
            for item in payload.get("sources") or []
            if isinstance(item, dict) and str(item.get("url") or "").strip()
        }
        for item in result_payload.get("source_matches") or []:
            if not isinstance(item, dict):
                continue
            url = str(item.get("source_url") or "").strip()
            match = str(item.get("match") or "").strip().casefold()
            if not url or match not in {"same_product", "different_product", "uncertain"}:
                continue
            key = (url.rstrip("/"), match)
            if key in seen:
                continue
            seen.add(key)
            source = sources.get(url.rstrip("/"), {})
            output.append(
                WebCandidate(
                    url=url,
                    title=str(source.get("title") or "").strip(),
                    match=match,
                    reason=str(item.get("reason") or "").strip(),
                    identity_evidence=[
                        str(value).strip()
                        for value in item.get("identity_evidence") or []
                        if str(value).strip()
                    ],
                )
            )
    return output


def load_run_result(run_dir: str | Path) -> RunResult:
    root = Path(run_dir).resolve()
    result = RunResult(run_dir=root)

    cold_manifest_path = latest_resolver_manifest(root, "02-cold-resolver")
    hot_manifest_path = latest_resolver_manifest(root, "03-hot-resolver")
    plan_path = latest_fill_plan(root)
    plan_manifest_path = latest_plan_manifest(root)
    scan_manifest_path = latest_scan_manifest(root)
    live_schema_path = latest_live_schema(root)

    cold_manifest = _read_json(cold_manifest_path) if cold_manifest_path else {}
    hot_manifest = _read_json(hot_manifest_path) if hot_manifest_path else {}
    plan_manifest = _read_json(plan_manifest_path) if plan_manifest_path else {}
    scan_manifest = _read_json(scan_manifest_path) if scan_manifest_path else {}

    result.cold = _phase_stats(cold_manifest)
    result.hot = _phase_stats(hot_manifest)
    result.product_url = str(
        hot_manifest.get("primary_product_url")
        or cold_manifest.get("primary_product_url")
        or plan_manifest.get("product_url")
        or ""
    )

    safety_inputs = [item for item in (scan_manifest, cold_manifest, hot_manifest, plan_manifest) if item]
    result.safety = _safety_from_manifests(safety_inputs)

    decisions_payload: dict[str, Any] = {}
    decision_path = _path_from_manifest(hot_manifest, "outputs.final_decisions")
    if decision_path and decision_path.is_file():
        decisions_payload = _read_json(decision_path)
    elif hot_manifest_path:
        fallback = hot_manifest_path.parent / "ai-decisions.json"
        if fallback.is_file():
            decisions_payload = _read_json(fallback)

    decisions = [item for item in decisions_payload.get("decisions") or [] if isinstance(item, dict)]
    decisions_by_id = {str(item.get("field_id") or ""): item for item in decisions}
    result.missing = sum(str(item.get("status") or "").casefold() == "missing" for item in decisions)
    result.conflict = sum(str(item.get("status") or "").casefold() == "conflict" for item in decisions)

    web_by_ref = {
        str(item.get("source_reference") or ""): str(item.get("url") or "")
        for item in decisions_payload.get("web_sources") or []
        if isinstance(item, dict)
    }

    plan_payload = _read_json(plan_path) if plan_path else {}
    result.plan_summary = dict(plan_payload.get("summary") or {})
    result.ready = int(result.plan_summary.get("ready") or 0)
    result.blocked = int(result.plan_summary.get("blocked") or 0)

    plan_items = [item for item in plan_payload.get("items") or [] if isinstance(item, dict)]
    plan_by_key = {
        (
            str(item.get("attribute_key") or ""),
            str(item.get("label") or ""),
            str(item.get("section_heading") or ""),
        ): item
        for item in plan_items
    }

    live_fields: list[dict[str, Any]] = []
    if live_schema_path and live_schema_path.is_file():
        live_payload = json.loads(live_schema_path.read_text(encoding="utf-8"))
        if isinstance(live_payload, list):
            live_fields = [item for item in live_payload if isinstance(item, dict)]
        elif isinstance(live_payload, dict):
            raw = live_payload.get("fields") or live_payload.get("items") or []
            live_fields = [item for item in raw if isinstance(item, dict)]
    result.live_field_count = len(live_fields)

    for field in live_fields:
        identifier = field_id(field)
        decision = decisions_by_id.get(identifier, {})
        key = (
            str(field.get("attribute_key") or ""),
            str(field.get("label") or ""),
            str(field.get("section_heading") or ""),
        )
        plan_item = plan_by_key.get(key, {})
        resolution = plan_item.get("resolution") or {}
        action = str(plan_item.get("action") or "").casefold()
        ai_status = str(decision.get("status") or "").upper() or "—"
        final_status = "READY" if action == "ready" else "BLOCKED" if action == "blocked" else "—"
        blocked_reason = ""
        if final_status == "BLOCKED":
            gate = str(resolution.get("gate_reason") or "").strip()
            detail = str(resolution.get("detail") or plan_item.get("reason") or "").strip()
            blocked_reason = " · ".join(value for value in (gate, detail) if value)
        source = _decision_sources(decision, web_by_ref)
        if not source:
            source = str(resolution.get("source_reference") or "")

        result.fields.append(
            FieldRow(
                field_id=identifier,
                field_name=str(field.get("label") or field.get("attribute_key") or identifier),
                ai_result=_decision_text(decision),
                ai_status=ai_status,
                final_status=final_status,
                blocked_reason=blocked_reason,
                source=source,
            )
        )

    result.web_candidates = _web_candidates(root)
    return result
