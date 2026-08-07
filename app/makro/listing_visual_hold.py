"""Whole-listing visual hold for Makro core form sections.

This module opens the three core listing sections, fills every currently empty
semantic field with synthetic test values, proves all successful values still
coexist after the final React render, and leaves all three sections expanded for
human inspection. It never clicks Save or Send to QC.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from .coverage import PASS, CoverageResult, summarize_results
from .listing_preflight import CORE_FORM_SECTIONS
from .visual_hold import (
    _verify_final_hold,
    cleanup_visual_hold_section,
    fill_section_for_visual_hold,
)

ListingProgressCallback = Callable[[str, CoverageResult, int, int], None]


def _assert_sections_collapsed(adapter: Any, sections: Iterable[str]) -> None:
    bad: list[str] = []
    for title in sections:
        section = adapter.find_section(title)
        if section is None:
            bad.append(f"{title}: not found")
        elif not section.get("has_edit"):
            bad.append(f"{title}: already expanded")
    if bad:
        raise RuntimeError(
            "whole-listing visual hold 开始前三个 section 必须全部折叠：\n- "
            + "\n- ".join(bad)
        )


def cleanup_all_visual_hold_sections(
    adapter: Any,
    sections: Iterable[str] = CORE_FORM_SECTIONS,
) -> dict[str, bool]:
    """Cancel every currently expanded core section in reverse order."""

    ordered = list(sections)
    cleaned: dict[str, bool] = {}
    errors: list[str] = []
    for title in reversed(ordered):
        try:
            section = adapter.find_section(title)
            if section is None:
                errors.append(f"{title}: not found")
                continue
            if section.get("has_edit"):
                cleaned[title] = False
                continue
            cleaned[title] = cleanup_visual_hold_section(adapter, title)
        except Exception as exc:  # pragma: no cover - real-site safety path
            errors.append(f"{title}: {exc}")
    if errors:
        raise RuntimeError("visual-hold cleanup failed: " + "; ".join(errors))
    return cleaned


def fill_all_core_sections_for_visual_hold(
    adapter: Any,
    *,
    sections: Iterable[str] = CORE_FORM_SECTIONS,
    recheck_wait_ms: int = 800,
    wait_ms: int = 250,
    max_scroll_steps: int = 200,
    on_result: ListingProgressCallback | None = None,
) -> dict[str, list[CoverageResult]]:
    """Fill all empty fields across all core sections and keep them all open.

    Each section is filled once with the existing safe visual-hold strategy. No
    extra multi-value ``+`` slots are created here; those are covered by normal
    functional coverage before this stage. After all sections are populated, a
    second listing-wide verification pass re-reads every PASS value in every
    section so later React updates cannot silently reset earlier fields.
    """

    ordered = list(sections)
    _assert_sections_collapsed(adapter, ordered)
    results_by_section: dict[str, list[CoverageResult]] = {}
    opened: list[str] = []

    try:
        for section_title in ordered:
            def callback(item: CoverageResult, index: int, total: int, *, _title: str = section_title) -> None:
                if on_result is not None:
                    on_result(_title, item, index, total)

            results = fill_section_for_visual_hold(
                adapter,
                section_title,
                recheck_wait_ms=recheck_wait_ms,
                wait_ms=wait_ms,
                max_scroll_steps=max_scroll_steps,
                on_result=callback,
            )
            results_by_section[section_title] = results
            opened.append(section_title)

            # A later section must not auto-collapse an earlier one. If Makro
            # behaves like an accordion, fail instead of pretending all fields
            # can be inspected simultaneously.
            for previous in opened:
                live = adapter.find_section(previous)
                if live is None or live.get("has_edit"):
                    raise RuntimeError(
                        f"打开 {section_title!r} 后 {previous!r} 不再保持展开；"
                        "无法形成三个 section 同时可见的 whole-listing hold。"
                    )

        # Final listing-wide re-verification after the last field of the last
        # section has been written.
        adapter.page.wait_for_timeout(recheck_wait_ms)
        for section_title in ordered:
            live = adapter.find_section(section_title)
            if live is None or live.get("has_edit"):
                raise RuntimeError(f"最终 whole-listing 复核时 {section_title!r} 未保持展开。")
            section_path = str(live.get("path") or "")
            if not section_path:
                raise RuntimeError(f"{section_title!r} 缺少 DOM path。")
            _verify_final_hold(
                adapter,
                section_title,
                section_path,
                results_by_section[section_title],
                wait_ms=wait_ms,
                max_scroll_steps=max_scroll_steps,
            )

        failures: list[str] = []
        for section_title in ordered:
            summary = summarize_results(results_by_section[section_title])
            if summary["failed_or_unsupported"]:
                failures.append(
                    f"{section_title}: {summary['passed']}/{summary['empty_field_attempts']} PASS"
                )
        if failures:
            raise RuntimeError("whole-listing final verification failed: " + "; ".join(failures))

        return results_by_section
    except BaseException:
        # Fail closed even on Ctrl+C during population.
        cleanup_all_visual_hold_sections(adapter, opened)
        raise
