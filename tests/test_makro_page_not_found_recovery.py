from __future__ import annotations

import pytest

import app.makro.step1_entry as step1_entry


class FakePage:
    def __init__(self) -> None:
        self.state = "dashboard"
        self.url = "https://seller.makro.co.za/index.html#dashboard/home-page"
        self.gotos: list[str] = []
        self.default_timeout = 0

    def set_default_timeout(self, value: int) -> None:
        self.default_timeout = value

    def wait_for_timeout(self, _milliseconds: int) -> None:
        return None

    def goto(self, url: str, **_kwargs) -> None:
        self.gotos.append(url)
        self.url = "https://seller.makro.co.za/index.html#dashboard/home-page"
        self.state = "dashboard"


def _install_simple_stage_fakes(monkeypatch) -> None:
    monkeypatch.setattr(step1_entry, "_has_password", lambda _page: False)
    monkeypatch.setattr(step1_entry, "_is_dashboard", lambda page: page.state == "dashboard")
    monkeypatch.setattr(step1_entry, "_is_listing_creation", lambda page: page.state == "listing_creation")
    monkeypatch.setattr(step1_entry, "_is_step1_operable", lambda page: page.state == "step1")
    monkeypatch.setattr(step1_entry, "_reject_later_listing_stage", lambda _page: None)

    def open_step1(page) -> None:
        page.state = "step1"
        page.url = "https://seller.makro.co.za/index.html#dashboard/addListings/single"

    monkeypatch.setattr(step1_entry, "_open_step1_from_listing_creation", open_step1)


def test_pre_step1_recovers_once_from_page_not_found_and_continues(monkeypatch) -> None:
    _install_simple_stage_fakes(monkeypatch)
    page = FakePage()
    dashboard_attempts = 0

    def open_dashboard(current) -> None:
        nonlocal dashboard_attempts
        dashboard_attempts += 1
        if dashboard_attempts == 1:
            current.state = "page_not_found"
            current.url = "https://seller.makro.co.za/index.html#dashboard/page-not-found"
            raise step1_entry._PortalPageNotFound("transient SPA route failure")
        current.state = "listing_creation"
        current.url = "https://seller.makro.co.za/index.html#dashboard/addListings"

    monkeypatch.setattr(step1_entry, "_open_listing_creation_from_dashboard", open_dashboard)

    step1_entry._prepare_new_listing_step1_page(page)

    assert dashboard_attempts == 2
    assert page.gotos == [step1_entry.MAKRO_HOME_URL]
    assert page.state == "step1"


def test_pre_step1_page_not_found_recovery_is_bounded(monkeypatch) -> None:
    _install_simple_stage_fakes(monkeypatch)
    page = FakePage()
    dashboard_attempts = 0

    def always_fail(current) -> None:
        nonlocal dashboard_attempts
        dashboard_attempts += 1
        current.state = "page_not_found"
        current.url = "https://seller.makro.co.za/index.html#dashboard/page-not-found"
        raise step1_entry._PortalPageNotFound("persistent SPA route failure")

    monkeypatch.setattr(step1_entry, "_open_listing_creation_from_dashboard", always_fail)
    monkeypatch.setattr(step1_entry, "_diagnostics", lambda _page: "{}")

    with pytest.raises(RuntimeError, match="page-not-found"):
        step1_entry._prepare_new_listing_step1_page(page)

    assert dashboard_attempts == step1_entry._MAX_PAGE_NOT_FOUND_RECOVERIES + 1
    assert page.gotos == [step1_entry.MAKRO_HOME_URL] * step1_entry._MAX_PAGE_NOT_FOUND_RECOVERIES
