"""Persist synthetic Makro section values so the whole listing can be inspected.

This module remains test-only for synthetic data generation. The actual
section lifecycle (Save/Cancel/error detection) is shared with production code
through ``app.makro.sections`` so there is only one persistence implementation.
It never clicks Send to QC.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from .coverage import (
    PASS,
    SKIPPED_EXISTING,
    CoverageResult,
    _equivalent,
    _match_field,
    _read_control,
    _unique_visible_locator,
    _value_controls,
    _write_control,
    exercise_live_field,
    field_shape,
    semantic_field_is_empty,
    summarize_results,
)
from .direct_visual_hold import _listing_fields, _open_section
from .listing_preflight import CORE_FORM_SECTIONS
from .sections import cancel_section, save_section, visible_section_errors
from .visual_hold import _verify_final_hold

ProgressCallback = Callable[[str, CoverageResult, int, int], None]


@dataclass(slots=True)
class PersistedSectionResult:
    section: str
    results: list[CoverageResult]
    saved: bool
    persisted_verified: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "section": self.section,
            "summary": summarize_results(self.results),
            "saved": self.saved,
            "persisted_verified": self.persisted_verified,
            "results": [item.as_dict() for item in self.results],
        }


def _gtin_check_digit(body: str) -> str:
    if not body or not body.isdigit():
        raise ValueError("GTIN body 必须是纯数字。")
    total = 0
    for index, digit in enumerate(reversed(body), start=1):
        total += int(digit) * (3 if index % 2 == 1 else 1)
    return str((10 - (total % 10)) % 10)


def _gtin_from_body(body: str) -> str:
    return body + _gtin_check_digit(body)


_SAVE_SAFE_VALUES = {
    "listing_status": "Inactive",
    "mrp": "1000",
    "flipkart_selling_price": "900",
    "minimum_order_quantity": "1",
    "max_order_quantity_allowed": "5",
    "shipping_days": "2",
    "ean": _gtin_from_body("200000000001"),
}


def _assert_clean_start(adapter: Any) -> None:
    bad: list[str] = []
    for title in CORE_FORM_SECTIONS:
        section = adapter.find_section(title)
        if section is None:
            bad.append(f"{title}: not found")
        elif not section.get("has_edit"):
            bad.append(f"{title}: expanded / unsaved")
    if bad:
        raise RuntimeError(
            "one-shot 开始前三个核心 section 必须全部处于 EDIT 折叠态：\n- "
            + "\n- ".join(bad)
        )


def _save_candidate_for(key: str, run_token: str) -> str | None:
    if key == "sku_id":
        safe = re.sub(r"[^A-Za-z0-9]", "", run_token)[-10:] or "TEST000001"
        return f"COV{safe}"
    return _SAVE_SAFE_VALUES.get(key)


def _apply_save_safe_overrides(
    adapter: Any,
    section_path: str,
    fields: list[dict[str, Any]],
    results: list[CoverageResult],
    *,
    run_token: str,
    recheck_wait_ms: int,
) -> None:
    """Normalize synthetic values that have format/cross-field constraints."""

    result_by_key = {
        item.attribute_key: item for item in results if item.status == PASS
    }
    for key, result in result_by_key.items():
        candidate = _save_candidate_for(key, run_token)
        if candidate is None:
            continue
        field = _match_field(fields, key, result.label)
        if field is None:
            continue
        values = _value_controls(field)
        if not values:
            continue
        control = values[0]
        locator, selector = _unique_visible_locator(adapter.page, section_path, control)

        if str(control.get("field_kind") or "") == "select":
            options = [
                str(option.get("text") or option.get("value") or "").strip()
                for option in control.get("options") or []
                if not option.get("disabled")
            ]
            if candidate not in options:
                continue

        _write_control(adapter.page, locator, control, candidate)
        immediate = _read_control(locator, control)
        adapter.page.wait_for_timeout(recheck_wait_ms)
        locator2, _ = _unique_visible_locator(adapter.page, section_path, control)
        settled = _read_control(locator2, control)
        if not (
            _equivalent(candidate, immediate, control)
            and _equivalent(candidate, settled, control)
        ):
            raise RuntimeError(
                f"save-safe override 回读失败：{key} expected={candidate!r}, "
                f"immediate={immediate!r}, settled={settled!r}"
            )
        if result.candidate:
            result.candidate[0] = candidate
        else:
            result.candidate.append(candidate)
        if result.immediate:
            result.immediate[0] = immediate
        else:
            result.immediate.append(immediate)
        if result.settled:
            result.settled[0] = settled
        else:
            result.settled.append(settled)
        if selector not in result.selectors:
            result.selectors.append(selector)
        result.detail = "已替换为 save-safe synthetic 值并完成稳定回读。"


def _verify_saved_section(
    adapter: Any,
    section_title: str,
    results: list[CoverageResult],
    *,
    recheck_wait_ms: int,
    wait_ms: int,
    max_scroll_steps: int,
) -> None:
    """Reopen saved section, prove persisted values, then collapse read-only state."""

    section = _open_section(adapter, section_title)
    section_path = str(section["path"])
    adapter.page.wait_for_timeout(recheck_wait_ms)
    _verify_final_hold(
        adapter,
        section_title,
        section_path,
        results,
        wait_ms=wait_ms,
        max_scroll_steps=max_scroll_steps,
    )
    failed = [item for item in results if item.status not in {PASS, SKIPPED_EXISTING}]
    if failed:
        raise RuntimeError(
            f"{section_title} Save 后重新打开复核失败："
            + ", ".join(item.label or item.attribute_key for item in failed[:10])
        )
    inline_errors = visible_section_errors(adapter.page, section_path)
    if inline_errors:
        raise RuntimeError(
            f"{section_title} Save 后重新打开仍有 Makro validation error："
            + " | ".join(inline_errors)
        )
    cancel_section(adapter.page, section_title)


def fill_save_and_verify_section(
    adapter: Any,
    section_title: str,
    *,
    run_token: str,
    recheck_wait_ms: int = 800,
    wait_ms: int = 250,
    max_scroll_steps: int = 200,
    on_result: ProgressCallback | None = None,
) -> PersistedSectionResult:
    section = _open_section(adapter, section_title)
    section_path = str(section["path"])
    fields = _listing_fields(
        adapter,
        section_path,
        wait_ms=wait_ms,
        max_scroll_steps=max_scroll_steps,
    )

    targets = [field for field in fields if semantic_field_is_empty(field)]
    existing = [field for field in fields if not semantic_field_is_empty(field)]
    results: list[CoverageResult] = []

    for field in existing:
        results.append(
            CoverageResult(
                section=str(field.get("section_heading") or section_title),
                subsection=str(field.get("subsection_heading") or ""),
                attribute_key=str(field.get("attribute_key") or ""),
                label=str(field.get("label") or ""),
                shape=field_shape(field),
                status=SKIPPED_EXISTING,
                detail="当前字段已有值；one-shot 不覆盖。",
            )
        )

    total = len(targets)
    for ordinal, field in enumerate(targets, start=1):
        result = exercise_live_field(
            adapter,
            field,
            section_path,
            ordinal,
            recheck_wait_ms=recheck_wait_ms,
            exercise_multi_value=False,
            wait_ms=wait_ms,
            max_scroll_steps=max_scroll_steps,
        )
        results.append(result)
        if on_result is not None:
            on_result(section_title, result, ordinal, total)

    live_fields = _listing_fields(
        adapter,
        section_path,
        wait_ms=wait_ms,
        max_scroll_steps=max_scroll_steps,
    )
    _apply_save_safe_overrides(
        adapter,
        section_path,
        live_fields,
        results,
        run_token=run_token,
        recheck_wait_ms=recheck_wait_ms,
    )

    adapter.page.wait_for_timeout(recheck_wait_ms)
    _verify_final_hold(
        adapter,
        section_title,
        section_path,
        results,
        wait_ms=wait_ms,
        max_scroll_steps=max_scroll_steps,
    )
    summary = summarize_results(results)
    if summary["failed_or_unsupported"]:
        raise RuntimeError(
            f"{section_title} 填写复核未全通过："
            f"{summary['passed']}/{summary['empty_field_attempts']}"
        )

    save_section(adapter.page, section_title)
    _verify_saved_section(
        adapter,
        section_title,
        results,
        recheck_wait_ms=recheck_wait_ms,
        wait_ms=wait_ms,
        max_scroll_steps=max_scroll_steps,
    )
    return PersistedSectionResult(
        section=section_title,
        results=results,
        saved=True,
        persisted_verified=True,
    )


def run_one_shot_persisted_inspection(
    adapter: Any,
    *,
    run_token: str,
    recheck_wait_ms: int = 800,
    wait_ms: int = 250,
    max_scroll_steps: int = 200,
    on_result: ProgressCallback | None = None,
) -> list[PersistedSectionResult]:
    """Fill + Save + persisted-readback all three core sections without user input."""

    _assert_clean_start(adapter)
    output: list[PersistedSectionResult] = []
    for title in CORE_FORM_SECTIONS:
        result = fill_save_and_verify_section(
            adapter,
            title,
            run_token=run_token,
            recheck_wait_ms=recheck_wait_ms,
            wait_ms=wait_ms,
            max_scroll_steps=max_scroll_steps,
            on_result=on_result,
        )
        output.append(result)
    return output
