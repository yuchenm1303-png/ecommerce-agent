"""Persist synthetic Makro section values so the whole listing can be inspected.

This module is intentionally test-only. It dynamically discovers the current
listing attributes, fills every current empty field, saves each core section so
Makro allows the next section to be edited, re-opens the saved section to verify
that the values persisted, then collapses it again. It never clicks Send to QC.
"""

from __future__ import annotations

import re
import time
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
    cancel_section,
    exercise_live_field,
    field_shape,
    semantic_field_is_empty,
    summarize_results,
)
from .direct_visual_hold import _listing_fields, _open_section
from .listing_preflight import CORE_FORM_SECTIONS
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


# Only global Makro business fields get save-safe synthetic overrides. Attribute
# counts and category attribute names remain fully dynamic.
_SAVE_SAFE_VALUES = {
    "listing_status": "Inactive",
    "mrp": "1000",
    "flipkart_selling_price": "900",
    "minimum_order_quantity": "1",
    "max_order_quantity_allowed": "5",
    "shipping_days": "2",
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
        # Deliberately short, alphanumeric and unique enough for a disposable test draft.
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
    """Normalize cross-field business values before clicking section Save."""

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

        # Selects can only use real live options. Fall back to the already-passed
        # synthetic value if the preferred save-safe option is unavailable.
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
        if not (_equivalent(candidate, immediate, control) and _equivalent(candidate, settled, control)):
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


def _visible_section_errors(adapter: Any, section_path: str) -> list[str]:
    card = adapter.page.locator(section_path)
    texts: list[str] = []
    selectors = ".form-error, [role='alert'], [class*='FormError']"
    try:
        for text in card.locator(selectors).all_inner_texts():
            clean = re.sub(r"\s+", " ", text).strip()
            if clean and clean not in texts:
                texts.append(clean)
    except Exception:
        pass
    return texts[:20]


def save_section(adapter: Any, section_title: str, *, timeout_s: float = 15.0) -> None:
    """Click the unique Save button inside one expanded section and prove collapse."""

    section = adapter.find_section(section_title)
    if section is None:
        raise RuntimeError(f"Save 前找不到 section：{section_title}")
    if section.get("has_edit"):
        raise RuntimeError(f"Save 前 section 已折叠：{section_title}")
    path = str(section.get("path") or "")
    if not path:
        raise RuntimeError(f"Save 前 section 缺少 DOM path：{section_title}")

    card = adapter.page.locator(path)
    save = card.locator("button").filter(has_text=re.compile(r"^\s*Save\s*$", re.I))
    if save.count() != 1 or not save.first.is_visible():
        raise RuntimeError(f"{section_title} 没有唯一可见 Save 按钮。")
    save.first.scroll_into_view_if_needed()
    save.first.click()

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        adapter.page.wait_for_timeout(250)
        live = adapter.find_section(section_title)
        if live is not None and live.get("has_edit"):
            return

    errors = _visible_section_errors(adapter, path)
    detail = " | ".join(errors) if errors else "未读取到可见 validation error"
    raise RuntimeError(f"{section_title} 点击 Save 后未恢复 EDIT：{detail}")


def _verify_saved_section(
    adapter: Any,
    section_title: str,
    results: list[CoverageResult],
    *,
    recheck_wait_ms: int,
    wait_ms: int,
    max_scroll_steps: int,
) -> None:
    """Reopen the saved section, prove persisted values, then collapse without edits."""

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
    cancel_section(adapter, section_title)


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

    # Re-discover after all writes, then normalize business values that have
    # cross-field save constraints (price order, MOQ range, inactive test status).
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

    save_section(adapter, section_title)
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
