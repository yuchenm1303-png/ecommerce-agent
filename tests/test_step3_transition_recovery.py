from __future__ import annotations

from pathlib import Path

import pytest

import app.makro.step3_transition as transition


ROOT = Path(__file__).resolve().parents[1]
GUI_WORKFLOW = (ROOT / "makro_gui_workflow.py").read_text(encoding="utf-8")
BATCH_JOB = (ROOT / "makro_batch_job.py").read_text(encoding="utf-8")
TRANSITION_SOURCE = (ROOT / "app" / "makro" / "step3_transition.py").read_text(encoding="utf-8")
INTERRUPTION_SOURCE = (ROOT / "app" / "makro" / "portal_interruptions.py").read_text(encoding="utf-8")


class FakeContext:
    def __init__(self) -> None:
        self.pages: list[FakePage] = []


class FakePage:
    def __init__(self, context: FakeContext, phase: str, *, brand: str = "Unbranded") -> None:
        self.context = context
        self.phase = phase
        self.closed = False
        self.timeout = 0
        self.wait_calls = 0
        self.url = (
            "https://seller.makro.co.za/index.html#dashboard/addListings/single"
            f"?vertical=cases_covers&brand={brand}"
        )
        context.pages.append(self)

    def is_closed(self) -> bool:
        return self.closed

    def set_default_timeout(self, value: int) -> None:
        self.timeout = value

    def wait_for_timeout(self, _milliseconds: int) -> None:
        self.wait_calls += 1


def _install_basics(monkeypatch) -> None:
    monkeypatch.setattr(transition, "dismiss_joyride_overlay", lambda _page: False)
    monkeypatch.setattr(
        transition,
        "is_product_info_step",
        lambda page: page.phase == "product",
    )


def test_same_page_step3_that_settles_after_old_timeout_is_recovered(monkeypatch):
    context = FakeContext()
    page = FakePage(context, "brand")
    _install_basics(monkeypatch)

    def select_brand(_page, _provider, _hints, *, wait_ms):
        assert wait_ms == 900
        raise RuntimeError(transition._CREATE_LISTING_TIMEOUT_ERROR)

    monkeypatch.setattr(transition, "select_brand", select_brand)

    original_wait = page.wait_for_timeout

    def settle(milliseconds: int) -> None:
        original_wait(milliseconds)
        page.phase = "product"

    page.wait_for_timeout = settle
    brand, step3_page = transition.select_brand_to_product_info(
        page, object(), object(), recovery_timeout_s=0.2
    )

    assert brand == "Unbranded"
    assert step3_page is page
    assert page.timeout == 15_000


def test_new_step3_target_is_handed_off(monkeypatch):
    context = FakeContext()
    origin = FakePage(context, "brand")
    _install_basics(monkeypatch)
    created: list[FakePage] = []

    def select_brand(_page, _provider, _hints, *, wait_ms):
        created.append(FakePage(context, "product"))
        raise RuntimeError(transition._CREATE_LISTING_TIMEOUT_ERROR)

    monkeypatch.setattr(transition, "select_brand", select_brand)
    brand, step3_page = transition.select_brand_to_product_info(
        origin, object(), object(), recovery_timeout_s=0.2
    )

    assert step3_page is created[0]
    assert brand == "Unbranded"


def test_preexisting_other_job_step3_page_is_never_adopted(monkeypatch):
    context = FakeContext()
    origin = FakePage(context, "brand")
    old_other_job = FakePage(context, "product", brand="OtherBrand")
    _install_basics(monkeypatch)
    created: list[FakePage] = []

    def select_brand(_page, _provider, _hints, *, wait_ms):
        created.append(FakePage(context, "product"))
        raise RuntimeError(transition._CREATE_LISTING_TIMEOUT_ERROR)

    monkeypatch.setattr(transition, "select_brand", select_brand)
    brand, step3_page = transition.select_brand_to_product_info(
        origin, object(), object(), recovery_timeout_s=0.2
    )

    assert step3_page is created[0]
    assert step3_page is not old_other_job
    assert brand == "Unbranded"


def test_multiple_new_step3_pages_fail_closed(monkeypatch):
    context = FakeContext()
    origin = FakePage(context, "brand")
    _install_basics(monkeypatch)

    def select_brand(_page, _provider, _hints, *, wait_ms):
        FakePage(context, "product", brand="One")
        FakePage(context, "product", brand="Two")
        raise RuntimeError(transition._CREATE_LISTING_TIMEOUT_ERROR)

    monkeypatch.setattr(transition, "select_brand", select_brand)

    with pytest.raises(RuntimeError, match="multiple new Step 3 pages"):
        transition.select_brand_to_product_info(
            origin, object(), object(), recovery_timeout_s=0.2
        )


def test_portal_interruption_handling_never_force_clicks_business_controls() -> None:
    assert "joyride-overlay" in INTERRUPTION_SOURCE
    assert 'page.keyboard.press("Escape")' in INTERRUPTION_SOURCE
    assert 'data-action="skip"' in INTERRUPTION_SOURCE
    assert 'data-action="close"' in INTERRUPTION_SOURCE
    assert "force=True" not in INTERRUPTION_SOURCE
    assert "business controls were not force-clicked" in INTERRUPTION_SOURCE
    assert "reconcile_portal_interruptions" in TRANSITION_SOURCE


def test_formal_single_and_batch_use_exact_recovered_step3_page() -> None:
    assert "brand, page = select_brand_to_product_info(page, provider, hints)" in GUI_WORKFLOW
    assert "harness.page = page" in GUI_WORKFLOW
    assert "dismiss_joyride_overlay(page)" in GUI_WORKFLOW

    assert "brand, page = select_brand_to_product_info(page, provider, hints)" in BATCH_JOB
    assert "harness.page = page" in BATCH_JOB
    assert 'manifest["makro_target_id"] = page_target_id(page)' in BATCH_JOB
    assert "dismiss_joyride_overlay(page)" in BATCH_JOB
