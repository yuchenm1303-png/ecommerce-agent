from __future__ import annotations

from pathlib import Path

from gui.usage_telemetry import _compact_executor_report, _material_usage_payload


def test_material_usage_prefers_actual_persisted_images(tmp_path: Path) -> None:
    image = tmp_path / "customer-photo.jpg"
    image.write_bytes(b"abc")
    report = _compact_executor_report(
        {
            "photo_upload": {
                "requested": 5,
                "attempted": 5,
                "persisted": 5,
                "final_count": 5,
                "status": "persisted_verified",
                "items": [
                    {"index": 1, "path": str(image), "status": "staged", "slot_position": 1}
                ],
                "screenshot_after_save": str(tmp_path / "private.png"),
            }
        }
    )

    usage = _material_usage_payload([], report)

    assert usage["state"] == "used"
    assert usage["evidence"] == "executor_report"
    assert usage["photo_confirmed_saved"] == 5
    assert usage["actual_files"][0]["name"] == "customer-photo.jpg"
    assert usage["actual_files"][0]["size_bytes"] == 3
    assert "path" not in report["photo_upload"]["items"][0]
    assert "screenshot_after_save" not in report["photo_upload"]
    assert str(tmp_path) not in str(report)


def test_empty_gui_snapshot_is_not_treated_as_confirmed_no_materials() -> None:
    usage = _material_usage_payload([], {})

    assert usage["state"] == "unknown"
    assert usage["evidence"] == "none"
    assert usage["selected_file_count"] == 0


def test_executor_zero_is_explicit_none() -> None:
    report = _compact_executor_report(
        {"photo_upload": {"requested": 0, "attempted": 0, "persisted": 0, "final_count": 0}}
    )

    usage = _material_usage_payload([], report)

    assert usage["state"] == "none"
    assert usage["evidence"] == "executor_report"
