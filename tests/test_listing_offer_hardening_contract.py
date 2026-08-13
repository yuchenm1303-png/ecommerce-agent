from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_offer_hardening_sources_compile() -> None:
    for relative in ("gui/listing_offer_hardening.py", "run_local_gui.py"):
        source = _source(relative)
        compile(source, str(ROOT / relative), "exec")


def test_hardening_is_installed_after_main_offer_support() -> None:
    source = _source("run_local_gui.py")
    assert source.index("install_listing_offer_support(window)") < source.index(
        "install_listing_offer_hardening(window)"
    )


def test_same_supplier_url_can_be_two_job_owned_offer_scopes() -> None:
    source = _source("gui/listing_offer_hardening.py")

    assert "_batch_entries" in source
    assert "_listing_offer_pending_intents" in source
    assert "_listing_offer_intent_by_job_id" in source
    assert "same supplier page + Black, same supplier page + White" in source
    assert "normalize_batch_urls" not in source


def test_single_offer_edit_invalidates_prepared_real_execution() -> None:
    source = _source("gui/listing_offer_hardening.py")

    assert "_prepared_single_intent" in source
    assert "current != self._prepared_single_intent" in source
    assert "real_start_button.setEnabled(False)" in source
    assert "当前 Fill Plan 已失效" in source
    assert "重新运行 Step 3 或完整准备" in source


def test_hardening_keeps_offer_scope_separate_from_seller_sku() -> None:
    source = _source("gui/listing_offer_hardening.py")

    assert "not the Makro seller SKU identifier" in source
    assert "makro_execute_listing.py" not in source
    assert "Send to QC" not in source
