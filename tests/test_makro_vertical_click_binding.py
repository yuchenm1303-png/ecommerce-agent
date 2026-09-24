from __future__ import annotations

import pytest

import app.makro.search_surface as search_surface
import app.makro.vertical_selection as vertical_selection


def test_direct_step2_rejects_changed_but_unrelated_canonical() -> None:
    with pytest.raises(RuntimeError, match="cannot be bound"):
        vertical_selection._verify_retry_canonical(
            None,
            "Air Purifiers",
            previous_canonical="",
            actual_canonical="container",
            selected_visible=False,
        )


def test_direct_step2_accepts_semantically_equivalent_canonical_slug() -> None:
    vertical_selection._verify_retry_canonical(
        None,
        "Air Purifiers",
        previous_canonical="",
        actual_canonical="air_purifier",
        selected_visible=False,
    )


def test_visible_step1_confirmation_is_independent_binding_evidence() -> None:
    vertical_selection._verify_retry_canonical(
        None,
        "Air Purifiers",
        previous_canonical="",
        actual_canonical="internal_slug",
        selected_visible=True,
    )


def test_query_owned_click_never_climbs_into_different_label_container() -> None:
    script = search_surface._CLICK_ROW_JS
    assert "if (normalize(targetText) !== wantedKey) break;" in script
    assert "same_label_wrapper" in script
    assert "matches[0].click();" not in script
    assert "no_exact_action_target" in script
