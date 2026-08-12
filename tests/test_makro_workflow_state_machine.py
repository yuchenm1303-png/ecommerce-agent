from __future__ import annotations

import json

import pytest

import makro_gui_workflow as workflow


class FakePage:
    def __init__(self, stage: str, *, vertical: str = "", brand: str = "") -> None:
        self.stage = stage
        self.vertical = vertical
        self.brand = brand
        self.url = f"fake://{stage}"


def _patch_stage_detector(monkeypatch) -> None:
    monkeypatch.setattr(workflow, "is_product_info_step", lambda page: page.stage == "step3")
    monkeypatch.setattr(workflow, "is_brand_step", lambda page: page.stage == "step2")
    monkeypatch.setattr(workflow, "_target_values", lambda page: (page.vertical, page.brand))
    monkeypatch.setattr(workflow, "dismiss_joyride_overlay", lambda _page: False)


def test_state_machine_advances_only_missing_stages(monkeypatch, tmp_path) -> None:
    _patch_stage_detector(monkeypatch)
    page = FakePage("home")
    calls: list[str] = []
    phases: list[tuple[str, str, str]] = []

    def prepare_step1():
        calls.append("prepare")
        page.stage = "step1"
        return page

    def select_vertical(current, _provider, _hints):
        calls.append("vertical")
        current.vertical = "air_purifier"
        current.stage = "step2"
        return current.vertical

    def select_brand(current, _provider, _hints):
        calls.append("brand")
        current.brand = "Dexmary"
        current.stage = "step3"
        return current.brand, current

    monkeypatch.setattr(workflow, "select_vertical", select_vertical)
    monkeypatch.setattr(workflow, "select_brand_to_product_info", select_brand)
    monkeypatch.setattr(workflow, "_phase", lambda n, s, d="": phases.append((n, s, d)))

    manifest = {}
    manifest_path = tmp_path / "manifest.json"
    result_page, vertical, brand = workflow._advance_listing_to_step3(
        page=page,
        prepare_step1=prepare_step1,
        provider=object(),
        hints=object(),
        manifest=manifest,
        manifest_path=manifest_path,
        allow_initial_later_stage=False,
    )

    assert result_page is page
    assert (vertical, brand) == ("air_purifier", "Dexmary")
    assert calls == ["prepare", "vertical", "brand"]
    assert manifest["status"] == "step2_complete"
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["brand"] == "Dexmary"
    assert [(name, state) for name, state, _ in phases] == [
        ("step1", "START"),
        ("step1", "COMPLETE"),
        ("step2", "START"),
        ("step2", "COMPLETE"),
    ]


def test_state_machine_resumes_step2_without_repeating_vertical(monkeypatch, tmp_path) -> None:
    _patch_stage_detector(monkeypatch)
    page = FakePage("step2", vertical="air_purifier")
    calls: list[str] = []

    monkeypatch.setattr(
        workflow,
        "select_vertical",
        lambda *_args, **_kwargs: pytest.fail("Step 1 must not rerun from Step 2"),
    )

    def select_brand(current, _provider, _hints):
        calls.append("brand")
        current.brand = "Dexmary"
        current.stage = "step3"
        return current.brand, current

    monkeypatch.setattr(workflow, "select_brand_to_product_info", select_brand)
    monkeypatch.setattr(workflow, "_phase", lambda *_args, **_kwargs: None)

    _, vertical, brand = workflow._advance_listing_to_step3(
        page=page,
        prepare_step1=lambda: pytest.fail("pre-Step1 must not rerun from Step 2"),
        provider=object(),
        hints=object(),
        manifest={},
        manifest_path=tmp_path / "manifest.json",
        allow_initial_later_stage=True,
    )

    assert (vertical, brand) == ("air_purifier", "Dexmary")
    assert calls == ["brand"]


def test_state_machine_resumes_step3_without_repeating_step1_or_step2(monkeypatch, tmp_path) -> None:
    _patch_stage_detector(monkeypatch)
    page = FakePage("step3", vertical="air_purifier", brand="Dexmary")
    monkeypatch.setattr(workflow, "_phase", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        workflow,
        "select_vertical",
        lambda *_args, **_kwargs: pytest.fail("Step 1 must not rerun from Step 3"),
    )
    monkeypatch.setattr(
        workflow,
        "select_brand_to_product_info",
        lambda *_args, **_kwargs: pytest.fail("Step 2 must not rerun from Step 3"),
    )

    _, vertical, brand = workflow._advance_listing_to_step3(
        page=page,
        prepare_step1=lambda: pytest.fail("pre-Step1 must not rerun from Step 3"),
        provider=object(),
        hints=object(),
        manifest={},
        manifest_path=tmp_path / "manifest.json",
        allow_initial_later_stage=True,
    )

    assert (vertical, brand) == ("air_purifier", "Dexmary")


def test_state_machine_refuses_unverified_later_draft(monkeypatch, tmp_path) -> None:
    _patch_stage_detector(monkeypatch)
    page = FakePage("step2", vertical="air_purifier")

    with pytest.raises(RuntimeError, match="no verified same-task"):
        workflow._advance_listing_to_step3(
            page=page,
            prepare_step1=lambda: page,
            provider=object(),
            hints=object(),
            manifest={},
            manifest_path=tmp_path / "manifest.json",
            allow_initial_later_stage=False,
        )
