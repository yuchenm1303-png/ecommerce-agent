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


def _reconciliation_hints() -> ListingBootstrapHints:
    return ListingBootstrapHints(
        vertical_search_terms=("personalized wooden sunglasses",),
        brand="",
        brand_status="unknown",
        product_summary="Polarized protective eyewear with a wooden-style frame.",
        product_identity={
            "entity_kind": "physical_product",
            "product_type_en": "personalized wooden sunglasses",
            "product_summary": "Personalized wooden polarized sunglasses.",
            "confidence": 0.78,
            "evidence_refs": ["identity:page-title"],
        },
        customer_intent="1个偏光防护镜",
        grounded_product_evidence=(
            "supplier_product_heading: Polarized protective eyewear with impact-resistant lenses and wooden-style frame",
        ),
    )


def test_taxonomy_path_contract_is_one_atomic_selection_key() -> None:
    request = build_taxonomy_path_choice_request(
        _hints(),
        ["Home Improvement"],
        ["Gardening Tools", "Storage Containers"],
    )
    schema = request["json_contract"]
    assert schema["required"] == ["selection_key"]
    assert set(schema["properties"]) == {"selection_key"}
    choices = schema["properties"]["selection_key"]["enum"]
    assert choices == [
        "none",
        "ancestor_branch:node_0",
        "ancestor_branch:node_1",
        "best_available_branch:node_0",
        "best_available_branch:node_1",
        "same_product_type:node_0",
        "same_product_type:node_1",
        "broader_valid_class:node_0",
        "broader_valid_class:node_1",
    ]
    assert all(not choice.startswith("none:") for choice in choices)
    assert request["context"]["live_node_options"] == [
        {"node_id": "node_0", "label": "Gardening Tools"},
        {"node_id": "node_1", "label": "Storage Containers"},
    ]
    rules = " ".join(request["rules"]).casefold()
    assert "atomic" in rules
    assert "best_available_branch" in rules
    assert "physical containment alone" in rules


def test_taxonomy_reconciliation_preserves_independent_evidence_without_splitting_atomic_decision() -> None:
    hints = _reconciliation_hints()
    request = build_taxonomy_path_choice_request(
        hints,
        ["Industrial & Scientific Supplies"],
        ["Safety Products", "Storage Containers"],
    )

    context = request["context"]
    assert context["initial_product_identity"]["product_type_en"] == "personalized wooden sunglasses"
    assert context["customer_listing_intent"] == "1个偏光防护镜"
    assert "Polarized protective eyewear" in context["grounded_supplier_evidence"][0]
    assert set(request["json_contract"]["properties"]) == {"selection_key"}
    assert "selected_node" not in request["json_contract"]["properties"]
    assert "selection_relation" not in request["json_contract"]["properties"]

    leaf_request = build_taxonomy_leaf_validation_request(
        hints,
        ["Industrial & Scientific Supplies", "Safety Products", "Protective Glasses"],
    )
    leaf_context = leaf_request["context"]
    assert leaf_context["initial_product_identity"] == context["initial_product_identity"]
    assert leaf_context["grounded_supplier_evidence"] == context["grounded_supplier_evidence"]
    assert leaf_context["customer_listing_intent"] == context["customer_listing_intent"]


def test_taxonomy_path_rejects_legacy_split_field_response() -> None:
    provider = FakeProvider(
        {
            "choose_safe_makro_taxonomy_node": {
                "selected_node": "Storage Containers",
                "selection_relation": "none",
            }
        }
    )
    with pytest.raises(ValueError, match="invalid taxonomy selection_key"):
        choose_taxonomy_path_candidate(
            provider,
            _hints(),
            ["Home Improvement"],
            ["Gardening Tools", "Storage Containers"],
        )


def test_taxonomy_path_can_choose_practical_best_available_branch() -> None:
    provider = FakeProvider(
        {
            "choose_safe_makro_taxonomy_node": {
                "selection_key": "best_available_branch:node_0",
            }
        }
    )
    assert choose_taxonomy_path_candidate(
        provider,
        _hints(),
        ["Home Improvement"],
        ["Gardening Tools", "Storage Containers"],
    ) == "Gardening Tools"


def test_taxonomy_path_atomic_key_binds_exact_live_node() -> None:
    provider = FakeProvider(
        {
            "choose_safe_makro_taxonomy_node": {
                "selection_key": "broader_valid_class:node_1",
            }
        }
    )
    assert choose_taxonomy_path_candidate(
        provider,
        _hints(),
        ["Home Improvement"],
        ["Gardening Tools", "Watering Equipment"],
    ) == "Watering Equipment"


def test_taxonomy_path_can_still_return_none_when_every_branch_is_unusable() -> None:
    provider = FakeProvider(
        {
            "choose_safe_makro_taxonomy_node": {
                "selection_key": "none",
            }
        }
    )
    assert choose_taxonomy_path_candidate(
        provider,
        _hints(),
        ["Home Improvement", "Gardening Tools"],
        ["Storage Containers"],
    ) == ""


def test_taxonomy_path_rejects_unknown_atomic_key() -> None:
    provider = FakeProvider(
        {
            "choose_safe_makro_taxonomy_node": {
                "selection_key": "none:node_0",
            }
        }
    )
    with pytest.raises(ValueError, match="invalid taxonomy selection_key"):
        choose_taxonomy_path_candidate(
            provider,
            _hints(),
            ["Home Improvement"],
            ["Gardening Tools"],
        )


def test_leaf_contract_allows_explicit_best_available_fit() -> None:
    request = build_taxonomy_leaf_validation_request(
        _hints(),
        ["Home Improvement", "Gardening Tools", "Watering Controllers"],
    )
    assert request["task"] == "validate_makro_taxonomy_leaf"
    assert request["json_contract"]["properties"]["selection_relation"]["enum"] == [
        "same_product_type",
        "broader_valid_class",
        "best_available_fit",
        "none",
    ]
    assert "unsupported_defining_constraints" in request["json_contract"]["required"]


def test_leaf_strict_broader_relation_still_rejects_claimed_unsupported_constraints() -> None:
    provider = FakeProvider(
        {
            "validate_makro_taxonomy_leaf": {
                "selection_relation": "broader_valid_class",
                "unsupported_defining_constraints": ["different defining purpose"],
                "reason": "This is not truly a strict superclass.",
            }
        }
    )
    assert validate_taxonomy_leaf_candidate(
        provider,
        _hints(),
        ["Home Improvement", "Gardening Tools", "Other Product"],
    ) is False


def test_leaf_best_available_fit_accepts_documented_taxonomy_tradeoff() -> None:
    provider = FakeProvider(
        {
            "validate_makro_taxonomy_leaf": {
                "selection_relation": "best_available_fit",
                "unsupported_defining_constraints": ["category is broader/coarser than product"],
                "reason": "Makro has no exact watering-timer leaf; this is the closest usable live category.",
            }
        }
    )
    assert validate_taxonomy_leaf_candidate(
        provider,
        _hints(),
        ["Home Improvement", "Gardening Tools", "Irrigation Equipment"],
    ) is True


def test_leaf_accepts_same_or_genuine_broader_without_tradeoffs() -> None:
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
