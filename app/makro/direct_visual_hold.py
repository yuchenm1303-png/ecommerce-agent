"""Direct whole-listing synthetic fill for human inspection.

Unlike the cleanup-oriented coverage runners, this module intentionally leaves
synthetic values in the current Makro draft so a human can inspect the rendered
result. It never clicks Save, Send to QC, or Cancel.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from .coverage import (
    FAIL,
    PASS,
    SKIPPED_EXISTING,
    CoverageResult,
    exercise_live_field,
    field_shape,
    semantic_field_is_empty,
    summarize_results,
)
from .listing_preflight import CORE_FORM_SECTIONS
from .visual_hold import _verify_final_hold

ProgressCallback = Callable[[str, CoverageResult, int, int], None]

_INDEXED_ATTRIBUTE_NAME_RE = re.compile(r"^.+_\d+_(?:value|qualifier)$")


def is_listing_attribute_field(field: dict[str, Any]) -> bool:
    """Return True only for real Makro listing attributes, not helper controls.

    Makro listing attributes expose a stable id and/or indexed attribute name
    such as ``sku_id_0_value``. Helper UI controls such as the "Search for SKU
    ID" copy-from-old-SKU search box have neither and must never be treated as a
    product/listing field.
    """

    for control in field.get("controls") or []:
        cid = str(control.get("id") or "").strip()
        name = str(control.get("name") or "").strip()
        if cid:
            return True
        if _INDEXED_ATTRIBUTE_NAME_RE.match(name):
            return True
    return False


def _listing_fields(adapter: Any, section_path: str, *, wait_ms: int, max_scroll_steps: int) -> list[dict[str, Any]]:
    controls = adapter.scan_section_fields(
        section_path,
        include_values=True,
        wait_ms=wait_ms,
        max_scroll_steps=max_scroll_steps,
    )
    fields = adapter.build_semantic_fields(controls)
    return [field for field in fields if is_listing_attribute_field(field)]


def _open_section(adapter: Any, section_title: str) -> dict[str, Any]:
    section = adapter.find_section(section_title)
    if section is None:
        raise RuntimeError(f"当前页面找不到 section：{section_title}")
    if section.get("has_edit"):
        adapter.open_section_for_edit(section)
        section = adapter.find_section(section_title) or section
    if section.get("has_edit"):
        raise RuntimeError(f"点击 EDIT 后 section 仍未展开：{section_title}")
    if not section.get("path"):
        raise RuntimeError(f"section 缺少 DOM path：{section_title}")
    return section


def _assert_clean_start(adapter: Any) -> None:
    bad: list[str] = []
    for title in CORE_FORM_SECTIONS:
        section = adapter.find_section(title)
        if section is None:
            bad.append(f"{title}: not found")
        elif not section.get("has_edit"):
            bad.append(f"{title}: already expanded")
    if bad:
        raise RuntimeError(
            "开始前要求三个核心 section 都处于折叠 EDIT 状态，避免覆盖未保存人工编辑：\n- "
            + "\n- ".join(bad)
        )


def fill_all_current_empty_attributes(
    adapter: Any,
    *,
    sections: tuple[str, ...] = CORE_FORM_SECTIONS,
    recheck_wait_ms: int = 800,
    wait_ms: int = 250,
    max_scroll_steps: int = 200,
    on_result: ProgressCallback | None = None,
) -> dict[str, list[CoverageResult]]:
    """Fill every currently empty real listing attribute and leave values intact.

    Counts are runtime-derived. Makro title counters such as ``0/14`` are treated
    as display/completion metadata only and are never used as a field-count gate.
    Existing non-placeholder values are skipped and never overwritten.
    """

    _assert_clean_start(adapter)
    results_by_section: dict[str, list[CoverageResult]] = {}

    for section_title in sections:
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
                    detail="当前字段已有非 placeholder 值；inspection hold 不覆盖已有数据。",
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

        adapter.page.wait_for_timeout(recheck_wait_ms)
        _verify_final_hold(
            adapter,
            section_title,
            section_path,
            results,
            wait_ms=wait_ms,
            max_scroll_steps=max_scroll_steps,
        )
        results_by_section[section_title] = results

    # Final cross-section re-read. If Makro auto-collapses earlier cards while a
    # later card opens, reopen them one at a time and prove their synthetic values
    # survived the transitions. Do not Cancel any section afterward.
    for section_title in sections:
        section = _open_section(adapter, section_title)
        section_path = str(section["path"])
        adapter.page.wait_for_timeout(recheck_wait_ms)
        _verify_final_hold(
            adapter,
            section_title,
            section_path,
            results_by_section[section_title],
            wait_ms=wait_ms,
            max_scroll_steps=max_scroll_steps,
        )

    return results_by_section


def summarize_direct_hold(results_by_section: dict[str, list[CoverageResult]]) -> dict[str, Any]:
    sections: dict[str, Any] = {}
    total_discovered = 0
    total_empty_attempts = 0
    total_passed = 0
    total_existing = 0
    total_failed = 0

    for title, results in results_by_section.items():
        summary = summarize_results(results)
        discovered = len(results)
        sections[title] = {
            "discovered_listing_attributes": discovered,
            "empty_targets": summary["empty_field_attempts"],
            "passed": summary["passed"],
            "failed_or_unsupported": summary["failed_or_unsupported"],
            "existing_skipped": summary["skipped_existing"],
            "by_shape": summary["by_shape"],
        }
        total_discovered += discovered
        total_empty_attempts += summary["empty_field_attempts"]
        total_passed += summary["passed"]
        total_existing += summary["skipped_existing"]
        total_failed += summary["failed_or_unsupported"]

    return {
        "sections": sections,
        "total_discovered_listing_attributes": total_discovered,
        "total_empty_targets": total_empty_attempts,
        "total_passed": total_passed,
        "total_existing_skipped": total_existing,
        "total_failed_or_unsupported": total_failed,
        "all_empty_targets_passed": total_empty_attempts > 0 and total_failed == 0,
    }
