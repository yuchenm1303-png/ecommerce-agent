from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "gui" / "page_scroll_layout.py").read_text(encoding="utf-8")


def test_offer_row_expands_product_source_instead_of_squeezing_existing_controls() -> None:
    assert "_INPUT_CARD_MIN_HEIGHT = 222" in PAGE
    assert "_INPUT_CARD_MAX_HEIGHT = 238" in PAGE
    assert "_INPUT_CARD_OFFER_MIN_HEIGHT = 278" in PAGE
    assert "_INPUT_CARD_OFFER_MAX_HEIGHT = 296" in PAGE
    assert "def refresh_single_source_layout" in PAGE
    assert 'offer_input = getattr(window, "listing_intent_input", None)' in PAGE
    assert "_INPUT_CARD_OFFER_MIN_HEIGHT if has_offer_row" in PAGE
    assert "_INPUT_CARD_OFFER_MAX_HEIGHT if has_offer_row" in PAGE


def test_product_source_actions_have_distinct_spacing_and_width_hierarchy() -> None:
    assert "url_row.setSpacing(12)" in PAGE
    assert 'getattr(window, "start_button", None), 176, 216' in PAGE
    assert 'getattr(window, "stop_button", None), 62, 76' in PAGE
    assert "stage_row.setSpacing(12)" in PAGE
    assert 'for name in ("step1_button", "step2_button", "step3_button")' in PAGE
    assert 'getattr(window, name, None), 148, 176' in PAGE
    assert "settings_row.setSpacing(14)" in PAGE
    assert 'getattr(window, "source_port", None), 188, 212' in PAGE
    assert 'getattr(window, "vertical_input", None), 210, 250' in PAGE
    assert "offer_row.setSpacing(12)" in PAGE
    assert "summary_row.setSpacing(14)" in PAGE


def test_source_reflow_is_one_shot_startup_presentation_only() -> None:
    assert "QTimer.singleShot(0, lambda: refresh_single_source_layout(window))" in PAGE
    assert "valueChanged.connect" not in PAGE
    assert "ReadOnlyRunner(" not in PAGE
    assert "RealExecutionRunner(" not in PAGE
    assert "AcceptanceConsole(" not in PAGE


def test_source_layout_module_compiles_without_importing_pyside() -> None:
    compile(PAGE, str(ROOT / "gui" / "page_scroll_layout.py"), "exec")
