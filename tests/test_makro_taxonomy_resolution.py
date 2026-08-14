from __future__ import annotations

import pytest

from app.makro.listing_creation import ListingBootstrapHints
from app.makro.taxonomy_resolution import (
    build_taxonomy_leaf_validation_request,
    build_taxonomy_path_choice_request,
    choose_taxonomy_path_candidate,
    validate_taxonomy_leaf_candidate,
)


class FakeProvider:
    name = "fake"

    def __init__(self, responses):
        self.responses = dict(responses)
        self.requests = []

    def extract_json(self, request_payload):
        self.requests.append(request_payload)
        return self.responses[request_payload["task"]]


def _hints() -> ListingBootstrapHints:
    summary = "Programmable outdoor watering timer with two outlets and rain delay."
    return ListingBootstrapHints(
        vertical_search_terms=("watering timer",),
        brand="",
        brand_status="unknown",
        product_summary=summary,
        product_identity={
            "entity_kind": "physical_product",
            "product_type_en": "watering timer",
            "brand": "",
            "brand_status": "unknown",
            "product_summary": summary,
            "confidence": 0.98,
            "evidence_refs": ["identity:page-title"],
        },
    )


def test_taxonomy_path_contract_distinguishes_category_ancestry_from_physical_containment() -> None:
    request = build_taxonomy_path_choice_request(
        _hints(),
        ["Home Improvement"],
        ["Gardening Tools", "Storage Containers"],
    )
    relation = request["json_contract"]["properties"]["selection_relation"]
    assert relation["enum"] == [
        "ancestor_branch",
        "same_product_type",
        "broader_valid_class",
        "none",
    ]
    rules = " ".join(request["rules"]).casefold()
    assert "physical containment is irrelevant" in rules
    assert "incidental compatibility or usage is irrelevant" in rules


def test_taxonomy_path_rejects_nonempty_node_with_none_relation() -> None:
    provider = FakeProvider(
        {
            "choose_safe_makro_taxonomy_node": {
                "selected_node": "Storage Containers",
                "selection_relation": "none",
            }
        }
    )
    with pytest.raises(ValueError, match="non-empty taxonomy selection"):
        choose_taxonomy_path_candidate(
            provider,
            _hints(),
            ["Home Improvement"],
            ["Gardening Tools", "Storage Containers"],
        )


def test_taxonomy_path_can_fail_closed_without_clicking_any_node() -> None:
    provider = FakeProvider(
        {
            "choose_safe_makro_taxonomy_node": {
                "selected_node": "",
                "selection_relation": "none",
            }
        }
    )
    assert choose_taxonomy_path_candidate(
        provider,
        _hints(),
        ["Home Improvement", "Gardening Tools"],
        ["Storage Containers"],
    ) == ""


def test_leaf_contract_is_a_second_independent_gate() -> None:
    request = build_taxonomy_leaf_validation_request(
        _hints(),
        ["Home Improvement", "Gardening Tools", "Watering Controllers"],
    )
    assert request["task"] == "validate_makro_taxonomy_leaf"
    assert request["json_contract"]["properties"]["selection_relation"]["enum"] == [
        "same_product_type",
        "broader_valid_class",
        "none",
    ]
    assert "unsupported_defining_constraints" in request["json_contract"]["required"]


def test_leaf_rejects_unsupported_defining_constraints_even_when_relation_claims_broader() -> None:
    provider = FakeProvider(
        {
            "validate_makro_taxonomy_leaf": {
                "selection_relation": "broader_valid_class",
                "unsupported_defining_constraints": ["unrelated defining purpose"],
                "reason": "The leaf changes what the product is.",
            }
        }
    )
    assert validate_taxonomy_leaf_candidate(
        provider,
        _hints(),
        ["Home Improvement", "Gardening Tools", "Other Product"],
    ) is False


def test_leaf_accepts_same_or_genuine_broader_class_only_without_unsupported_constraints() -> None:
    provider = FakeProvider(
        {
            "validate_makro_taxonomy_leaf": {
                "selection_relation": "broader_valid_class",
                "unsupported_defining_constraints": [],
                "reason": "This is a genuine retail superclass of the supplied product.",
            }
        }
    )
    assert validate_taxonomy_leaf_candidate(
        provider,
        _hints(),
        ["Home Improvement", "Gardening Tools", "Watering Controllers"],
    ) is True
