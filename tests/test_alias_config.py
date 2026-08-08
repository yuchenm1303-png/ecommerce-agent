from __future__ import annotations

import json

import pytest

from app.alias_config import AliasConfigError, load_alias_config
from app.source_bundle import normalize_key


def write_config(tmp_path, payload):
    path = tmp_path / "aliases.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_alias_config_normalizes_question_keys_and_keeps_real_labels(tmp_path):
    path = write_config(
        tmp_path,
        {
            "schema_version": 1,
            "vertical": "vehicle_camera_system",
            "aliases": {"Video Resolution": ["Image Resolution", " Image Resolution "]},
            "sections": {"Height": "Additional Description"},
        },
    )

    config = load_alias_config(path, expected_vertical="vehicle_camera_system")

    assert config.vertical == "vehicle_camera_system"
    assert config.aliases == {normalize_key("Video Resolution"): ("Image Resolution",)}
    assert config.sections == {normalize_key("Height"): "Additional Description"}


def test_alias_config_allows_section_only_config(tmp_path):
    path = write_config(
        tmp_path,
        {
            "schema_version": 1,
            "vertical": "vehicle_camera_system",
            "sections": {"Height": "Additional Description"},
        },
    )

    config = load_alias_config(path, expected_vertical="vehicle_camera_system")

    assert config.aliases == {}
    assert config.sections == {normalize_key("Height"): "Additional Description"}


def test_empty_section_override_is_rejected(tmp_path):
    path = write_config(
        tmp_path,
        {
            "vertical": "vehicle_camera_system",
            "sections": {"Height": ""},
        },
    )

    with pytest.raises(AliasConfigError, match="不能为空"):
        load_alias_config(path, expected_vertical="vehicle_camera_system")


def test_vertical_mismatch_fails_closed(tmp_path):
    path = write_config(
        tmp_path,
        {
            "vertical": "sports_action_camera",
            "aliases": {"Video Resolution": ["Image Resolution"]},
        },
    )

    with pytest.raises(AliasConfigError, match="不一致"):
        load_alias_config(path, expected_vertical="vehicle_camera_system")


def test_live_vertical_requires_config_vertical(tmp_path):
    path = write_config(
        tmp_path,
        {"sections": {"Height": "Additional Description"}},
    )

    with pytest.raises(AliasConfigError, match="缺少 vertical"):
        load_alias_config(path, expected_vertical="vehicle_camera_system")


def test_same_alias_cannot_be_owned_by_two_questions(tmp_path):
    path = write_config(
        tmp_path,
        {
            "vertical": "vehicle_camera_system",
            "aliases": {
                "Video Resolution": ["Image Resolution"],
                "Recording Resolution": ["Image Resolution"],
            },
        },
    )

    with pytest.raises(AliasConfigError, match="多个 QA question"):
        load_alias_config(path, expected_vertical="vehicle_camera_system")
