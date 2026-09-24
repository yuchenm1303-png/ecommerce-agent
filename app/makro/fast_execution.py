from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.browser_visual_hud import browser_visual_hud_advice
from app.business_decisions import (
    is_user_decision_business_field,
    price_decision_advisory,
    user_decision_business_key,
)
from app.fill_plan import LiveFillPlan
from app.hard_field_validators import validate_resolved_answer
from app.makro.domain import MakroDomainAdapter
from app.makro.execution import (
    _cancel_open_section_transaction,
    _capture_diagnostic_screenshot,
    _collect_save_failure_diagnostics,
)
from app.makro.field_engine import (
    control_locator,
    execution_contract,
    fill_control,
    read_control,
    values_equivalent,
)
from app.makro_dryrun import FillVerification
from makro_preview_listing import (
    _base_result_payload,
    _item_identity,
    _open_and_index_section,
    _safe_name,
    _section_candidates,
    _verify_saved_values,
    execution_answer_for_item,
    preview_mode_for_item,
)


FAST_BATCH_SIZE = 8
_FAST_LIVE_FAMILIES = {"text", "long_text"}
_POLICY_BLANK_FIELDS = {
    "certification",
    "certifications",
    "ingredient",
    "ingredients",
}


@dataclass(slots=True)
class _PreparedCandidate:
    item: Any
    mode: str
    base_payload: dict[str, Any]
    identity: tuple[str, str, str]
    required: bool
    answer: Any
    section_path: str
    live_field: dict[str, Any]


@dataclass(slots=True)
class _FastPending:
    prepared: _PreparedCandidate
    constrained_answer: Any
    result_entry: dict[str, Any]
    immediate: FillVerification


