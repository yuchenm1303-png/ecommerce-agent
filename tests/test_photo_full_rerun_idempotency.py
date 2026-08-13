from __future__ import annotations

from pathlib import Path

from app.makro import execution


class _FakeAdapter:
    def __init__(self) -> None:
        self.cancelled = False

    def find_section(self, _title: str):
        return {"has_edit": True, "path": "#photos", "title": "Product Photos (5/5)"}


def test_full_gallery_rerun_is_already_persisted(monkeypatch, tmp_path: Path) -> None:
    images = []
    for index in range(5):
        path = tmp_path / f"image-{index}.jpg"
        path.write_bytes(b"x")
        images.append(str(path))

    adapter = _FakeAdapter()
    monkeypatch.setattr(
        execution,
        "_fresh_photo_state",
        lambda _adapter: (
            "#photos",
            {
                "completion_count": 5,
                "capacity": 5,
                "add_image_tile_count": 0,
            },
        ),
    )

    def _cancel(_adapter):
        adapter.cancelled = True

    monkeypatch.setattr(execution, "_cancel_open_photo_transaction", _cancel)

    report = execution.run_photos(
        adapter,
        images,
        allow_save=True,
        upload_timeout_ms=1_000,
        run_dir=tmp_path,
    )

    assert report["status"] == "persisted_verified"
    assert report["requested"] == 5
    assert report["already_persisted"] == 5
    assert report["persisted"] == 5
    assert report["attempted"] == 0
    assert report["staged"] == 0
    assert report["save_attempted"] is False
    assert report["saved"] is True
    assert report["restored_collapsed_state"] is True
    assert adapter.cancelled is True
