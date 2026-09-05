from __future__ import annotations

from app.makro.listing_creation import ListingBootstrapHints
from app.makro.vertical_resolution import build_vertical_search_plan_request, plan_vertical_search_terms
from app.makro.vertical_selection import _broad_first_search_execution_terms


class _Provider:
    name = "fake"

    def extract_json(self, request_payload):
        assert request_payload["task"] == "plan_makro_vertical_search_intents"
        return {
            "specific_queries": [
                "niacinamide discoloration correcting serum",
                "brightening face serum",
            ],
            "alternate_queries": [
                "dark spot serum",
                "facial treatment serum",
            ],
            "broader_queries": [
                "skincare treatment",
                "serum",
            ],
            "head_noun_query": "skincare serum",
        }


def _serum_hints() -> ListingBootstrapHints:
    summary = "Niacinamide facial serum for dark spots and uneven skin tone."
    return ListingBootstrapHints(
        vertical_search_terms=("niacinamide skincare serum",),
        brand="",
        brand_status="unknown",
        product_summary=summary,
        product_identity={
            "entity_kind": "physical_product",
            "product_type_en": "niacinamide skincare serum",
            "brand": "",
            "brand_status": "unknown",
            "product_summary": summary,
            "confidence": 0.95,
            "evidence_refs": ["identity:page-title"],
        },
    )


def test_planner_explicitly_requests_short_core_class_anchor() -> None:
    request = build_vertical_search_plan_request(_serum_hints())
    rules = " ".join(request["rules"]).casefold()

    assert "single common noun" in rules
    assert "high-recall marketplace retrieval" in rules
    assert "retrieval probe" in rules
    assert "complete makro breadcrumb" in rules


def test_single_word_core_anchor_survives_budget_and_executes_first() -> None:
    hints = _serum_hints()
    planned = plan_vertical_search_terms(_Provider(), hints)

    assert "serum" in planned
    assert planned[-1] == "niacinamide skincare serum"

    execution = _broad_first_search_execution_terms(hints, planned)

    assert execution[0] == "serum"
    assert execution[-1] == "niacinamide skincare serum"
    assert len(execution) <= 7