def _normalized_field_name(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def _policy_blank_reason(item: Any) -> str | None:
    """Return an explicit seller policy for fields that must remain untouched.

    This is intentionally keyed only by the live field identity, not by Python
    product/category inference. Makro exposes Ingredients only on the verticals
    where that field exists, so the executor simply leaves it blank whenever the
    live schema presents it. The same rule applies to Certifications.
    """

    names = {
        _normalized_field_name(getattr(item, "attribute_key", "")),
        _normalized_field_name(getattr(item, "label", "")),
    }
    tokens = {name.replace(" ", "") for name in names if name}
    if tokens & _POLICY_BLANK_FIELDS:
        return "seller_policy_leave_blank"
    return None


def _value_controls(field: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        control
        for control in field.get("controls") or []
        if control.get("field_kind") != "option"
        and not str(control.get("name") or "").endswith("_qualifier")
    ]


def _answer_values(answer: Any) -> list[str]:
    values = [
        str(value).strip()
        for value in list(getattr(answer, "answer_values", []) or [])
        if str(value).strip()
    ]
    if values:
        return values
    scalar = str(getattr(answer, "answer", "") or "").strip()
    return [scalar] if scalar else []


def _price_decision_values(plan: LiveFillPlan) -> dict[str, str]:
    """Collect only explicit user-confirmed price values for HUD context."""

    output: dict[str, str] = {}
    for item in plan.items:
        key = user_decision_business_key(getattr(item, "attribute_key", ""))
        if not key:
            continue
        resolution = getattr(item, "resolution", None)
        if str(getattr(resolution, "source_type", "") or "").casefold() != "user":
            continue
        values = _answer_values(resolution)
        if values:
            output[key] = values[0]
    return output


def _show_user_decision_advice(
    adapter: MakroDomainAdapter,
    prepared: _PreparedCandidate,
    decision_values: dict[str, str] | None,
) -> None:
    """Best-effort HUD guidance; never changes execution outcome."""

    if not is_user_decision_business_field(prepared.live_field):
        return
    if str(getattr(prepared.answer, "source_type", "") or "").casefold() != "user":
        return

    controls = _value_controls(prepared.live_field)
    if len(controls) != 1:
        return

    key = user_decision_business_key(prepared.live_field)
    counterpart_key = (
        "flipkart_selling_price" if key == "mrp" else "mrp"
    )
    current_values = _answer_values(prepared.answer)
    advisory = price_decision_advisory(
        prepared.live_field,
        confirmed_value=current_values[0] if current_values else "",
        counterpart_value=(decision_values or {}).get(counterpart_key, ""),
    )
    if not advisory:
        return
    try:
        locator, _selector = control_locator(
            adapter.page,
            controls[0],
            prepared.section_path,
        )
        browser_visual_hud_advice(locator, advisory, phase=2)
    except Exception:
        # Visual guidance is deliberately non-authoritative.
        return


def _fast_batch_eligible(live_field: dict[str, Any], answer: Any) -> bool:
    """Admit only independent one-slot plain text controls to the fast lane.

    Dropdowns, radios, booleans, qualifiers, repeatable values and any other
    dynamic contract stay on the existing conservative per-field executor.
    """

    values = _answer_values(answer)
    if len(values) != 1:
        return False
    if str(getattr(answer, "qualifier", "") or "").strip():
        return False
    if bool(live_field.get("has_add_value_control") or live_field.get("multi_value")):
        return False
    controls = _value_controls(live_field)
    if len(controls) != 1:
        return False
    contract = execution_contract(live_field, answer)
    return bool(contract.supported) and contract.live_family in _FAST_LIVE_FAMILIES


def _stage_fast_text_field(
    adapter: MakroDomainAdapter,
    prepared: _PreparedCandidate,
) -> tuple[FillVerification, Any]:
    """Write one safe text field and perform only the immediate readback.

    The expensive React-settle readback is deliberately deferred to the whole
    batch. If immediate readback is already uncertain, the caller falls back to
    the mature conservative executor for this field only.
    """

    constrained = adapter._constrained_execution_answer(  # noqa: SLF001
        prepared.live_field,
        prepared.answer,
    )
    contract = execution_contract(prepared.live_field, constrained)
    values = _answer_values(constrained)
    controls = _value_controls(prepared.live_field)
    selectors: list[str] = []
    actual: list[str] = []

    hard_validation = validate_resolved_answer(prepared.live_field, constrained)
    if not hard_validation.valid:
        return (
            FillVerification(
                attribute_key=str(getattr(constrained, "attribute_key", "") or ""),
                label=str(getattr(constrained, "label", "") or ""),
                status="validation_failed",
                expected=values,
                detail=f"fast batch preflight rejected: {hard_validation.detail}",
                execution_family=contract.live_family,
            ),
            constrained,
        )

    if len(values) != 1 or len(controls) != 1:
        return (
            FillVerification(
                attribute_key=str(getattr(constrained, "attribute_key", "") or ""),
                label=str(getattr(constrained, "label", "") or ""),
                status="validation_failed",
                expected=values,
                detail="fast batch contract drifted away from one value / one control",
                execution_family=contract.live_family,
            ),
            constrained,
        )

    try:
        selector = fill_control(
            adapter.page,
            controls[0],
            values[0],
            section_path=prepared.section_path,
        )
        selectors.append(selector)
        value = read_control(
            adapter.page,
            controls[0],
            section_path=prepared.section_path,
            timeout_ms=3_000,
        )
        actual.append(value)
        passed = values_equivalent(controls[0], values[0], value)
        return (
            FillVerification(
                attribute_key=str(getattr(constrained, "attribute_key", "") or ""),
                label=str(getattr(constrained, "label", "") or ""),
                status="validated" if passed else "validation_failed",
                expected=values,
                actual=actual,
                selectors=selectors,
                detail=(
                    "fast batch staged: immediate readback matched; React settle verification deferred."
                    if passed
                    else "fast batch immediate readback did not match; falling back to safe executor."
                ),
                execution_family=contract.live_family,
            ),
            constrained,
        )
    except Exception as exc:
        return (
            FillVerification(
                attribute_key=str(getattr(constrained, "attribute_key", "") or ""),
                label=str(getattr(constrained, "label", "") or ""),
                status="fill_error",
                expected=values,
                actual=actual,
                selectors=selectors,
                detail=f"fast batch staging failed: {exc}",
                execution_family=contract.live_family,
            ),
            constrained,
        )


def _verify_fast_text_field(
    adapter: MakroDomainAdapter,
    live_field: dict[str, Any],
    constrained_answer: Any,
    *,
    section_path: str,
    prior_selectors: list[str],
) -> FillVerification:
    values = _answer_values(constrained_answer)
    controls = _value_controls(live_field)
    contract = execution_contract(live_field, constrained_answer)
    selectors = list(prior_selectors)
    actual: list[str] = []

    if not _fast_batch_eligible(live_field, constrained_answer):
        return FillVerification(
            attribute_key=str(getattr(constrained_answer, "attribute_key", "") or ""),
            label=str(getattr(constrained_answer, "label", "") or ""),
            status="validation_failed",
            expected=values,
            selectors=selectors,
            detail="fast batch live contract changed after React settle; safe fallback required.",
            execution_family=contract.live_family,
        )

    try:
        value = read_control(
            adapter.page,
            controls[0],
            section_path=section_path,
            timeout_ms=3_000,
        )
        actual.append(value)
        passed = values_equivalent(controls[0], values[0], value)
        return FillVerification(
            attribute_key=str(getattr(constrained_answer, "attribute_key", "") or ""),
            label=str(getattr(constrained_answer, "label", "") or ""),
            status="validated" if passed else "validation_failed",
            expected=values,
            actual=actual,
            selectors=selectors,
            detail=(
                "fast batch settled readback matched after the shared React wait."
                if passed
                else "fast batch settled readback changed after the shared React wait."
            ),
            execution_family=contract.live_family,
        )
    except Exception as exc:
        return FillVerification(
            attribute_key=str(getattr(constrained_answer, "attribute_key", "") or ""),
            label=str(getattr(constrained_answer, "label", "") or ""),
            status="validation_failed",
            expected=values,
            actual=actual,
            selectors=selectors,
            detail=f"fast batch settled readback failed: {exc}",
            execution_family=contract.live_family,
        )


def _prepare_candidate(
    adapter: MakroDomainAdapter,
    item: Any,
    section_title: str,
    *,
    include_review_candidates: bool,
    scroll_wait_ms: int,
    max_scroll_steps: int,
    report: dict[str, Any],
) -> tuple[_PreparedCandidate | None, bool]:
    mode = preview_mode_for_item(
        item,
        include_review_candidates=include_review_candidates,
    )
    base_payload = _base_result_payload(item, mode)
    identity = _item_identity(item)
    required = bool(getattr(item, "required", False))

    live_section = adapter.find_section(section_title)
    if live_section is None or live_section.get("has_edit"):
        report["validation_failed"] += 1
        report["stopped_on_field"] = item.label
        report["results"].append(
            {
                **base_payload,
                "execution_status": "section_transaction_lost",
                "detail": "字段写入前目标 section 已不存在或意外折叠；执行面已丢失，无法安全继续后续字段。",
            }
        )
        return None, True

    try:
        section_path, live = _open_and_index_section(
            adapter,
            section_title,
            wait_ms=scroll_wait_ms,
            max_scroll_steps=max_scroll_steps,
        )
    except Exception as exc:
        report["validation_failed"] += 1
        report["stopped_on_field"] = item.label
        report["results"].append(
            {
                **base_payload,
                "execution_status": "live_refresh_failed",
                "detail": f"字段写入前刷新当前 React live schema 失败，执行面不可用：{exc}",
            }
        )
        return None, True

    matches = live.get(identity, [])
    if len(matches) != 1:
        report["skipped_live_match"] += 1
        if not required:
            report["optional_prewrite_skipped"] += 1
            report["results"].append(
                {
                    **base_payload,
                    "execution_status": "optional_skipped_live_match",
                    "detail": (
                        f"可选字段写入前重新扫描后 live field 匹配数={len(matches)}，期望恰好 1；"
                        "该字段尚未发生任何写入，已局部跳过。"
                    ),
                }
            )
            return None, False
        report["validation_failed"] += 1
        report["results"].append(
            {
                **base_payload,
                "execution_status": "required_live_match_failed",
                "detail": (
                    f"必填字段写入前重新扫描后 live field 匹配数={len(matches)}，期望恰好 1；"
                    "仅该字段失败，继续处理同 section 的其他字段。"
                ),
            }
        )
        return None, False

    answer = execution_answer_for_item(
        item,
        include_review_candidates=include_review_candidates,
    )
    live_field = matches[0]
    hard_validation = validate_resolved_answer(live_field, answer)
    if not hard_validation.valid:
        if not required:
            report["optional_prewrite_skipped"] += 1
            report["results"].append(
                {
                    **base_payload,
                    "execution_status": "optional_preflight_rejected",
                    "detail": (
                        "可选字段与当前 live DOM 机械契约不兼容；尚未写入，已局部跳过："
                        f"{hard_validation.detail}"
                    ),
                }
            )
            return None, False
        report["validation_failed"] += 1
        report["results"].append(
            {
                **base_payload,
                "execution_status": "preflight_rejected",
                "detail": (
                    "当前必填 live field 与 Fill Plan 答案的机械契约不兼容；"
                    f"仅该字段未写入，继续处理其他字段：{hard_validation.detail}"
                ),
            }
        )
        return None, False

    return (
        _PreparedCandidate(
            item=item,
            mode=mode,
            base_payload=base_payload,
            identity=identity,
            required=required,
            answer=answer,
            section_path=section_path,
            live_field=live_field,
        ),
        False,
    )


def _record_final_verification(
    report: dict[str, Any],
    prepared: _PreparedCandidate,
    verification: FillVerification,
    *,
    result_entry: dict[str, Any] | None,
    validated_identities: set[tuple[str, str, str]],
    executed_candidates: list[Any],
    fast_batch: bool = False,
    safe_fallback: bool = False,
) -> None:
    if verification.status == "validated":
        report["validated"] += 1
        validated_identities.add(prepared.identity)
        executed_candidates.append(prepared.item)
        if fast_batch:
            report["fast_batch_verified"] += 1
    elif verification.status == "fill_error":
        report["fill_error"] += 1
    else:
        report["validation_failed"] += 1

    payload = result_entry if result_entry is not None else {**prepared.base_payload}
    payload.update(
        {
            "execution_status": verification.status,
            "verification": verification.as_dict(),
        }
    )
    if fast_batch:
        payload["fast_batch"] = True
    if safe_fallback:
        payload["fast_batch_safe_fallback"] = True
    if result_entry is None:
        report["results"].append(payload)


def _run_safe_prepared(
    adapter: MakroDomainAdapter,
    prepared: _PreparedCandidate,
    *,
    recheck_wait_ms: int,
    report: dict[str, Any],
    validated_identities: set[tuple[str, str, str]],
    executed_candidates: list[Any],
    count_write: bool = True,
    result_entry: dict[str, Any] | None = None,
    safe_fallback: bool = False,
    decision_values: dict[str, str] | None = None,
) -> None:
    if count_write:
        report["writes_attempted"] += 1
        if prepared.mode == "review":
            report["review_candidates_attempted"] += 1
    _show_user_decision_advice(adapter, prepared, decision_values)
    try:
        verification = adapter.fill_resolved_field(
            prepared.live_field,
            prepared.answer,
            section_path=prepared.section_path,
            recheck_wait_ms=recheck_wait_ms,
        )
    except Exception as exc:
        report["fill_error"] += 1
        payload = result_entry if result_entry is not None else {**prepared.base_payload}
        payload.update(
            {
                "execution_status": "fill_exception",
                "detail": f"该字段填写异常；保留当前页面状态并继续其他字段：{exc}",
            }
        )
        if safe_fallback:
            payload["fast_batch_safe_fallback"] = True
        if result_entry is None:
            report["results"].append(payload)
        return

    _record_final_verification(
        report,
        prepared,
        verification,
        result_entry=result_entry,
        validated_identities=validated_identities,
        executed_candidates=executed_candidates,
        fast_batch=False,
        safe_fallback=safe_fallback,
    )


def _mark_pending_unverified(
    pending: list[_FastPending],
    report: dict[str, Any],
    *,
    detail: str,
) -> None:
    for pending_item in pending:
        report["validation_failed"] += 1
        pending_item.result_entry.update(
            {
                "execution_status": "validation_failed",
                "fast_batch": True,
                "detail": detail,
            }
        )
    pending.clear()


def _flush_fast_batch(
    adapter: MakroDomainAdapter,
    pending: list[_FastPending],
    section_title: str,
    *,
    scroll_wait_ms: int,
    max_scroll_steps: int,
    recheck_wait_ms: int,
    report: dict[str, Any],
    validated_identities: set[tuple[str, str, str]],
    executed_candidates: list[Any],
) -> bool:
    """Settle one staged batch once, then verify every field on fresh live DOM.

    Returns ``True`` only when the section execution surface is still usable.
    Individual fields may still fall back to the conservative executor without
    turning sibling fields into failures.
    """

    if not pending:
        return True

    report["fast_batch_groups"] += 1
    if recheck_wait_ms > 0:
        adapter.page.wait_for_timeout(recheck_wait_ms)

    try:
        section_path, live = _open_and_index_section(
            adapter,
            section_title,
            wait_ms=scroll_wait_ms,
            max_scroll_steps=max_scroll_steps,
        )
    except Exception as exc:
        _mark_pending_unverified(
            pending,
            report,
            detail=f"fast batch React settle 后无法刷新 section live schema：{exc}",
        )
        return False

    for pending_item in list(pending):
        prepared = pending_item.prepared
        matches = live.get(prepared.identity, [])
        if len(matches) != 1:
            report["validation_failed"] += 1
            pending_item.result_entry.update(
                {
                    "execution_status": "validation_failed",
                    "fast_batch": True,
                    "detail": (
                        "fast batch settle 后字段无法唯一重新绑定；"
                        f"live field 匹配数={len(matches)}。"
                    ),
                }
            )
            continue

        fresh_field = matches[0]
        settled = _verify_fast_text_field(
            adapter,
            fresh_field,
            pending_item.constrained_answer,
            section_path=section_path,
            prior_selectors=list(pending_item.immediate.selectors),
        )
        if settled.status == "validated":
            fresh_prepared = _PreparedCandidate(
                item=prepared.item,
                mode=prepared.mode,
                base_payload=prepared.base_payload,
                identity=prepared.identity,
                required=prepared.required,
                answer=prepared.answer,
                section_path=section_path,
                live_field=fresh_field,
            )
            _record_final_verification(
                report,
                fresh_prepared,
                settled,
                result_entry=pending_item.result_entry,
                validated_identities=validated_identities,
                executed_candidates=executed_candidates,
                fast_batch=True,
            )
            continue

        report["fast_batch_fallbacks"] += 1
        fresh_prepared = _PreparedCandidate(
            item=prepared.item,
            mode=prepared.mode,
            base_payload=prepared.base_payload,
            identity=prepared.identity,
            required=prepared.required,
            answer=prepared.answer,
            section_path=section_path,
            live_field=fresh_field,
        )
        _run_safe_prepared(
            adapter,
            fresh_prepared,
            recheck_wait_ms=recheck_wait_ms,
            report=report,
            validated_identities=validated_identities,
            executed_candidates=executed_candidates,
            count_write=False,
            result_entry=pending_item.result_entry,
            safe_fallback=True,
        )

    pending.clear()
    return True


def fill_one_section(
    adapter: MakroDomainAdapter,
    plan: LiveFillPlan,
    section_title: str,
    *,
    include_review_candidates: bool,
    persist: bool,
    scroll_wait_ms: int,
    max_scroll_steps: int,
    recheck_wait_ms: int,
    run_dir: Path,
) -> dict[str, Any]:
    """Execute one Step 3 card with a bounded fast lane for stable text fields.

    Independent one-slot text/textarea fields are staged in live-schema order in
    fixed batches of at most ``FAST_BATCH_SIZE``. The batch pays one shared React
    settle wait, then every field is rebound and read back on fresh DOM. Any field
    that does not survive that settle automatically falls back to the existing
    conservative per-field executor. Dynamic controls never enter the fast lane.
    """

    candidates = _section_candidates(
        plan,
        section_title,
        include_review_candidates=include_review_candidates,
    )
    report: dict[str, Any] = {
        "section": section_title,
        "candidate_count": len(candidates),
        "writes_attempted": 0,
        "review_candidates_attempted": 0,
        "validated": 0,
        "validation_failed": 0,
        "fill_error": 0,
        "skipped_existing": 0,
        "skipped_live_match": 0,
        "optional_prewrite_skipped": 0,
        "policy_blank_skipped": 0,
        "fast_batch_staged": 0,
        "fast_batch_verified": 0,
        "fast_batch_groups": 0,
        "fast_batch_fallbacks": 0,
        "save_attempted": False,
        "saved": False,
        "persisted_verified": 0,
        "review_candidates_persisted": 0,
        "persisted_validation_failed": 0,
        "results": [],
        "persisted_verifications": [],
    }
    if not candidates:
        report["status"] = "no_candidates"
        return report

    decision_values = _price_decision_values(plan)

    try:
        _open_and_index_section(
            adapter,
            section_title,
            wait_ms=scroll_wait_ms,
            max_scroll_steps=max_scroll_steps,
        )
    except Exception as exc:
        report["status"] = "section_error"
        report["detail"] = str(exc)
        return report

    validated_identities: set[tuple[str, str, str]] = set()
    executed_candidates: list[Any] = []
    pending: list[_FastPending] = []
    structural_failure = False

    for item in candidates:
        blank_reason = _policy_blank_reason(item)
        if blank_reason:
            if pending and not _flush_fast_batch(
                adapter,
                pending,
                section_title,
                scroll_wait_ms=scroll_wait_ms,
                max_scroll_steps=max_scroll_steps,
                recheck_wait_ms=recheck_wait_ms,
                report=report,
                validated_identities=validated_identities,
                executed_candidates=executed_candidates,
            ):
                structural_failure = True
                break
            mode = preview_mode_for_item(
                item,
                include_review_candidates=include_review_candidates,
            )
            payload = _base_result_payload(item, mode)
            required = bool(getattr(item, "required", False))
            report["policy_blank_skipped"] += 1
            if required:
                report["validation_failed"] += 1
            report["results"].append(
                {
                    **payload,
                    "execution_status": (
                        "required_policy_blank" if required else "policy_blank_skipped"
                    ),
                    "detail": (
                        "Seller policy: Certifications and Ingredients remain blank when present in the live schema."
                    ),
                    "policy": blank_reason,
                }
            )
            continue

        prepared, structural = _prepare_candidate(
            adapter,
            item,
            section_title,
            include_review_candidates=include_review_candidates,
            scroll_wait_ms=scroll_wait_ms,
            max_scroll_steps=max_scroll_steps,
            report=report,
        )
        if structural:
            if pending:
                _mark_pending_unverified(
                    pending,
                    report,
                    detail="section execution surface was lost before the staged fast batch could be settled.",
                )
            structural_failure = True
            break
        if prepared is None:
            if pending and not _flush_fast_batch(
                adapter,
                pending,
                section_title,
                scroll_wait_ms=scroll_wait_ms,
                max_scroll_steps=max_scroll_steps,
                recheck_wait_ms=recheck_wait_ms,
                report=report,
                validated_identities=validated_identities,
                executed_candidates=executed_candidates,
            ):
                structural_failure = True
                break
            continue

        if _fast_batch_eligible(prepared.live_field, prepared.answer):
            report["writes_attempted"] += 1
            if prepared.mode == "review":
                report["review_candidates_attempted"] += 1
            report["fast_batch_staged"] += 1
            result_entry = {
                **prepared.base_payload,
                "execution_status": "fast_batch_staged",
                "fast_batch": True,
            }
            report["results"].append(result_entry)
            immediate, constrained = _stage_fast_text_field(adapter, prepared)
            if immediate.status == "validated":
                pending.append(
                    _FastPending(
                        prepared=prepared,
                        constrained_answer=constrained,
                        result_entry=result_entry,
                        immediate=immediate,
                    )
                )
                if len(pending) >= FAST_BATCH_SIZE and not _flush_fast_batch(
                    adapter,
                    pending,
                    section_title,
                    scroll_wait_ms=scroll_wait_ms,
                    max_scroll_steps=max_scroll_steps,
                    recheck_wait_ms=recheck_wait_ms,
                    report=report,
                    validated_identities=validated_identities,
                    executed_candidates=executed_candidates,
                ):
                    structural_failure = True
                    break
                continue

            report["fast_batch_fallbacks"] += 1
            _run_safe_prepared(
                adapter,
                prepared,
                recheck_wait_ms=recheck_wait_ms,
                report=report,
                validated_identities=validated_identities,
                executed_candidates=executed_candidates,
                count_write=False,
                result_entry=result_entry,
                safe_fallback=True,
            )
            continue

        if pending:
            if not _flush_fast_batch(
                adapter,
                pending,
                section_title,
                scroll_wait_ms=scroll_wait_ms,
                max_scroll_steps=max_scroll_steps,
                recheck_wait_ms=recheck_wait_ms,
                report=report,
                validated_identities=validated_identities,
                executed_candidates=executed_candidates,
            ):
                structural_failure = True
                break
            prepared, structural = _prepare_candidate(
                adapter,
                item,
                section_title,
                include_review_candidates=include_review_candidates,
                scroll_wait_ms=scroll_wait_ms,
                max_scroll_steps=max_scroll_steps,
                report=report,
            )
            if structural:
                structural_failure = True
                break
            if prepared is None:
                continue

        _run_safe_prepared(
            adapter,
            prepared,
            recheck_wait_ms=recheck_wait_ms,
            report=report,
            validated_identities=validated_identities,
            executed_candidates=executed_candidates,
            decision_values=decision_values,
        )

    if not structural_failure and pending:
        if not _flush_fast_batch(
            adapter,
            pending,
            section_title,
            scroll_wait_ms=scroll_wait_ms,
            max_scroll_steps=max_scroll_steps,
            recheck_wait_ms=recheck_wait_ms,
            report=report,
            validated_identities=validated_identities,
            executed_candidates=executed_candidates,
        ):
            structural_failure = True

    report["field_failures"] = int(report["validation_failed"]) + int(report["fill_error"])

    if structural_failure:
        failed_shot = run_dir / f"{_safe_name(section_title)}-execution-surface-lost.png"
        _capture_diagnostic_screenshot(
            adapter,
            failed_shot,
            report,
            "screenshot_execution_surface_lost",
        )
        report["status"] = "section_execution_lost"
        report["detail"] = (
            "section 本身的 live 执行面已丢失，无法继续或可靠 Save；"
            "执行器没有主动 Cancel，也没有回滚此前字段。"
        )
        return report

    if not executed_candidates:
        if report["writes_attempted"] == 0:
            try:
                if _cancel_open_section_transaction(adapter, section_title):
                    report["cancelled_no_write_transaction"] = True
            except Exception as cleanup_exc:
                report["cleanup_error"] = str(cleanup_exc)
        report["status"] = "no_validated_writes"
        report["detail"] = (
            "本 section 没有任何通过回读验证的写入，因此没有可安全持久化的字段；"
            "若曾发生写入尝试，当前页面状态保持原样，不做整栏回滚。"
        )
        return report

    before_save = run_dir / f"{_safe_name(section_title)}-before-save.png"
    _capture_diagnostic_screenshot(
        adapter,
        before_save,
        report,
        "screenshot_before_save",
    )

    if not persist:
        report["status"] = "preview_partial" if report["field_failures"] else "preview_open"
        return report

    report["save_attempted"] = True
    try:
        adapter.save_section(section_title)
        report["saved"] = True
        persisted, errors = _verify_saved_values(
            adapter,
            executed_candidates,
            validated_identities,
            section_title,
            include_review_candidates=include_review_candidates,
            wait_ms=scroll_wait_ms,
            max_scroll_steps=max_scroll_steps,
        )
        report["persisted_verifications"] = persisted
        report["persisted_verified"] = sum(
            1 for item in persisted if item.get("status") == "persisted_verified"
        )
        report["review_candidates_persisted"] = sum(
            1
            for item in persisted
            if item.get("status") == "persisted_verified"
            and item.get("preview_mode") == "review"
        )
        report["persisted_validation_failed"] = len(persisted) - report["persisted_verified"]
        report["post_save_errors"] = errors
    except Exception as exc:
        report["status"] = "save_failed"
        report["save_error"] = str(exc)
        diagnostics = _collect_save_failure_diagnostics(adapter, section_title)
        report["save_failure_diagnostics"] = diagnostics
        report["visible_errors_after_save_failure"] = diagnostics.get("visible_errors", [])
        report["detail"] = (
            "Makro section 在 Save 后没有恢复 EDIT，无法证明持久化；"
            "只保留现场诊断，不把该状态误报成已保存。"
        )
        failed_shot = run_dir / f"{_safe_name(section_title)}-save-failed.png"
        _capture_diagnostic_screenshot(
            adapter,
            failed_shot,
            report,
            "screenshot_save_failed",
        )
        return report

    if errors or report["persisted_validation_failed"]:
        report["status"] = "persisted_validation_failed"
        report["detail"] = (
            f"Makro 已持久化并复核 {report['persisted_verified']} 个成功字段；"
            "残余 validation error 只表示该 section 尚未完整，不再阻断后续 section。"
        )
    elif report["field_failures"]:
        report["status"] = "persisted_partial"
        report["detail"] = (
            f"已持久化并复核 {report['persisted_verified']} 个成功字段；"
            f"另有 {report['field_failures']} 个字段独立失败，没有触发整栏回滚。"
        )
    else:
        report["status"] = "persisted_verified"

    after_save = run_dir / f"{_safe_name(section_title)}-after-save-reopen.png"
    _capture_diagnostic_screenshot(
        adapter,
        after_save,
        report,
        "screenshot_after_save",
    )
    try:
        adapter.cancel_section(section_title)
    except Exception as cleanup_exc:
        report["post_save_cleanup_error"] = str(cleanup_exc)
    return report
