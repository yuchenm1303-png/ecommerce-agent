from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.makro.execution import PRODUCT_PHOTOS, run_photos


class FakePage:
    def __init__(self) -> None:
        self.screenshots: list[str] = []

    def screenshot(self, *, path: str, **_kwargs) -> None:
        self.screenshots.append(path)


class FakePhotoAdapter:
    def __init__(
        self,
        *,
        outcomes: list[str],
        initial_count: int = 0,
        capacity: int = 5,
    ) -> None:
        self.page = FakePage()
        self.expanded = False
        self.persisted_count = initial_count
        self.capacity = capacity
        self.outcomes = list(outcomes)
        self.upload_calls: list[list[str]] = []
        self.save_calls = 0
        self.cancel_calls = 0

    def find_section(self, title: str):
        assert title == PRODUCT_PHOTOS
        return {
            "title": PRODUCT_PHOTOS,
            "path": "#product-photos",
            "has_edit": not self.expanded,
        }

    def open_section_for_edit(self, _section) -> None:
        self.expanded = True

    def inspect_product_photos(self):
        return {
            "completion_count": self.persisted_count,
            "capacity": self.capacity,
            "add_image_tile_count": max(0, self.capacity - self.persisted_count),
            "visible_image_count": self.persisted_count,
        }

    def upload_product_photos(self, paths: list[str], *, timeout_ms: int):
        assert timeout_ms > 0
        assert len(paths) == 1
        self.expanded = True
        self.upload_calls.append(list(paths))
        outcome = self.outcomes[len(self.upload_calls) - 1]
        staged = 1 if outcome == "staged" else 0
        return SimpleNamespace(
            as_dict=lambda: {
                "status": outcome,
                "attempted": 1,
                "staged": staged,
                "items": [
                    {
                        "path": paths[0],
                        "status": "staged" if staged else outcome,
                        "slot_id": f"thumbnail_{self.persisted_count}",
                    }
                ],
                "detail": outcome,
            }
        )

    def save_section(self, title: str) -> None:
        assert title == PRODUCT_PHOTOS
        assert self.expanded
        self.save_calls += 1
        self.expanded = False

    def verify_persisted_photo_count(self, *, initial_count: int, expected_added: int):
        self.persisted_count = initial_count + expected_added
        return {
            "status": "persisted_verified",
            "initial_count": initial_count,
            "expected_added": expected_added,
            "final_count": self.persisted_count,
        }

    def cancel_section(self, title: str) -> None:
        assert title == PRODUCT_PHOTOS
        self.cancel_calls += 1
        self.expanded = False


def _image(tmp_path: Path, name: str) -> str:
    path = tmp_path / name
    path.write_bytes(b"fixture")
    return str(path)


def test_run_photos_persists_each_accepted_image_before_starting_the_next(tmp_path: Path) -> None:
    images = [_image(tmp_path, "a.jpg"), _image(tmp_path, "b.jpg")]
    adapter = FakePhotoAdapter(outcomes=["staged", "staged"])

    report = run_photos(
        adapter,
        images,
        allow_save=True,
        upload_timeout_ms=8_000,
        run_dir=tmp_path,
    )

    assert adapter.upload_calls == [[str(Path(path).resolve())] for path in images]
    assert adapter.save_calls == 2
    assert report["status"] == "persisted_verified"
    assert report["staged"] == 2
    assert report["persisted_this_run"] == 2
    assert report["save_count"] == 2
    assert report["final_count"] == 2


def test_later_uncertain_image_cannot_erase_an_earlier_persisted_image(tmp_path: Path) -> None:
    images = [_image(tmp_path, "a.jpg"), _image(tmp_path, "b.jpg")]
    adapter = FakePhotoAdapter(outcomes=["staged", "post_submit_uncertain"])

    report = run_photos(
        adapter,
        images,
        allow_save=True,
        upload_timeout_ms=8_000,
        run_dir=tmp_path,
    )

    assert adapter.upload_calls == [[str(Path(path).resolve())] for path in images]
    assert adapter.save_calls == 1
    assert report["status"] == "persisted_verified"
    assert report["persisted_this_run"] == 1
    assert report["final_count"] == 1
    assert report["saved"] is True
    assert report["request_complete"] is False
    assert str(Path(images[1]).resolve()) in report["cancelled_image_transactions"]


def test_capacity_omission_never_rolls_back_the_last_available_slot(tmp_path: Path) -> None:
    images = [_image(tmp_path, "a.jpg"), _image(tmp_path, "b.jpg")]
    adapter = FakePhotoAdapter(outcomes=["staged"], initial_count=4, capacity=5)

    report = run_photos(
        adapter,
        images,
        allow_save=True,
        upload_timeout_ms=8_000,
        run_dir=tmp_path,
    )

    assert adapter.upload_calls == [[str(Path(images[0]).resolve())]]
    assert adapter.save_calls == 1
    assert report["capacity_limited"] is True
    assert report["omitted_count"] == 1
    assert report["status"] == "persisted_verified"
    assert report["final_count"] == 5
