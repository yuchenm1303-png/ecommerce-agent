from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import app.makro.brand_selection as brand_selection
from app.makro.requested_vertical import (
    current_requested_vertical,
    requested_vertical_scope,
)


ROOT = Path(__file__).resolve().parents[1]
ONE_LINK = (ROOT / "makro_one_link.py").read_text(encoding="utf-8")
TRANSITION = (ROOT / "app" / "makro" / "step3_transition.py").read_text(encoding="utf-8")


def test_requested_vertical_scope_is_context_local_and_restores_environment(monkeypatch) -> None:
    monkeypatch.setenv("ECOMMERCE_REQUESTED_VERTICAL", "environment_vertical")
    assert current_requested_vertical() == "environment_vertical"

    with requested_vertical_scope("  exact_vertical  "):
        assert current_requested_vertical() == "exact_vertical"

    assert current_requested_vertical() == "environment_vertical"


def test_diagnostic_brand_override_wins_policy_without_changing_global_mode() -> None:
    hints = SimpleNamespace(brand="Supplier Brand", brand_status="explicit")
    original_mode = brand_selection.BRAND_SELECTION_MODE

    assert brand_selection._brand_terms(
        hints,
        diagnostic_override="  TEST BRAND  ",
    ) == ("TEST BRAND",)
    assert brand_selection.BRAND_SELECTION_MODE == original_mode


def test_direct_one_link_uses_shared_step1_and_canonical_step1_step2_contracts() -> None:
    assert "return prepare_single_step1_page(harness)" in ONE_LINK
    assert "page = prepare_single_step1_page(harness)" in ONE_LINK
    assert "select_vertical(page, provider, hints)" in ONE_LINK
    assert "select_brand_to_product_info(" in ONE_LINK
    assert "diagnostic_brand_override=brand_override" in ONE_LINK
    assert "creation, page = _run_canonical_listing_creation(" in ONE_LINK
    assert "run_listing_creation(" not in ONE_LINK


def test_direct_one_link_adopts_transition_owned_step3_page_before_schema_scan() -> None:
    creation_call = ONE_LINK.index("creation, page = _run_canonical_listing_creation(")
    adapter_call = ONE_LINK.index("adapter = MakroDomainAdapter(page)")
    assert creation_call < adapter_call
    assert "target = parse_makro_listing_url(str(step3_page.url or \"\"))" in ONE_LINK
    assert "return (\n        ListingCreationResult(" in ONE_LINK
    assert ",\n        step3_page,\n    )" in ONE_LINK


def test_step3_transition_forwards_diagnostic_override_into_native_brand_gate() -> None:
    assert "diagnostic_brand_override: str = \"\"" in TRANSITION
    assert "diagnostic_override=diagnostic_brand_override" in TRANSITION
    assert "from .brand_selection import select_brand" in TRANSITION
