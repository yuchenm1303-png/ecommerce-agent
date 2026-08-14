from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DETAIL = (ROOT / "gui" / "listing_intent_detail.py").read_text(encoding="utf-8")
TOP = (ROOT / "gui" / "single_top_compact.py").read_text(encoding="utf-8")
OFFER = (ROOT / "gui" / "listing_offer_support.py").read_text(encoding="utf-8")


def test_listing_intent_detail_sources_compile() -> None:
    compile(DETAIL, str(ROOT / "gui" / "listing_intent_detail.py"), "exec")
    compile(TOP, str(ROOT / "gui" / "single_top_compact.py"), "exec")


def test_detail_editor_reuses_the_existing_intent_contract() -> None:
    assert "_INTENT_LIMIT = 600" in OFFER
    assert "from .listing_offer_support import _INTENT_LIMIT, _clean_intent" in DETAIL
    assert "self.line.setMaxLength(_INTENT_LIMIT)" in DETAIL
    assert "canonical = _clean_intent(raw)" in DETAIL
    assert "self.line.setText(canonical)" in DETAIL
    assert "second AI prompt" in DETAIL


def test_detail_editor_is_explicitly_expandable_and_collapsible() -> None:
    assert 'QPushButton("详情", card)' in DETAIL
    assert 'self.button.setText("收起")' in DETAIL
    assert 'self.button.setText("详情")' in DETAIL
    assert "self.host.hide()" in DETAIL
    assert "self.host.show()" in DETAIL
    assert "QPlainTextEdit" in DETAIL


def test_single_top_geometry_expands_only_on_demand() -> None:
    assert "_INTENT_DETAIL_EXTRA = 112" in TOP
    assert "def set_single_top_detail_expanded" in TOP
    assert "_TOP_CARD_MIN + extra" in TOP
    assert "_TOP_CARD_MAX + extra" in TOP
    assert "install_listing_intent_detail" in TOP
    assert "on_expanded=lambda expanded: set_single_top_detail_expanded(window, expanded)" in TOP
