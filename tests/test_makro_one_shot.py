from __future__ import annotations

import inspect

import makro_listing_one_shot
from app.makro.persisted_inspection import (
    _SAVE_SAFE_VALUES,
    _gtin_check_digit,
    _save_candidate_for,
    run_one_shot_persisted_inspection,
)


def test_parser_requires_explicit_persist_switch():
    parser = makro_listing_one_shot.build_parser()
    args = parser.parse_args(["--expected-vertical", "vehicle_camera_system"])
    assert args.persist_test_values is False
    args = parser.parse_args(["--expected-vertical", "vehicle_camera_system", "--persist-test-values"])
    assert args.persist_test_values is True


def test_business_test_values_are_consistent():
    assert int(_SAVE_SAFE_VALUES["mrp"]) > int(_SAVE_SAFE_VALUES["flipkart_selling_price"])
    assert int(_SAVE_SAFE_VALUES["minimum_order_quantity"]) <= int(_SAVE_SAFE_VALUES["max_order_quantity_allowed"])
    assert _SAVE_SAFE_VALUES["listing_status"] == "Inactive"
    sku = _save_candidate_for("sku_id", "20260807-234700")
    assert sku is not None and sku.startswith("COV") and sku.isalnum()


def test_ean_test_value_is_valid_ean13():
    ean = _SAVE_SAFE_VALUES["ean"]
    assert len(ean) == 13
    assert ean.isdigit()
    assert _gtin_check_digit(ean[:-1]) == ean[-1]
    assert ean == "2000000000015"


def test_one_shot_has_no_manual_gate_between_sections():
    source = inspect.getsource(run_one_shot_persisted_inspection)
    assert "for title in CORE_FORM_SECTIONS" in source
    assert "input(" not in source


def test_cli_does_not_click_qc_submission():
    source = inspect.getsource(makro_listing_one_shot.main)
    assert 'get_by_text("Send to QC"' not in source
    assert "74/74" not in source
    assert "78/78" not in source
