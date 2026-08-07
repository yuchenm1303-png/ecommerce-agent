from __future__ import annotations

import inspect

import makro_fill


def test_makro_fill_cli_imports_and_defaults_to_dry_run():
    args = makro_fill.build_parser().parse_args(["--product", "fixture.xlsx"])

    assert args.dry_run is True
    assert args.source_format == "auto"
    assert args.browser == "edge"


def test_section_title_count_is_not_part_of_identity():
    assert makro_fill._base_section_title("Product Description (14/14)") == "Product Description"
    assert makro_fill._base_section_title("Additional Description (Optional) (0/46)") == "Additional Description (Optional)"


def test_target_section_uses_resolved_answers_only():
    sections = [
        {"title": "Price, Stock and Shipping Information (0/14)"},
        {"title": "Product Description (0/14)"},
    ]
    resolutions = [
        {"status": "missing", "section_heading": "Price, Stock and Shipping Information (0/14)"},
        {"status": "resolved", "section_heading": "Product Description (0/14)"},
    ]

    assert makro_fill._select_target_section(sections, resolutions, None) == "Product Description"


def test_cli_has_no_save_or_submit_action():
    source = inspect.getsource(makro_fill)

    assert "select_option" not in source  # field writes live in makro_dryrun, not ad-hoc CLI code
    assert 'get_by_text("Save"' not in source
    assert 'get_by_text("Send to QC"' not in source
    assert ".save(" not in source
