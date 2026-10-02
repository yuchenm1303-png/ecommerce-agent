from __future__ import annotations

import inspect

from app.ai_decisions import field_id
from app.fill_plan import BLOCKED, READY, LiveFillPlan, LiveFillPlanItem
from app.makro.execution import fill_one_section, run_photos
from app.makro.photos import (
    _DynamicPhotoFileTarget,
    _next_empty_photo_slot,
    _open_photo_slot_upload_panel,
    _photo_surface,
    _raw_file_input,
    _select_file_input,
    _slot_snapshot,
    _stage_accepted,
    _target_slot_acceptance_signal,
    _uploading_visible,
    _visible_upload_photo_button,
    _wait_for_target_slot_completion,
    _wait_for_upload_photo_button,
)
from app.makro.sections import save_section
from app.required_overrides import apply_required_overrides
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


def _live_field(*, free_text: bool = False) -> dict[str, object]:
    # User values pass the same live execution contract as every other source,
    # so the fixture carries the live control a Makro DOM scan produces.
    if free_text:
        control = {"id": "colour", "name": "colour_0_value", "type": "text", "field_kind": "input"}
    else:
        control = {
            "id": "colour",
            "name": "colour_0_value",
            "field_kind": "select",
            "options": [
                {"text": item, "value": item, "disabled": False}
                for item in ("Select One", "Orange", "Black")
            ],
        }
    return {
        "attribute_key": "colour",
        "label": "Colour",
        "section_heading": "Product Description (0/10)",
        "required": True,
        "multi_value": False,
        "options": [] if free_text else ["Select One", "Orange", "Black"],
        "qualifier_options": [],
        "help_text": "",
        "context_text": "",
        "controls": [control],
    }


def test_explicit_user_value_promotes_only_unresolved_required_field_to_ready():
    live = _live_field()
    item = _blocked_item()
    plan = LiveFillPlan([item])

    result = apply_required_overrides(
        plan,
        [live],
        [{"field_id": field_id(live), "values": ["Orange"], "source_type": "user"}],
    )

    assert result["applied"] == 1
    assert item.action == READY
    assert item.resolution.answer_values == ["Orange"]
    assert item.resolution.source_type == "user"
    assert item.resolution.source_reference == "user:required-override"
    assert item.resolution.eligible_for_autofill is True


def test_user_required_value_is_not_semantically_rejudged_by_python():
    # A free-text live control accepts any user wording; Python never compares
    # it with the AI's suggested question options.
    live = _live_field(free_text=True)
    item = _blocked_item()
    plan = LiveFillPlan([item])

    result = apply_required_overrides(
        plan,
        [live],
        [{"field_id": field_id(live), "values": ["Purple"], "source_type": "user"}],
    )

    assert result["applied"] == 1
    assert item.action == READY
    assert item.resolution.answer_values == ["Purple"]


def test_user_value_outside_live_select_domain_fails_mechanically_without_rewrite():
    live = _live_field()
    item = _blocked_item()
    plan = LiveFillPlan([item])

    result = apply_required_overrides(
        plan,
        [live],
        [{"field_id": field_id(live), "values": ["Purple"], "source_type": "user"}],
    )

    assert result["applied"] == 0
    assert result["isolated_failed"] == 1
    assert "不属于当前 enabled live options" in result["isolated_failures"][0]["reason"]
    assert item.action == BLOCKED
    assert item.resolution.answer_values == []


def test_user_override_cannot_promote_optional_unresolved_field():
    live = _live_field()
    optional = _blocked_item(required=False)
    plan = LiveFillPlan([optional])

    result = apply_required_overrides(
        plan,
        [live],
        [{"field_id": field_id(live), "values": ["Orange"], "source_type": "user"}],
    )

    # The refusal is recorded as a field-local failure; the optional field stays BLOCKED.
    assert result["applied"] == 0
    assert result["isolated_failed"] == 1
    assert "不是 required" in result["isolated_failures"][0]["reason"]
    assert optional.action == BLOCKED


def test_production_ready_executor_has_no_existing_value_second_gate():
    source = inspect.getsource(fill_one_section)
    assert "_has_existing_value" not in source
    assert "skipped_existing\"] +=" not in source
    assert 'report["writes_attempted"] += 1' in source


def test_save_section_clicks_makro_even_when_old_inline_errors_are_rendered():
    source = inspect.getsource(save_section)
    before_click, after_click = source.split("save.first.click()", maxsplit=1)

    assert "visible_section_errors(" not in before_click
    # Collapse back to EDIT is the persistence boundary. A residual red badge is
    # listing completeness, verified later by reopen persisted verification.
    assert "collapsed_error_badges(" not in after_click
    assert "collapsed_samples >= 2" in after_click
    assert "visible_section_errors(" in after_click


