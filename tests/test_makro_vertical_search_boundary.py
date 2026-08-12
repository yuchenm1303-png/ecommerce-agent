from __future__ import annotations

import inspect

from app.makro import vertical_selection


def test_search_delta_rejects_preexisting_broad_taxonomy_nodes() -> None:
    before = [
        "Health & Beauty",
        "Bath and Spa",
        "Health Care Devices",
        "Personal Care & Grooming",
    ]
    after = [
        "Health & Beauty",
        "Bath and Spa",
        "Health Care Devices",
        "Personal Care & Grooming",
        "Neck Massagers",
        "Massage Devices",
    ]
    columns = [
        ["Furniture", "Gaming", "Health & Beauty", "Home & Kitchen"],
        ["Bath and Spa", "Health Care Devices", "Personal Care & Grooming"],
    ]

    assert vertical_selection._search_result_delta(before, after, columns) == [
        "Neck Massagers",
        "Massage Devices",
    ]


def test_search_delta_blocks_taxonomy_label_even_if_not_in_page_baseline() -> None:
    assert vertical_selection._search_result_delta(
        ["Browse Verticals"],
        ["Browse Verticals", "Health & Beauty", "Neck Massagers"],
        [["Health & Beauty"]],
    ) == ["Neck Massagers"]


def test_search_delta_is_fail_closed_when_query_exposes_nothing_new() -> None:
    before = ["Health & Beauty", "Bath and Spa"]
    after = ["Health & Beauty", "Bath and Spa"]

    assert vertical_selection._search_result_delta(
        before,
        after,
        [["Health & Beauty"], ["Bath and Spa"]],
    ) == []


def test_search_fallback_submits_enter_without_relaxing_candidate_boundary() -> None:
    source = inspect.getsource(vertical_selection._select_via_search_with_context)

    assert 'search.press("Enter")' in source
    assert "_search_result_delta(" in source
    assert "baseline_columns = taxonomy.columns()" in source
    assert "choose_vertical_candidate(provider, hints, term, candidates)" in source
