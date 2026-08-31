from __future__ import annotations

from types import SimpleNamespace

from app.makro.behavior_contract import (
    REACT_STABLE,
    REPEATABLE_COMMIT,
    UNVERIFIED,
    build_run_contracts,
    marketplace_completeness,
    merge_contract_registry,
    observe_field,
    signature_for_field,
    verification_level,
)


def _field(
    *,
    key: str = "breadth",
    label: str = "Breadth",
    context: str = "Breadth *CM",
    kind: str = "input",
    input_type: str = "number",
    inputmode: str = "decimal",
    multi: bool = False,
    add: bool = False,
    qualifier: bool = False,
) -> dict:
    controls = [
        {
            "id": key,
            "name": f"{key}_0_value",
            "field_kind": kind,
            "tag": "input",
            "type": input_type,
            "inputmode": inputmode,
            "context_text": context,
            "label": label,
        }
    ]
    if qualifier:
        controls.append(
            {
                "id": "",
                "name": f"{key}_0_qualifier",
                "field_kind": "select",
                "tag": "select",
                "type": "select",
                "context_text": label,
                "label": label,
            }
        )
    return {
        "attribute_key": key,
        "label": label,
        "section_heading": "Product Description",
        "subsection_heading": "",
        "required": True,
        "multi_value": multi,
        "has_add_value_control": add,
        "controls": controls,
    }


def _result(observation, *, plus_available: bool = False, plus_status: str = "not_available"):
    return SimpleNamespace(
        section=observation.section,
        attribute_key=observation.attribute_key,
        label=observation.label,
        status="pass",
        plus_available=plus_available,
        plus_status=plus_status,
    )


def test_fixed_units_share_mechanical_signature_but_keep_observed_unit() -> None:
    cm = observe_field(_field(key="breadth", label="Breadth", context="Breadth *CM"))
    kg = observe_field(_field(key="weight", label="Weight", context="Weight *KG"))

    assert cm.fingerprint == kg.fingerprint
    assert cm.signature.qualifier_model == "fixed"
    assert kg.signature.qualifier_model == "fixed"
    assert cm.fixed_unit == "cm"
    assert kg.fixed_unit == "kg"


def test_selectable_qualifier_is_a_different_contract_from_fixed_unit() -> None:
    fixed = signature_for_field(_field(context="Breadth *CM"))
    selectable = signature_for_field(_field(context="Breadth", qualifier=True))

    assert fixed.qualifier_model == "fixed"
    assert selectable.qualifier_model == "selectable"
    assert fixed.fingerprint != selectable.fingerprint


def test_repeatable_add_requires_real_add_slot_evidence() -> None:
    observation = observe_field(
        _field(key="ingredients", label="Ingredients", context="Ingredients", input_type="text", inputmode="", multi=True, add=True)
    )

    level, _ = verification_level(observation, _result(observation))
    assert level == UNVERIFIED

    level, _ = verification_level(
        observation,
        _result(observation, plus_available=True, plus_status="pass"),
    )
    assert level == REPEATABLE_COMMIT


def test_pre_rendered_repeatable_slots_do_not_pass_on_first_slot_only() -> None:
    field = _field(
        key="keywords",
        label="Keywords",
        context="Keywords",
        input_type="text",
        inputmode="",
        multi=True,
        add=False,
    )
    field["controls"].append(
        {
            **field["controls"][0],
            "name": "keywords_1_value",
        }
    )
    observation = observe_field(field)

    assert observation.signature.commit_model == "repeatable_slots"
    level, _ = verification_level(observation, _result(observation))
    assert level == UNVERIFIED


def test_direct_field_stable_readback_is_mechanically_verified() -> None:
    observation = observe_field(_field())
    level, _ = verification_level(observation, _result(observation))
    assert level == REACT_STABLE


def test_registry_groups_by_contract_not_field_label() -> None:
    breadth = observe_field(_field(key="breadth", label="Breadth", context="Breadth *CM"))
    height = observe_field(_field(key="height", label="Height", context="Height *CM"))
    run = build_run_contracts(
        vertical="Test Vertical",
        observations=[breadth, height],
        results=[_result(breadth), _result(height)],
    )

    assert run["stats"]["observed_field_count"] == 2
    assert run["stats"]["unique_contract_count"] == 1
    assert run["stats"]["verified_contract_count"] == 1
    assert run["stats"]["fully_verified"] is True


def test_marketplace_completeness_requires_every_harvested_vertical() -> None:
    observation = observe_field(_field())
    run = build_run_contracts(
        vertical="Vertical A",
        observations=[observation],
        results=[_result(observation)],
    )
    registry = merge_contract_registry({}, run)
    schema = {"verticals": {"Vertical A": {}, "Vertical B": {}}}

    completeness = marketplace_completeness(schema, registry)
    assert completeness["marketplace_complete"] is False
    assert completeness["missing_verticals"] == ["Vertical B"]
