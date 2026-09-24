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
        assert self.expanded
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

    assert report["status"] == "save_failed"
    assert report["saved"] is False
    assert report["cancelled_unsaved_after_failure"] is True
    assert adapter.cancel_calls == 1
    assert adapter.expanded is False
