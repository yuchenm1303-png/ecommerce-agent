from __future__ import annotations

from app.makro_dryrun import (
    _preflight_answer_capacity,
    _qualifier_targets,
)
from app.resolution_types import ResolvedAnswer


_UNIT_OPTIONS = [
    {"text": "cm", "value": "cm", "disabled": False},
    {"text": "mm", "value": "mm", "disabled": False},
]


def _control(name: str, *, kind: str = "input") -> dict[str, object]:
    control: dict[str, object] = {
        "name": name,
        "field_kind": kind,
        "path": f"body > input[name='{name}']",
        "selector_candidates": [],
    }
    if name.endswith("_qualifier"):
        # Selectable qualifiers are executable only with captured enabled options.
        control["options"] = list(_UNIT_OPTIONS)
    return control


def _answer(values: list[str], qualifier: str = "cm") -> ResolvedAnswer:
    return ResolvedAnswer(
        attribute_key="dimensions",
        label="Dimensions",
        status="resolved",
        answer=" | ".join(values),
        answer_values=values,
        qualifier=qualifier,
        source_type="fixture",
        source_reference="fixture",
        evidence="fixture",
        confidence=1.0,
    )


def test_one_shared_qualifier_is_used_once_for_multiple_values():
    field = {
        "attribute_key": "dimensions",
        "multi_value": True,
        "controls": [
            _control("dimensions_0_value"),
            _control("dimensions_1_value"),
            _control("dimensions_2_value"),
            _control("dimensions_qualifier", kind="select"),
        ],
    }
    answer = _answer(["10", "20", "30"])
    assert _preflight_answer_capacity(field, answer) is None
    assert [control["name"] for control in _qualifier_targets(field, 3)] == [
        "dimensions_qualifier"
    ]


def test_one_qualifier_per_value_slot_is_applied_to_every_written_slot():
    field = {
        "attribute_key": "dimensions",
        "multi_value": True,
        "controls": [
            _control("dimensions_0_value"),
            _control("dimensions_1_value"),
            _control("dimensions_2_value"),
            _control("dimensions_0_qualifier", kind="select"),
            _control("dimensions_1_qualifier", kind="select"),
            _control("dimensions_2_qualifier", kind="select"),
        ],
    }
    answer = _answer(["10", "20", "30"])
    assert _preflight_answer_capacity(field, answer) is None
    assert [control["name"] for control in _qualifier_targets(field, 3)] == [
        "dimensions_0_qualifier",
        "dimensions_1_qualifier",
        "dimensions_2_qualifier",
    ]


def test_partially_repeated_qualifier_shape_fails_before_any_write():
    field = {
        "attribute_key": "dimensions",
        "multi_value": True,
        "controls": [
            _control("dimensions_0_value"),
            _control("dimensions_1_value"),
            _control("dimensions_2_value"),
            _control("dimensions_0_qualifier", kind="select"),
            _control("dimensions_1_qualifier", kind="select"),
        ],
    }
    error = _preflight_answer_capacity(field, _answer(["10", "20", "30"]))
    assert error is not None
    assert "3 个待写 value" in error
    assert "2 个 qualifier control" in error
    assert "未执行任何部分写入" in error
