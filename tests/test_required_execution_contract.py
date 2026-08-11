from __future__ import annotations

import inspect

import pytest

from app.ai_decisions import field_id
from app.fill_plan import BLOCKED, READY, LiveFillPlan, LiveFillPlanItem
from app.makro.execution import fill_one_section, run_photos
from app.makro.photos import (
    PHOTO_SLOT_IDS,
    _DynamicPhotoFileTarget,
    _next_empty_photo_slot,
    _photo_surface,
    _select_file_input,
    _stage_accepted,
    _uploading_visible,
    _visible_upload_photo_button,
    _wait_for_target_slot_completion,
    _wait_for_upload_photo_button,
)
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


def test_production_photo_path_fills_all_fixed_slots_then_saves_once():
    source = inspect.getsource(run_photos)
    loop = source.index("for offset, image in enumerate(pending, start=1):")
    save = source.index("adapter.save_section(PRODUCT_PHOTOS)", loop)
    verify = source.index("expected_added=expected_new", save)

    assert loop < save < verify
    assert 'report["save_count"] = 1' in source
    assert 'report["staged"] += 1' in source
    assert 'report["persisted"] = final_count' in source
    assert "before_add_tiles=before_add_tiles" in source
    assert "expected_added=1" not in source


def test_product_photos_uses_real_thumbnail_ids_and_shared_input_surface():
    surface_source = inspect.getsource(_photo_surface)
    next_slot_source = inspect.getsource(_next_empty_photo_slot)
    select_source = inspect.getsource(_select_file_input)

    assert PHOTO_SLOT_IDS == (
        "thumbnail_0",
        "thumbnail_1",
        "thumbnail_2",
        "thumbnail_3",
        "thumbnail_4",
    )
    assert '[id^="thumbnail_"]' in surface_source
    assert 'input[type="file"]' in surface_source
    assert "i.fa-plus" in next_slot_source
    assert "PHOTO_SLOT_IDS" in next_slot_source
    assert "_next_empty_photo_slot" in select_source
    assert "_DynamicPhotoFileTarget" in select_source
    assert "AddProductImage" not in next_slot_source


def test_dynamic_photo_target_bypasses_banner_stability_then_clicks_upload_once():
    source = inspect.getsource(_DynamicPhotoFileTarget.set_input_files)

    slot_click = source.index("slot.click(timeout=1_500, force=True)")
    button_lookup = source.index("_wait_for_upload_photo_button", slot_click)
    chooser = source.index("expect_file_chooser(timeout=1_500)", button_lookup)
    upload_click = source.index("upload_button.click(timeout=1_500, force=True)", chooser)
    completion_wait = source.index("_wait_for_target_slot_completion", upload_click)

    assert slot_click < button_lookup < chooser < upload_click < completion_wait
    assert 'locator(f"#{self.slot_id}")' in source
    assert "scroll_into_view_if_needed" not in source
    assert "role_selector.click()" not in source
    assert "i.fa-plus" not in source
    assert "set_files" in source
    assert "_raw_file_input" in source
    assert "shared.set_input_files" in source


def test_upload_photo_button_is_exact_visible_enabled_active_role_control():
    source = inspect.getsource(_visible_upload_photo_button)

    assert 'get_by_text("Upload Photo", exact=True)' in source
    assert "is_visible" in source
    assert "is_enabled" in source
    assert "ancestor-or-self::button" in source
    assert "len(visible) > 1" in source


def test_photo_upload_waits_on_real_uploading_state_not_page_stability():
    target_source = inspect.getsource(_DynamicPhotoFileTarget.set_input_files)
    button_wait_source = inspect.getsource(_wait_for_upload_photo_button)
    uploading_source = inspect.getsource(_uploading_visible)
    completion_source = inspect.getsource(_wait_for_target_slot_completion)

    assert "wait_for_timeout(250)" not in target_source
    assert "force=True" in target_source
    assert "expect_file_chooser(timeout=1_500)" in target_source
    assert "timeout_ms: int = 2_000" in button_wait_source
    assert "wait_for_timeout(50)" in button_wait_source
    assert "Uploading" in uploading_source
    assert "soft_timeout_ms: int = 12_000" in completion_source
    assert "uploading_timeout_ms: int = 60_000" in completion_source
    assert "uploading_seen" in completion_source
    assert "wait_for_timeout(100)" in completion_source
    assert "slot_id not in empty_slots" in completion_source


def test_photo_acceptance_can_be_proved_by_target_thumbnail_losing_plus():
    assert _stage_accepted(
        {
            "visible_image_count": 5,
            "visible_image_sources": ["sample-a", "sample-b"],
            "completion_count": 0,
            "add_image_tile_count": 4,
            "empty_slot_ids": [
                "thumbnail_1",
                "thumbnail_2",
                "thumbnail_3",
                "thumbnail_4",
            ],
        },
        before_images=5,
        before_sources={"sample-a", "sample-b"},
        before_completion=0,
        before_add_tiles=5,
        target_slot_id="thumbnail_0",
    )
