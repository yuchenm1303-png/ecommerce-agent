from pathlib import Path


def test_listings_management_is_a_resumable_pre_step1_state() -> None:
    source = Path("app/makro/step1_entry.py").read_text(encoding="utf-8")
    assert '_LISTINGS_MANAGEMENT_ROUTE = "#dashboard/listings-management"' in source
    assert "def _is_listings_management" in source
    assert "def _is_listing_hub" in source
    assert "_is_listings_management(page) or _is_listing_creation(page)" in source


def test_dashboard_transition_accepts_both_current_and_legacy_listing_hubs() -> None:
    source = Path("app/makro/step1_entry.py").read_text(encoding="utf-8")
    assert "def _dashboard_next_state_ready" in source
    assert "_has_exact_action(page, _ADD_NEW_LISTINGS)" in source
    assert "_is_listing_hub(page)" in source
    assert "点击 Listings 后既没有进入 Listings Management，也没有出现 Add New Listings" in source


def test_listings_management_diagnostics_include_singular_add_action() -> None:
    source = Path("app/makro/step1_entry.py").read_text(encoding="utf-8")
    assert '"listings_management": _is_listings_management(page)' in source
    assert '"add_new_listing_action": _has_exact_action(page, _ADD_NEW_LISTING)' in source
    assert '"add_single_listing_action": _has_exact_action(page, _ADD_SINGLE_LISTING)' in source
