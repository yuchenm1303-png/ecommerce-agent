from __future__ import annotations

import pytest

import app.makro.step1_entry as step1_entry
import app.makro.vertical_selection as vertical_selection


class FakePage:
    def __init__(self, url: str) -> None:
        self.url = url
        self.timeout = 0

    def set_default_timeout(self, value: int) -> None:
        self.timeout = value


class FakeContext:
    def __init__(self, pages) -> None:
        self.pages = list(pages)


class FakeHarness:
    def __init__(self, pages) -> None:
        self.context = FakeContext(pages)

    def ensure_page(self):
        raise AssertionError("existing listing page should be reused")


def test_single_unique_step2_page_is_handed_to_state_machine(monkeypatch) -> None:
    page = FakePage(
        "https://seller.makro.co.za/#dashboard/addListings/single?vertical=air_purifier"
    )
    harness = FakeHarness([page])
    monkeypatch.setattr(step1_entry, "_wait_until_vertical_operable", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(step1_entry, "is_product_info_step", lambda _page: False)
    monkeypatch.setattr(step1_entry, "is_brand_step", lambda _page: True)

    assert step1_entry.prepare_single_step1_page(harness) is page
    assert page.timeout == 15_000


def test_single_multiple_listing_pages_still_fail_closed(monkeypatch) -> None:
    pages = [
        FakePage("https://seller.makro.co.za/#dashboard/addListings/single?vertical=air_purifier"),
        FakePage("https://seller.makro.co.za/#dashboard/addListings/single?vertical=massager"),
    ]
    harness = FakeHarness(pages)

    with pytest.raises(RuntimeError, match="拒绝猜目标"):
        step1_entry.prepare_single_step1_page(harness)


def test_select_vertical_reads_committed_step2_vertical(monkeypatch) -> None:
    page = FakePage("fake://step2")
    monkeypatch.setattr(vertical_selection, "is_product_info_step", lambda _page: False)
    monkeypatch.setattr(vertical_selection, "is_brand_step", lambda _page: True)
    monkeypatch.setattr(
        vertical_selection,
        "_current_target_values",
        lambda _page: ("air_purifier", ""),
    )
    monkeypatch.setattr(
        vertical_selection,
        "is_vertical_interaction_ready",
        lambda _page: pytest.fail("later stage must not re-open Step 1 controls"),
    )

    assert vertical_selection.select_vertical(page, object(), object()) == "air_purifier"


def test_select_vertical_requires_committed_vertical_on_later_stage(monkeypatch) -> None:
    page = FakePage("fake://step3")
    monkeypatch.setattr(vertical_selection, "is_product_info_step", lambda _page: True)
    monkeypatch.setattr(vertical_selection, "is_brand_step", lambda _page: False)
    monkeypatch.setattr(vertical_selection, "_current_target_values", lambda _page: ("", "Dexmary"))

    with pytest.raises(RuntimeError, match="no committed canonical vertical"):
        vertical_selection.select_vertical(page, object(), object())
