from __future__ import annotations

import makro_gui_workflow
import makro_one_link
from app.workflow_cli import (
    build_one_link_parser,
    build_staged_workflow_parser,
    provider_config,
)


def _parser_contract(parser):
    rows = []
    for action in parser._actions:
        if action.dest == "help":
            continue
        rows.append(
            (
                action.dest,
                tuple(action.option_strings),
                bool(getattr(action, "required", False)),
                action.default,
                tuple(action.choices) if action.choices is not None else None,
                action.nargs,
            )
        )
    return rows


def test_one_link_cli_delegates_parser_and_provider_config_to_shared_owner() -> None:
    assert makro_one_link.build_parser is build_one_link_parser
    assert makro_one_link._provider_config is provider_config


def test_shared_staged_parser_is_behavior_equivalent_to_existing_gui_cli() -> None:
    assert _parser_contract(build_staged_workflow_parser()) == _parser_contract(makro_gui_workflow.build_parser())


def test_batch_no_longer_borrows_parser_or_provider_config_from_one_link_cli() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "makro_batch_job.py").read_text(encoding="utf-8")
    assert "from app.workflow_cli import build_staged_workflow_parser, provider_config" in source
    assert "from makro_one_link import _provider_config" not in source
    assert "build_semantic_provider(provider_config(args))" in source


def test_product_pack_uses_shared_parser_and_provider_config_owner() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "makro_product_pack_workflow.py").read_text(encoding="utf-8")
    assert "from app.workflow_cli import build_one_link_parser, provider_config" in source
    assert "_provider_config" not in source
    assert "build_parser as build_one_link_parser" not in source
    assert "build_semantic_provider(provider_config(args))" in source
