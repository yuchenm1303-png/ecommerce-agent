from __future__ import annotations

import inspect

from app.makro.photos import (
    _DynamicPhotoFileTarget,
    _photo_surface_is_ready,
    _select_file_input,
    _wait_for_photo_surface_ready,
    inspect_product_photos,
)

# Slots are discovered live; a "Product Photos (0/5)" gallery renders these ids.
LIVE_SLOT_IDS = tuple(f"thumbnail_{index}" for index in range(5))


class FakeWaitPage:
    def __init__(self) -> None:
        self.waits: list[int] = []

    def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(ms)


def _state(slot_ids: list[str], *, capacity: int | None = 5) -> dict:
    return {
        "found": True,
        "slots": [
            {
                "id": slot_id,
                "index": int(slot_id.rsplit("_", 1)[1]),
                "has_plus": True,
                "has_check": False,
                "image_sources": [],
            }
            for slot_id in slot_ids
        ],
        "slot_count": len(slot_ids),
        "completion_count": 0,
        "capacity": capacity,
        "empty_slot_ids": list(slot_ids),
        "file_inputs": [],
        "uploading": False,
    }


def test_photo_surface_requires_every_declared_live_slot():
    # Readiness follows Makro's declared live capacity, not a fixed five-slot role list.
    assert not _photo_surface_is_ready(_state(list(LIVE_SLOT_IDS[:4])))
    assert _photo_surface_is_ready(_state(list(LIVE_SLOT_IDS)))
    assert _photo_surface_is_ready(_state(list(LIVE_SLOT_IDS[:4]), capacity=4))
    assert not _photo_surface_is_ready(_state([], capacity=5))


def test_photo_surface_waits_for_two_complete_stable_snapshots(monkeypatch):
    page = FakeWaitPage()
    states = iter(
        [
            _state(list(LIVE_SLOT_IDS[:2])),
            _state(list(LIVE_SLOT_IDS)),
            _state(list(LIVE_SLOT_IDS)),
        ]
    )
    monkeypatch.setattr(
        "app.makro.photos.find_section",
        lambda *_args: {"path": "#live-photos", "has_edit": False, "title": "Product Photos (0/5)"},
    )
    monkeypatch.setattr("app.makro.photos._photo_state", lambda *_args: next(states))

    path, state = _wait_for_photo_surface_ready(page, "#old-photos", timeout_ms=1_000)

    assert path == "#live-photos"
    assert state["surface_ready"] is True
    assert state["surface_stable_samples"] == 2
    assert page.waits == [100, 100]


def test_expanded_inspection_uses_surface_readiness_gate(monkeypatch):
    page = FakeWaitPage()
    ready = _state(list(LIVE_SLOT_IDS))
    ready["surface_ready"] = True
    calls: list[tuple[str, int]] = []

    monkeypatch.setattr(
        "app.makro.photos.find_section",
        lambda *_args: {"path": "#photos", "has_edit": False, "title": "Product Photos (0/5)"},
    )

    def fake_wait(_page, path, *, timeout_ms):
        calls.append((path, timeout_ms))
        return "#photos-live", dict(ready)

    monkeypatch.setattr("app.makro.photos._wait_for_photo_surface_ready", fake_wait)

    state = inspect_product_photos(page)

    assert calls == [("#photos", 8_000)]
    assert state["section_path"] == "#photos-live"
    assert state["expanded"] is True


def test_upload_timeout_is_owned_by_each_exact_slot_transaction(monkeypatch):
    monkeypatch.setattr(
        "app.makro.photos._next_empty_photo_slot",
        lambda *_args, **_kwargs: ("thumbnail_0", object()),
    )

    target = _select_file_input(object(), "#photos", timeout_ms=23_456)

    assert isinstance(target, _DynamicPhotoFileTarget)
    assert target.timeout_ms == 23_456

    source = inspect.getsource(_DynamicPhotoFileTarget.set_input_files)
    assert "timeout_ms=self.timeout_ms" in source
    assert "soft_timeout_ms=self.timeout_ms" in source
    assert "uploading_timeout_ms=max(self.timeout_ms, 60_000)" in source
