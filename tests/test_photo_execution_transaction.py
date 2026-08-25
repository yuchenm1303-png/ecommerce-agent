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
    def __init__(self, *, staged: int, initial_count: int = 0, capacity: int = 5) -> None:
        self.page = FakePage()
        self.expanded = False
        self.initial_count = initial_count
        self.persisted_count = initial_count
        self.capacity = capacity
        self.staged_result = staged
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
        assert self.expanded
        assert timeout_ms > 0
        self.upload_calls.append(list(paths))
        staged = min(self.staged_result, len(paths))
        items = [
            {
                "path": path,
                "status": "staged" if index < staged else "upload_error",
                "slot_id": f"thumbnail_{index}",
            }
            for index, path in enumerate(paths)
        ]
        return SimpleNamespace(
            as_dict=lambda: {
                "status": "staged" if staged == len(paths) else "partial_staged",
                "attempted": len(paths),
                "staged": staged,
                "items": items,
                "detail": f"{staged}/{len(paths)} exact slots confirmed",
            }
        )

    def save_section(self, title: str) -> None:
        assert title == PRODUCT_PHOTOS
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


def test_run_photos_delegates_all_staging_to_one_exact_slot_transaction(tmp_path: Path) -> None:
    images = [_image(tmp_path, "a.jpg"), _image(tmp_path, "b.jpg")]
    adapter = FakePhotoAdapter(staged=2)

    report = run_photos(
        adapter,
        images,
        allow_save=True,
        upload_timeout_ms=8_000,
        run_dir=tmp_path,
    )

    assert adapter.upload_calls == [[str(Path(path).resolve()) for path in images]]
    assert adapter.save_calls == 1
    assert report["status"] == "persisted_verified"
    assert report["staged"] == 2
    assert report["persisted_this_run"] == 2
    assert report["save_count"] == 1


def test_run_photos_never_saves_a_partial_exact_slot_transaction(tmp_path: Path) -> None:
    images = [_image(tmp_path, "a.jpg"), _image(tmp_path, "b.jpg")]
    adapter = FakePhotoAdapter(staged=1)

    report = run_photos(
        adapter,
        images,
        allow_save=True,
        upload_timeout_ms=8_000,
        run_dir=tmp_path,
    )

    assert len(adapter.upload_calls) == 1
    assert adapter.save_calls == 0
    assert adapter.cancel_calls == 1
    assert report["staged"] == 1
    assert report["saved"] is False
    assert report["cancelled_unsaved_partial"] is True


def test_run_photos_capacity_is_decided_before_exact_slot_transaction(tmp_path: Path) -> None:
    images = [_image(tmp_path, "a.jpg"), _image(tmp_path, "b.jpg")]
    adapter = FakePhotoAdapter(staged=1, initial_count=4, capacity=5)

    report = run_photos(
        adapter,
        images,
        allow_save=True,
        upload_timeout_ms=8_000,
        run_dir=tmp_path,
    )

    assert adapter.upload_calls == [[str(Path(images[0]).resolve())]]
    assert report["capacity_limited"] is True
    assert report["omitted_count"] == 1
    assert report["status"] == "persisted_verified"
    assert report["final_count"] == 5
