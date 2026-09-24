from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.source_capture as source_capture


PRODUCT_URL = "https://detail.1688.com/offer/123456789.html"


def test_source_edge_rotates_once_only_for_cdp_transport_failure(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    captured = SimpleNamespace(cache_hit=False)

    def capture_once(*_args, **_kwargs):
        calls.append("capture")
        if calls.count("capture") == 1:
            raise RuntimeError(
                "长期 Makro Edge CDP http://127.0.0.1:9333 仍可达，但 Playwright attach 连续失败。"
            )
        return captured

    monkeypatch.setattr(source_capture._engine, "capture_product_source", capture_once)
    monkeypatch.setattr(
        source_capture,
        "mark_cdp_poisoned",
        lambda *_a, **_k: {"endpoint_token": "ws://source-old"},
    )
    cleared: list[int] = []
    monkeypatch.setattr(source_capture, "clear_cdp_poison", lambda port: cleared.append(port))
    monkeypatch.setattr(
        source_capture,
        "close_managed_browser",
        lambda **_kwargs: SimpleNamespace(ok=True, detail="", pid=1234),
    )
    launched: list[tuple[object, int, str]] = []
    monkeypatch.setattr(
        source_capture,
        "launch_detached_edge",
        lambda *, profile_dir, port, start_url: launched.append((profile_dir, port, start_url)),
    )
    monkeypatch.setattr(
        source_capture,
        "probe_cdp_automation",
        lambda *_a, **_k: SimpleNamespace(
            automation_ready=True,
            endpoint_token="ws://source-new",
            error="",
            state="AUTOMATION_READY",
        ),
    )

    result = source_capture.capture_product_source(
        PRODUCT_URL,
        output_dir=tmp_path / "out",
        profile_dir=tmp_path / "source-edge",
        cdp_port=9333,
    )

    assert result is captured
    assert calls == ["capture", "capture"]
    assert len(launched) == 1
    assert launched[0][1] == 9333
    assert launched[0][2] == PRODUCT_URL
    assert 9333 in cleared


def test_source_edge_does_not_restart_for_normal_supplier_page_failure(monkeypatch, tmp_path) -> None:
    def capture_once(*_args, **_kwargs):
        raise RuntimeError("page.goto: Timeout 45000ms exceeded while loading supplier page")

    monkeypatch.setattr(source_capture._engine, "capture_product_source", capture_once)
    closed: list[bool] = []
    monkeypatch.setattr(
        source_capture,
        "close_managed_browser",
        lambda **_kwargs: closed.append(True),
    )

    with pytest.raises(RuntimeError, match="page.goto"):
        source_capture.capture_product_source(
            PRODUCT_URL,
            output_dir=tmp_path / "out",
            profile_dir=tmp_path / "source-edge",
            cdp_port=9333,
        )

    assert closed == []
