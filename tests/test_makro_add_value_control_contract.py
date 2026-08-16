from __future__ import annotations

from types import SimpleNamespace

import app.makro.domain as domain
from app.makro.domain import MakroDomainAdapter
from app.makro.fields import _SCAN_SCRIPT, _merge_semantic_field
from app.makro.locators import ADD_VALUE_CONTROL_JS, click_add_value_for_control


def test_schema_scanner_and_executor_share_exact_add_value_detector():
    assert ADD_VALUE_CONTROL_JS in _SCAN_SCRIPT
    assert '[role="button"]' in ADD_VALUE_CONTROL_JS
    assert '[onclick]' in ADD_VALUE_CONTROL_JS
    assert '[tabindex]:not([tabindex="-1"])' in ADD_VALUE_CONTROL_JS
    assert "data-testid" in ADD_VALUE_CONTROL_JS
    assert "actions.length !== 1" in ADD_VALUE_CONTROL_JS


def test_disabled_add_button_still_counts_as_repeatable_capability():
    # Makro renders AddRemoveValueIcon as disabled while the current slot is
    # empty. Capability detection must keep that structural control, while the
    # click helper independently refuses to click until React enables it.
    assert "disabledAddNode(action)) continue" not in ADD_VALUE_CONTROL_JS
    assert "disabled: disabledAddNode(node)" in ADD_VALUE_CONTROL_JS
    assert "add-disabled" in click_add_value_for_control.__doc__ or "add-disabled" in str(
        click_add_value_for_control.__code__.co_consts
    )


def test_live_add_marker_makes_single_rendered_slot_repeatable():
    field = _merge_semantic_field(
        "keywords",
        [
            {
                "id": "keywords",
                "name": "keywords_0_value",
                "field_kind": "input",
                "label": "Keywords",
                "section_heading": "Additional Description",
                "required": False,
                "has_add_value_control": True,
                "options": [],
            }
        ],
    )

    assert field["has_add_value_control"] is True
    assert field["multi_value"] is True


def test_indexed_name_without_live_add_marker_stays_single_value():
    field = _merge_semantic_field(
        "processor",
        [
            {
                "id": "processor",
                "name": "processor_0_value",
                "field_kind": "input",
                "label": "Processor",
                "section_heading": "Additional Description",
                "required": False,
                "has_add_value_control": False,
                "options": [],
            }
        ],
    )

    assert field["has_add_value_control"] is False
    assert field["multi_value"] is False


class _FakePage:
    def __init__(self) -> None:
        self.waits: list[int] = []

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.waits.append(milliseconds)


def _repeatable_field(slot_count: int) -> dict[str, object]:
    return {
        "attribute_key": "keywords",
        "label": "Keywords",
        "section_heading": "Additional Description",
        "has_add_value_control": True,
        "multi_value": True,
        "controls": [
            {
                "id": "keywords" if index == 0 else "",
                "name": f"keywords_{index}_value",
                "field_kind": "input",
            }
            for index in range(slot_count)
        ],
    }


def test_disabled_add_is_seeded_then_slots_materialize_progressively(monkeypatch):
    page = _FakePage()
    adapter = MakroDomainAdapter(page)  # type: ignore[arg-type]
    state = {"slots": 1, "seeded": set()}
    seeded: list[tuple[int, str]] = []
    clicks: list[str] = []

    def fake_fill_live_control(_page, control, value, section_path=None):
        index = int(str(control["name"]).split("_")[-2])
        seeded.append((index, value))
        state["seeded"].add(index)
        return f"{section_path}:{control['name']}"

    def fake_click_add(_page, _section_path, _control):
        current_index = state["slots"] - 1
        clicks.append(f"slot-{current_index}")
        if current_index not in state["seeded"]:
            return {
                "available": True,
                "clicked": False,
                "reason": "add-disabled",
                "count": 1,
            }
        state["slots"] += 1
        return {"available": True, "clicked": True, "reason": "", "count": 1}

    def fake_refresh(_current, _section_path):
        return _repeatable_field(state["slots"])

    monkeypatch.setattr(domain, "fill_live_control", fake_fill_live_control)
    monkeypatch.setattr(domain, "click_add_value_for_control", fake_click_add)
    monkeypatch.setattr(adapter, "_refresh_field", fake_refresh)

    answer = SimpleNamespace(answer_values=["one", "two", "three"], qualifier=None)
    expanded = adapter._ensure_answer_value_slots(
        _repeatable_field(1),
        answer,
        "section-path",
    )

    assert len(domain._value_controls(expanded)) == 3
    assert seeded == [(0, "one"), (1, "two")]
    assert clicks == ["slot-0", "slot-0", "slot-1", "slot-1"]
