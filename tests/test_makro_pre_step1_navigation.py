from __future__ import annotations

import app.makro.step1_entry as step1_entry


class FakeElement:
    def __init__(self, page: "FakePortalPage", label: str) -> None:
        self.page = page
        self.label = label

    def is_visible(self) -> bool:
        return True

    def click(self, timeout: int = 0) -> None:
        assert timeout == 5_000
        self.page.clicks.append(self.label)
        transitions = {
            ("dashboard", "Listings"): "dashboard_menu",
            ("dashboard_menu", "Add New Listings"): "listing_creation",
            ("listing_creation", "Add New Listing"): "listing_creation_menu",
            ("listing_creation_menu", "Add Single Listing"): "step1",
        }
        self.page.state = transitions[(self.page.state, self.label)]
        if self.page.state == "step1":
            self.page.url = "https://seller.makro.co.za/#dashboard/addListings/single"


class FakeLocator:
    def __init__(self, items=None) -> None:
        self.items = list(items or [])

    def count(self) -> int:
        return len(self.items)

    def nth(self, index: int):
        return self.items[index]


class FakePortalPage:
    def __init__(self, state: str, *, url: str = "https://seller.makro.co.za/#dashboard/home-page") -> None:
        self.state = state
        self.url = url
        self.clicks: list[str] = []
        self.gotos: list[str] = []
        self.default_timeout = 0

    def set_default_timeout(self, value: int) -> None:
        self.default_timeout = value

    def wait_for_timeout(self, _milliseconds: int) -> None:
        return None

    def goto(self, url: str, **_kwargs) -> None:
        self.gotos.append(url)
        self.url = "https://seller.makro.co.za/#dashboard/home-page"
        self.state = "dashboard"

    def locator(self, selector: str):
        assert selector == 'input[type="password"]'
        return FakeLocator()

    def _roles(self) -> dict[str, str]:
        if self.state == "dashboard":
            return {"Listings": "link"}
        if self.state == "dashboard_menu":
            return {"Listings": "link", "Add New Listings": "link"}
        if self.state == "listing_creation":
            return {"Add New Listing": "button"}
        if self.state == "listing_creation_menu":
            return {"Add New Listing": "button", "Add Single Listing": "link"}
        return {}

    def get_by_role(self, role: str, *, name: str, exact: bool):
        assert exact is True
        actual_role = self._roles().get(name)
        return FakeLocator([FakeElement(self, name)] if actual_role == role else [])

    def get_by_text(self, text: str, *, exact: bool):
        assert exact is True
        labels = set()
        if self.state in {"dashboard", "dashboard_menu"}:
            labels.add("Your Dashboard")
        if self.state in {"listing_creation", "listing_creation_menu"}:
            labels.add("Listing Creation")
        return FakeLocator([FakeElement(self, text)] if text in labels else [])


def _install_stage_fakes(monkeypatch) -> None:
    monkeypatch.setattr(step1_entry, "is_vertical_interaction_ready", lambda page: page.state == "step1")
    monkeypatch.setattr(step1_entry, "is_brand_step", lambda _page: False)
    monkeypatch.setattr(step1_entry, "is_product_info_step", lambda _page: False)


def test_dashboard_walks_real_menu_chain_to_step1(monkeypatch) -> None:
    _install_stage_fakes(monkeypatch)
    page = FakePortalPage("dashboard")
    step1_entry._prepare_new_listing_step1_page(page)
    assert page.default_timeout == 15_000
    assert page.gotos == []
    assert page.clicks == ["Listings", "Add New Listings", "Add New Listing", "Add Single Listing"]
    assert page.state == "step1"


def test_listing_creation_resumes_without_revisiting_dashboard(monkeypatch) -> None:
    _install_stage_fakes(monkeypatch)
    page = FakePortalPage("listing_creation", url="https://seller.makro.co.za/#dashboard/addListings")
    step1_entry._prepare_new_listing_step1_page(page)
    assert page.gotos == []
    assert page.clicks == ["Add New Listing", "Add Single Listing"]
    assert page.state == "step1"


def test_blank_owned_tab_normalizes_to_home_then_uses_same_ui_chain(monkeypatch) -> None:
    _install_stage_fakes(monkeypatch)
    page = FakePortalPage("blank", url="about:blank")
    step1_entry._prepare_new_listing_step1_page(page)
    assert page.gotos == [step1_entry.MAKRO_HOME_URL]
    assert page.clicks == ["Listings", "Add New Listings", "Add New Listing", "Add Single Listing"]
    assert page.state == "step1"


def test_pre_step1_navigation_refuses_ambiguous_portal_action(monkeypatch) -> None:
    _install_stage_fakes(monkeypatch)
    page = FakePortalPage("dashboard")
    original = page.get_by_role

    def ambiguous(role: str, *, name: str, exact: bool):
        if role == "link" and name == "Listings":
            return FakeLocator([FakeElement(page, name), FakeElement(page, name)])
        return original(role, name=name, exact=exact)

    page.get_by_role = ambiguous  # type: ignore[method-assign]
    try:
        step1_entry._prepare_new_listing_step1_page(page)
    except RuntimeError as exc:
        assert "refusing to guess" in str(exc)
    else:
        raise AssertionError("ambiguous Listings target must fail closed")
