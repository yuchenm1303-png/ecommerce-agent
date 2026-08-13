from pathlib import Path


def test_batch_sku_spec_ui_is_explicit_and_natural_language() -> None:
    source = Path("gui/batch_sku_spec_ui.py").read_text(encoding="utf-8")
    assert 'QLabel("SKU规格"' in source
    assert 'setPlaceholderText(_SKU_PLACEHOLDER)' in source
    assert '"SKU规格（可选）"' in source
    assert "不要求特殊格式" in source
    assert "每一行独立传给对应 Batch Job" in source


def test_batch_sku_spec_ui_installs_after_listing_offer_support() -> None:
    source = Path("run_local_gui.py").read_text(encoding="utf-8")
    support = source.index("install_listing_offer_support(window)")
    sku_ui = source.index("install_batch_sku_spec_ui(window)")
    hardening = source.index("install_listing_offer_hardening(window)")
    assert support < sku_ui < hardening


def test_legacy_key_field_review_lock_is_released_but_other_review_reasons_are_preserved() -> None:
    source = Path("gui/batch_sku_spec_ui.py").read_text(encoding="utf-8")
    assert "_release_legacy_required_reviews" in source
    assert 'detail != "需确认关键字段"' in source
    assert 'error.startswith("关键必填待确认：")' in source
    assert 'job.status = "READY"' in source
    assert "其他真正的 REVIEW" in source
