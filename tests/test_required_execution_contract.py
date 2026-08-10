from __future__ import annotations

import inspect

import pytest

from app.ai_decisions import field_id
from app.fill_plan import BLOCKED, READY, LiveFillPlan, LiveFillPlanItem
from app.makro.execution import fill_one_section, run_photos
from app.makro.photos import _DynamicPhotoFileTarget, _photo_surface, _select_file_input
from app.makro.sections import save_section
from app.required_overrides import RequiredOverrideError, apply_required_overrides
from app.resolution_types import MISSING, ResolutionRecord


def _blocked_item(*, required: bool = True) -> LiveFillPlanItem:
    record = ResolutionRecord(
        attribute_key="colour",
        label="Colour",
        status=MISSING,
        answer=None,
        answer_values=[],
        qualifier=None,
        confidence=0.0,
        source_type=None,
        source_reference=None,
        evidence=None,
        detail="AI could not determine required value",
        eligible_for_autofill=False,
        gate_reason="ai_missing",
        question_category="Product Description (0/10)",
        question_options=["Orange", "Black"],
    )
    return LiveFillPlanItem(
        attribute_key="colour",
        label="Colour",
        section_heading="Product Description (0/10)",
        required=required,
        action=BLOCKED,
        reason=record.detail,
        resolution=record,
    )


def _live_field() -> dict[str, object]:
    return {
        "attribute_key": "colour",
        "label": "Colour",
        "section_heading": "Product Description (0/10)",
        "required": True,
        "multi_value": False,
        "options": ["Select One", "Orange", "Black"],
        "qualifier_options": [],
        "help_text": "",
        "context_text": "",
        "controls": [],
    }


def test_explicit_user_value_promotes_only_unresolved_required_field_to_ready():
    live = _live_field()
    item = _blocked_item()
    plan = LiveFillPlan([item])

    result = apply_required_overrides(
        plan,
        [live],
        [{"field_id": field_id(live), "values": ["Orange"]}],
    )

    assert result["applied"] == 1
    assert item.action == READY
    assert item.resolution.answer_values == ["Orange"]
    assert item.resolution.source_type == "user"
    assert item.resolution.source_reference == "user:required-field-input"
    assert item.resolution.eligible_for_autofill is True


def test_user_required_value_still_must_match_live_makro_option():
    live = _live_field()
    plan = LiveFillPlan([_blocked_item()])

    with pytest.raises(RequiredOverrideError, match="Colour"):
        apply_required_overrides(
            plan,
            [live],
            [{"field_id": field_id(live), "values": ["Purple"]}],
        )


def test_user_override_cannot_replace_optional_or_already_resolved_decision():
    live = _live_field()
    optional = _blocked_item(required=False)
    plan = LiveFillPlan([optional])
    with pytest.raises(RequiredOverrideError, match="不是 required"):
        apply_required_overrides(
            plan,
            [live],
            [{"field_id": field_id(live), "values": ["Orange"]}],
        )


def test_production_ready_executor_has_no_existing_value_second_gate():
    source = inspect.getsource(fill_one_section)
    assert "_has_existing_value" not in source
    assert "skipped_existing\"] +=" not in source
    assert 'report["writes_attempted"] += 1' in source


def test_save_section_clicks_makro_even_when_old_inline_errors_are_rendered():
    source = inspect.getsource(save_section)
    before_click, after_click = source.split("save.first.click()", maxsplit=1)

    assert "visible_section_errors(" not in before_click
    assert "collapsed_error_badges(" in after_click
    assert "visible_section_errors(" in after_click


def test_rejected_save_reopens_same_section_to_expose_field_level_error():
    source = inspect.getsource(save_section)
    after_click = source.split("save.first.click()", maxsplit=1)[1]

    assert "open_section_for_edit(page, live)" in after_click
    assert "expanded = find_section(page, section_title)" in after_click
    assert "field_errors = visible_section_errors(page, expanded_path)" in after_click
    assert "字段错误" in after_click


def test_production_photo_path_saves_and_verifies_each_image_before_next():
    source = inspect.getsource(run_photos)
    loop = source.index("for index, image in enumerate(resolved, start=1):")
    save = source.index("adapter.save_section(PRODUCT_PHOTOS)", loop)
    verify_one = source.index("expected_added=1", save)

    assert loop < save < verify_one
    assert 'report["persisted"] += 1' in source
    assert 'int(report["persisted"]) != requested' in source
    assert "_wait_for_file_input(adapter" in source
    assert "expected_added=requested" not in source


def test_product_photos_searches_the_gallery_sibling_surface_not_only_title_card():
    surface_source = inspect.getsource(_photo_surface)
    select_source = inspect.getsource(_select_file_input)

    assert 'locator("xpath=..")' in surface_source
    assert 'ImageGalleryWrapper' in surface_source
    assert 'AddProductImage' in surface_source
    assert "_raw_file_input" in select_source
    assert "_add_product_image_tile" in select_source


def test_dynamic_photo_target_uses_exact_add_tile_file_chooser_or_fresh_input():
    source = inspect.getsource(_DynamicPhotoFileTarget.set_input_files)

    assert "_raw_file_input" in source
    assert "_add_product_image_tile" in source
    assert "expect_file_chooser" in source
    assert "set_files" in source
    assert "direct.set_input_files" in source
