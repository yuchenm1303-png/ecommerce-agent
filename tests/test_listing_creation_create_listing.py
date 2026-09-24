from __future__ import annotations

import pytest

import app.makro.listing_creation as listing_creation


class FakePage:
    def __init__(self, phase: str) -> None:
        self.phase = phase

    def wait_for_timeout(self, _milliseconds: int) -> None:
        return None


class FakeButton:
    def __init__(self, callback) -> None:
        self.callback = callback
        self.clicks = 0

    def click(self, **_kwargs) -> None:
        self.clicks += 1
        self.callback()


def _install_brand_state(monkeypatch, page: FakePage, *, create_button=True, create_brand="Unbranded"):
    confirm = FakeButton(lambda: setattr(page, "phase", "create"))
    create = FakeButton(lambda: setattr(page, "phase", "product")) if create_button else None

    monkeypatch.setattr(
        listing_creation,
        "is_product_info_step",
        lambda current: current.phase == "product",
    )
    monkeypatch.setattr(
        listing_creation,
        "_brand_confirmation_content",
        lambda current: current.phase == "confirmation",
    )
    monkeypatch.setattr(
        listing_creation,
        "_brand_confirmation_button",
        lambda current: confirm if current.phase == "confirmation" else None,
    )
    monkeypatch.setattr(
        listing_creation,
        "_create_new_listing_content",
        lambda current: current.phase == "create",
    )
    monkeypatch.setattr(
        listing_creation,
        "_create_new_listing_button",
        lambda current: create if current.phase == "create" else None,
    )

    def body(current):
        if current.phase == "confirmation":
            return "Selected Brand Unbranded\nConfirm Brand"
        if current.phase == "create":
            return f"{create_brand}\nYou can start selling under this brand.\nCreate New Listing"
        if current.phase == "product":
            return "ADD PRODUCT INFO\nProduct Photos\nPrice, Stock and Shipping Information"
        return ""

    monkeypatch.setattr(listing_creation, "_body_text", body)
    return confirm, create


def test_brand_confirmation_then_create_new_listing_reaches_step3(monkeypatch):
    page = FakePage("confirmation")
    confirm, create = _install_brand_state(monkeypatch, page)

    listing_creation._advance_brand_confirmation(page, "Unbranded")

    assert confirm.clicks == 1
    assert create is not None and create.clicks == 1
    assert page.phase == "product"


def test_brand_can_land_directly_on_create_new_listing_state(monkeypatch):
    page = FakePage("create")
    confirm, create = _install_brand_state(monkeypatch, page)

    listing_creation._advance_brand_confirmation(page, "Unbranded")

    assert confirm.clicks == 0
    assert create is not None and create.clicks == 1
    assert page.phase == "product"


def test_create_new_listing_state_rejects_wrong_brand(monkeypatch):
    page = FakePage("create")
    _confirm, create = _install_brand_state(monkeypatch, page, create_brand="Samsung")

    with pytest.raises(RuntimeError, match="create-listing confirmation mismatch"):
        listing_creation._advance_brand_confirmation(page, "Unbranded")

    assert create is not None and create.clicks == 0


def test_create_new_listing_state_requires_exact_button(monkeypatch):
    page = FakePage("create")
    _confirm, _create = _install_brand_state(monkeypatch, page, create_button=False)

    with pytest.raises(RuntimeError, match="exact Create New Listing button was not found"):
        listing_creation._advance_brand_confirmation(page, "Unbranded")
