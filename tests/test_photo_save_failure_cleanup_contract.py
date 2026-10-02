from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.makro.execution import PRODUCT_PHOTOS, run_photos


class _Page:
    def screenshot(self, **_kwargs) -> None:
        return None

    def locator(self, _path: str):
        return SimpleNamespace(
            inner_text=lambda **_kwargs: "",
            locator=lambda _selector: SimpleNamespace(count=lambda: 0),
        )


class _SaveRejectingPhotoAdapter:
    def __init__(self) -> None:
        self.page = _Page()
        self.expanded = False
        self.cancel_calls = 0

    def find_section(self, title: str):
        assert title == PRODUCT_PHOTOS
        return {"title": title, "path": "#photos", "has_edit": not self.expanded}

    def open_section_for_edit(self, _section) -> None:
        self.expanded = True

    def inspect_product_photos(self):
        return {
            "completion_count": 0,
            "capacity": 5,
            "add_image_tile_count": 5,
            "visible_image_count": 0,
        }

    def upload_product_photos(self, paths: list[str], *, timeout_ms: int):
        # Like the real uploader, each image transaction opens the section itself.
        self.expanded = True
        assert timeout_ms > 0
        return SimpleNamespace(
            as_dict=lambda: {
                "status": "staged",
                "attempted": len(paths),
                "staged": len(paths),
                "items": [{"path": path, "status": "staged"} for path in paths],
                "detail": "all exact slots staged",
            }
        )

    def save_section(self, title: str) -> None:
        assert title == PRODUCT_PHOTOS
        assert self.expanded
        raise RuntimeError("Makro rejected photo Save")

    def visible_section_errors(self, _path: str):
        return ["Save rejected"]

    def cancel_section(self, title: str) -> None:
        assert title == PRODUCT_PHOTOS
        self.cancel_calls += 1
        self.expanded = False

    def cancel_product_photos(self) -> None:
        self.cancel_section(PRODUCT_PHOTOS)


def test_photo_save_failure_discards_the_open_unsaved_transaction(tmp_path: Path) -> None:
    image = tmp_path / "photo.jpg"
    image.write_bytes(b"fixture")
    adapter = _SaveRejectingPhotoAdapter()

    report = run_photos(
        adapter,
        [str(image)],
        allow_save=True,
        upload_timeout_ms=8_000,
        run_dir=tmp_path,
    )

    # Image-owned transactions: the rejected Save is reconciled from Makro's
    # persisted counter; the open unsaved transaction is discarded and nothing
    # is reported as persisted.
    assert report["status"] == "incomplete_upload"
    assert report["saved"] is False
    assert report["persisted_this_run"] == 0
    failure = report["save_failures"][0]
    assert failure["error"] == "Makro rejected photo Save"
    assert failure["recovery"]["cancelled_open_transaction"] is True
    assert failure["recovery"]["status"] == "clean_no_commit"
    assert [item["status"] for item in report["items"]] == ["save_failed"]
    # One Cancel restores the collapsed state after inspection, one discards the
    # rejected image transaction.
    assert adapter.cancel_calls == 2
    assert adapter.expanded is False
