from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DETAIL = (ROOT / "gui" / "listing_intent_detail.py").read_text(encoding="utf-8")
COMPOSER = (ROOT / "gui" / "workspace_composer.py").read_text(encoding="utf-8")
OFFER = (ROOT / "gui" / "listing_offer_support.py").read_text(encoding="utf-8")


def test_listing_intent_detail_sources_compile() -> None:
    compile(DETAIL, str(ROOT / "gui" / "listing_intent_detail.py"), "exec")
    compile(COMPOSER, str(ROOT / "gui" / "workspace_composer.py"), "exec")


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


def test_detail_editor_expands_inside_the_composed_product_card() -> None:
    # The composer installs the editor before re-laying out the product card and
    # places its host directly beneath the intent row; expanding it only re-commits
    # glass geometry, the card itself absorbs the height through its stretch.
    assert "install_listing_intent_detail(self.window, on_expanded=self._intent_detail_toggled)" in COMPOSER
    assert "def _intent_detail_toggled" in COMPOSER
    assert "layout.addWidget(detail_host)" in COMPOSER
    assert "host_layout.setContentsMargins(0, 0, 0, 0)" in COMPOSER
