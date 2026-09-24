from pathlib import Path

import makro_execute_listing


class FakePage:
    def screenshot(self, **_kwargs):
        raise RuntimeError("synthetic screenshot timeout")


def test_optional_screenshot_failure_is_reported_without_raising(monkeypatch, tmp_path: Path) -> None:
    capture_safe_calls: list[bool] = []
    monkeypatch.setattr(
        makro_execute_listing,
        "set_visual_execution_hud_capture_safe",
        lambda _page, enabled: capture_safe_calls.append(bool(enabled)),
    )

    result = makro_execute_listing._capture_optional_screenshot(
        FakePage(),
        tmp_path / "step3-final.png",
    )

    assert result["status"] == "failed"
    assert result["path"] == ""
    assert result["requested_path"].endswith("step3-final.png")
    assert result["error_type"] == "RuntimeError"
    assert result["error"] == "synthetic screenshot timeout"
    assert "synthetic screenshot timeout" in result["traceback"]
    assert capture_safe_calls == [True, False]
