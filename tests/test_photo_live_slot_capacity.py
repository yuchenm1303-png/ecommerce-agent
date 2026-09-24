from app.makro.execution import _photo_upload_budget
from app.makro.photos import _photo_surface_is_ready, _surface_slot_ids


def _surface_state(*, capacity: int | None, slot_ids: list[str]) -> dict:
    return {
        "found": True,
        "capacity": capacity,
        "slots": [
            {"id": slot_id, "index": int(slot_id.rsplit("_", 1)[1])}
            for slot_id in slot_ids
        ],
    }


def test_four_slot_surface_is_ready_when_makro_declares_four() -> None:
    state = _surface_state(
        capacity=4,
        slot_ids=["thumbnail_0", "thumbnail_1", "thumbnail_2", "thumbnail_3"],
    )

    assert _surface_slot_ids(state) == (
        "thumbnail_0",
        "thumbnail_1",
        "thumbnail_2",
        "thumbnail_3",
    )
    assert _photo_surface_is_ready(state) is True


def test_surface_waits_when_declared_capacity_has_not_rendered_yet() -> None:
    state = _surface_state(
        capacity=5,
        slot_ids=["thumbnail_0", "thumbnail_1", "thumbnail_2", "thumbnail_3"],
    )

    assert _photo_surface_is_ready(state) is False


def test_counterless_surface_uses_stable_observed_slots() -> None:
    state = _surface_state(
        capacity=None,
        slot_ids=["thumbnail_0", "thumbnail_1", "thumbnail_2", "thumbnail_3"],
    )

    assert _photo_surface_is_ready(state) is True


def test_five_requested_images_are_capacity_limited_to_four_live_slots() -> None:
    budget = _photo_upload_budget(
        requested=5,
        initial_count=0,
        capacity=4,
        visible_empty_slots=4,
    )

    assert budget["available_slots"] == 4
    assert budget["upload_count"] == 4
    assert budget["omitted_count"] == 1
