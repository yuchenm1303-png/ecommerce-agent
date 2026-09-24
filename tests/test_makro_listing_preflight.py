from __future__ import annotations

import inspect

import makro_listing_preflight
from app.makro.coverage import PASS, CoverageResult
from app.makro.listing_preflight import (
    CORE_FORM_SECTIONS,
    SectionAudit,
    SectionPreflightResult,
    _advertised_total,
    summarize_listing_preflight,
)


def _result(section: str, count: int = 1) -> SectionPreflightResult:
    audit = SectionAudit(section, count, count, [])
    coverage = [
        CoverageResult(section, "", f"k{i}", f"L{i}", "text", PASS)
        for i in range(count)
    ]
    return SectionPreflightResult(
        section=section,
        audit=audit,
        functional_results=coverage,
        visual_results=coverage,
        screenshot=f"{section}.png",
        cleanup_clicked=True,
    )


def test_core_sections_are_exactly_the_three_listing_forms():
    assert CORE_FORM_SECTIONS == (
        "Price, Stock and Shipping Information",
        "Product Description",
        "Additional Description",
    )


def test_advertised_total_parses_section_progress():
    assert _advertised_total("Product Description (0/14)") == 14
    assert _advertised_total("Additional Description (Optional) (46/46)") == 46
    assert _advertised_total("Price, Stock and Shipping Information (0/14)") == 14
    assert _advertised_total("No counter") is None


def test_listing_summary_requires_all_three_sections_and_counts_semantics():
    results = [_result(CORE_FORM_SECTIONS[0], 14), _result(CORE_FORM_SECTIONS[1], 14), _result(CORE_FORM_SECTIONS[2], 46)]
    summary = summarize_listing_preflight(results)
    assert summary["sections"] == 3
    assert summary["sections_passed"] == 3
    assert summary["all_sections_passed"] is True
    assert summary["advertised_total"] == 74
    assert summary["semantic_total"] == 74
    assert summary["functional_passed"] == 74
    assert summary["visual_passed"] == 74


def test_cli_requires_vertical_and_has_no_save_submit_or_browser_close_path():
    parser = makro_listing_preflight.build_parser()
    args = parser.parse_args(["--expected-vertical", "vehicle_camera_system"])
    assert args.expected_vertical == "vehicle_camera_system"

    source = inspect.getsource(makro_listing_preflight.main)
    assert 'get_by_text("Save"' not in source
    assert 'get_by_text("Send to QC"' not in source
    assert "browser.close()" not in source
    assert "context.close()" not in source
    assert "harness.detach()" in source