def test_save_that_never_collapses_fails_closed_with_visible_field_errors():
    source = inspect.getsource(save_section)
    after_click = source.split("save.first.click()", maxsplit=1)[1]
    timeout_path = after_click.split("while time.monotonic() < deadline:", 1)[1]

    # The card is still open, so its field errors are read in place; no reopen.
    assert "open_section_for_edit" not in after_click
    assert "errors = visible_section_errors(page, live_path)" in timeout_path
    assert "未恢复 EDIT" in timeout_path
    assert "raise RuntimeError(" in timeout_path


def test_production_photo_path_persists_each_image_in_its_own_transaction():
    # NOTE: AGENTS.md still describes one Save for the staged subset; the runtime
    # uses image-owned Save/verify transactions, which this test pins.
    source = inspect.getsource(run_photos)
    loop = source.split("for index, image in enumerate(resolved):", 1)[1]
    stage = loop.index("adapter.upload_product_photos(")
    save = loop.index("adapter.save_section(PRODUCT_PHOTOS)", stage)
    verify = loop.index("expected_added=1", save)

    assert stage < save < verify
    assert "[str(image)]," in loop[stage:save]
    assert 'report["save_count"] += 1' in loop
    assert 'report["staged"] += 1' in loop
    assert 'report["persisted_this_run"] += 1' in loop
    assert "_reconcile_failed_photo_transaction(" in loop
    assert "expected_added=expected_new" not in source


def test_product_photos_uses_real_thumbnail_ids_and_shared_input_surface():
    surface_source = inspect.getsource(_photo_surface)
    snapshot_source = inspect.getsource(_slot_snapshot)
    next_slot_source = inspect.getsource(_next_empty_photo_slot)
    select_source = inspect.getsource(_select_file_input)
    raw_input_source = inspect.getsource(_raw_file_input)

    # Real thumbnail_N slots are discovered from the live surface (Makro may
    # declare fewer than five), ordered by their numeric index.
    assert '[id^="thumbnail_"]' in surface_source
    assert "/^thumbnail_(\\d+)$/" in snapshot_source
    assert "slots.sort((left, right) => left.index - right.index)" in snapshot_source
    assert "_slot_snapshot(page, section_path)" in next_slot_source
    assert 'snapshot.get("is_empty")' in next_slot_source
    assert "range(5)" not in next_slot_source
    # The single shared file input is resolved on the same photo surface.
    assert 'locator(\'input[type="file"]\')' in raw_input_source
    assert "_next_empty_photo_slot" in select_source
    assert "_DynamicPhotoFileTarget" in select_source
    assert "AddProductImage" not in next_slot_source


def test_dynamic_photo_target_opens_exact_slot_then_clicks_upload_once():
    target_source = inspect.getsource(_DynamicPhotoFileTarget.set_input_files)
    panel_source = inspect.getsource(_open_photo_slot_upload_panel)

    panel_open = target_source.index("_open_photo_slot_upload_panel")
    chooser = target_source.index("expect_file_chooser(timeout=1_500)", panel_open)
    upload_click = target_source.index("upload_button.click(timeout=1_500, force=True)", chooser)
    completion_wait = target_source.index("_wait_for_target_slot_completion", upload_click)

    assert panel_open < chooser < upload_click < completion_wait
    assert "slot.click(timeout=click_timeout_ms, force=True)" in panel_source
    assert "_wait_for_upload_photo_button" in panel_source
    assert 'locator(f"#{self.slot_id}")' in target_source
    assert "role_selector.click()" not in target_source
    assert "set_files" in target_source
    assert "_raw_file_input" in target_source
    assert "shared.set_input_files" in target_source


def test_upload_photo_button_is_exact_visible_enabled_active_role_control():
    source = inspect.getsource(_visible_upload_photo_button)

    assert 'get_by_text("Upload Photo", exact=True)' in source
    assert "is_visible" in source
    assert "is_enabled" in source
    assert "ancestor-or-self::button" in source
    assert "len(visible) > 1" in source


def test_photo_upload_waits_on_target_transaction_evidence_not_page_stability():
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
    assert "_target_slot_acceptance_signal" in completion_source
    # Every target-slot signal (including the slot losing its plus) must stay
    # stable outside Uploading before it counts as acceptance.
    assert '"target_slot_consumed"' in inspect.getsource(_target_slot_acceptance_signal)
    assert "accepted_stability_ms: int = 750" in completion_source
    assert "if stable_ms >= accepted_stability_ms:" in completion_source


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
