"""One-shot Makro core-form preflight.

This module closes the browser execution-layer validation in one run. It audits,
functionally exercises, visually proves, and safely cleans the three core Makro
listing form sections without ever clicking Save or Send to QC.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .coverage import (
    PASS,
    SKIPPED_EXISTING,
    CoverageResult,
    _require_collapsed_section,
    _value_controls,
    cancel_section,
    run_section_coverage,
    summarize_results,
)
from .visual_hold import cleanup_visual_hold_section, fill_section_for_visual_hold

CORE_FORM_SECTIONS = (
    "Price, Stock and Shipping Information",
    "Product Description",
    "Additional Description",
)


@dataclass(slots=True)
class SectionAudit:
    section: str
    advertised_total: int | None
    semantic_count: int
    blank_uneditable: list[str] = field(default_factory=list)

    @property
    def count_matches(self) -> bool:
        return self.advertised_total is None or self.semantic_count == self.advertised_total

    @property
    def safe_to_test(self) -> bool:
        return self.count_matches and not self.blank_uneditable

    def as_dict(self) -> dict[str, Any]:
        return {
            "section": self.section,
            "advertised_total": self.advertised_total,
            "semantic_count": self.semantic_count,
            "count_matches": self.count_matches,
            "blank_uneditable": list(self.blank_uneditable),
            "safe_to_test": self.safe_to_test,
        }


@dataclass(slots=True)
class SectionPreflightResult:
    section: str
    audit: SectionAudit
    functional_results: list[CoverageResult]
    visual_results: list[CoverageResult]
    screenshot: str | None
    cleanup_clicked: bool

    @property
    def functional_summary(self) -> dict[str, Any]:
        return summarize_results(self.functional_results)

    @property
    def visual_summary(self) -> dict[str, Any]:
        return summarize_results(self.visual_results)

    @property
    def passed(self) -> bool:
        f = self.functional_summary
        v = self.visual_summary
        return (
            self.audit.safe_to_test
            and f["failed_or_unsupported"] == 0
            and v["failed_or_unsupported"] == 0
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "section": self.section,
            "audit": self.audit.as_dict(),
            "functional_summary": self.functional_summary,
            "visual_summary": self.visual_summary,
            "functional_results": [item.as_dict() for item in self.functional_results],
            "visual_results": [item.as_dict() for item in self.visual_results],
            "screenshot": self.screenshot,
            "cleanup_clicked": self.cleanup_clicked,
            "passed": self.passed,
        }


def _advertised_total(title: str) -> int | None:
    """Extract the denominator from titles such as ``(0/14)`` or ``(46/46)``."""

    matches = re.findall(r"\((\d+)\s*/\s*(\d+)\)", str(title or ""))
    if not matches:
        return None
    return int(matches[-1][1])


def _captured_value(control: dict[str, Any]) -> str:
    if control.get("value_recorded"):
        return str(control.get("value") or "").strip()
    for option in control.get("options") or []:
        if option.get("selected"):
            return str(option.get("text") or option.get("value") or "").strip()
    return ""


def _looks_blank(value: str) -> bool:
    return value.strip().casefold() in {
        "",
        "select",
        "select one",
        "choose",
        "choose one",
        "please select",
        "please select one",
        "-- select --",
        "- select -",
    }


def audit_section(
    adapter: Any,
    section_title: str,
    *,
    wait_ms: int = 250,
    max_scroll_steps: int = 200,
) -> SectionAudit:
    """Prove discovery completeness before writing anything synthetic."""

    section = _require_collapsed_section(adapter, section_title)
    advertised = _advertised_total(str(section.get("title") or ""))
    adapter.open_section_for_edit(section)
    try:
        live = adapter.find_section(section_title) or section
        section_path = str(live.get("path") or "")
        if not section_path:
            raise RuntimeError(f"section {section_title!r} 缺少 DOM path。")
        controls = adapter.scan_section_fields(
            section_path,
            include_values=True,
            wait_ms=wait_ms,
            max_scroll_steps=max_scroll_steps,
        )
        fields = adapter.build_semantic_fields(controls)
        blank_uneditable: list[str] = []
        for item in fields:
            values = _value_controls(item)
            if not values:
                continue
            values_blank = all(_looks_blank(_captured_value(control)) for control in values)
            values_uneditable = all(
                bool(control.get("disabled") or control.get("readonly")) for control in values
            )
            if values_blank and values_uneditable:
                blank_uneditable.append(
                    str(item.get("label") or item.get("attribute_key") or "<unknown>")
                )
        return SectionAudit(
            section=section_title,
            advertised_total=advertised,
            semantic_count=len(fields),
            blank_uneditable=blank_uneditable,
        )
    finally:
        cancel_section(adapter, section_title)


def _assert_clean_collapsed(adapter: Any, section_title: str) -> None:
    section = adapter.find_section(section_title)
    if section is None:
        raise RuntimeError(f"最终复核找不到 section：{section_title}")
    if not section.get("has_edit"):
        raise RuntimeError(f"最终复核发现 section 未折叠：{section_title}")


def page_has_coverage_residue(page: Any) -> bool:
    """Detect synthetic markers in values/text after every Cancel cleanup."""

    return bool(
        page.evaluate(
            """() => {
              const marker = /COVERAGE_/i;
              for (const el of document.querySelectorAll('input, textarea')) {
                if (marker.test(String(el.value || ''))) return true;
              }
              for (const el of document.querySelectorAll('[contenteditable="true"]')) {
                if (marker.test(String(el.innerText || el.textContent || ''))) return true;
              }
              return false;
            }"""
        )
    )


def run_core_section_preflight(
    adapter: Any,
    section_title: str,
    *,
    evidence_dir: Path,
    recheck_wait_ms: int = 800,
    wait_ms: int = 250,
    max_scroll_steps: int = 200,
) -> SectionPreflightResult:
    """Audit -> full functional coverage -> simultaneous visual proof -> Cancel."""

    audit = audit_section(
        adapter,
        section_title,
        wait_ms=wait_ms,
        max_scroll_steps=max_scroll_steps,
    )
    if not audit.safe_to_test:
        detail = []
        if not audit.count_matches:
            detail.append(
                f"semantic count {audit.semantic_count} != advertised {audit.advertised_total}"
            )
        if audit.blank_uneditable:
            detail.append(f"blank uneditable={audit.blank_uneditable}")
        raise RuntimeError(f"{section_title} discovery audit failed: {'; '.join(detail)}")

    functional = run_section_coverage(
        adapter,
        section_title,
        recheck_wait_ms=recheck_wait_ms,
        exercise_multi_value=True,
        wait_ms=wait_ms,
        max_scroll_steps=max_scroll_steps,
    )
    functional_summary = summarize_results(functional)
    if functional_summary["failed_or_unsupported"]:
        # Keep collecting at the outer orchestrator level, but don't create a visual
        # proof from a functionally broken section.
        return SectionPreflightResult(
            section=section_title,
            audit=audit,
            functional_results=functional,
            visual_results=[],
            screenshot=None,
            cleanup_clicked=False,
        )

    visual: list[CoverageResult] = []
    screenshot_path: str | None = None
    cleanup_clicked = False
    try:
        visual = fill_section_for_visual_hold(
            adapter,
            section_title,
            recheck_wait_ms=recheck_wait_ms,
            wait_ms=wait_ms,
            max_scroll_steps=max_scroll_steps,
        )
        evidence_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9]+", "-", section_title).strip("-").lower()
        image = evidence_dir / f"{safe_name}.png"
        adapter.page.screenshot(path=str(image), full_page=True)
        screenshot_path = str(image.resolve())
    finally:
        section = adapter.find_section(section_title)
        if section is not None and not section.get("has_edit"):
            cleanup_clicked = cleanup_visual_hold_section(adapter, section_title)

    _assert_clean_collapsed(adapter, section_title)
    if page_has_coverage_residue(adapter.page):
        raise RuntimeError(f"{section_title} Cancel 后仍发现 COVERAGE_ 残留。")

    return SectionPreflightResult(
        section=section_title,
        audit=audit,
        functional_results=functional,
        visual_results=visual,
        screenshot=screenshot_path,
        cleanup_clicked=cleanup_clicked,
    )


def summarize_listing_preflight(results: list[SectionPreflightResult]) -> dict[str, Any]:
    return {
        "sections": len(results),
        "sections_passed": sum(1 for item in results if item.passed),
        "all_sections_passed": bool(results) and all(item.passed for item in results),
        "advertised_total": sum(item.audit.advertised_total or 0 for item in results),
        "semantic_total": sum(item.audit.semantic_count for item in results),
        "functional_empty_attempts": sum(
            item.functional_summary["empty_field_attempts"] for item in results
        ),
        "functional_passed": sum(item.functional_summary["passed"] for item in results),
        "functional_failed_or_unsupported": sum(
            item.functional_summary["failed_or_unsupported"] for item in results
        ),
        "functional_skipped_existing": sum(
            item.functional_summary["skipped_existing"] for item in results
        ),
        "visual_empty_attempts": sum(
            item.visual_summary["empty_field_attempts"] for item in results
        ),
        "visual_passed": sum(item.visual_summary["passed"] for item in results),
        "visual_failed_or_unsupported": sum(
            item.visual_summary["failed_or_unsupported"] for item in results
        ),
    }
