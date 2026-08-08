from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .source_bundle import normalize_key


class AliasConfigError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class AliasConfig:
    vertical: str
    aliases: dict[str, tuple[str, ...]]
    sections: dict[str, str]
    source_path: str


def _clean_vertical(value: object) -> str:
    return str(value or "").strip()


def _parse_alias_mapping(payload: Any) -> dict[str, tuple[str, ...]]:
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise AliasConfigError("aliases 必须是 JSON object：{QA Question: [Makro Label, ...]}。")

    output: dict[str, tuple[str, ...]] = {}
    alias_owner: dict[str, str] = {}

    for raw_question, raw_aliases in payload.items():
        question = str(raw_question or "").strip()
        normalized_question = normalize_key(question)
        if not normalized_question:
            raise AliasConfigError("alias config 中存在空 question key。")
        if not isinstance(raw_aliases, list):
            raise AliasConfigError(f"aliases[{question!r}] 必须是数组。")

        cleaned: list[str] = []
        seen: set[str] = set()
        for raw_alias in raw_aliases:
            alias = str(raw_alias or "").strip()
            normalized_alias = normalize_key(alias)
            if not normalized_alias or normalized_alias == normalized_question:
                continue
            if normalized_alias in seen:
                continue

            owner = alias_owner.get(normalized_alias)
            if owner is not None and owner != normalized_question:
                raise AliasConfigError(
                    f"同一个 Makro alias {alias!r} 被多个 QA question 声明；"
                    "为避免错误字段匹配，配置已拒绝。"
                )
            alias_owner[normalized_alias] = normalized_question
            seen.add(normalized_alias)
            cleaned.append(alias)

        if cleaned:
            output[normalized_question] = tuple(cleaned)

    return output


def _parse_section_mapping(payload: Any) -> dict[str, str]:
    """Parse explicit QA question -> Makro section constraints.

    This is configuration, not category-specific code. It exists for cases where
    the customer QA sheet does not carry section metadata and the live page has
    the same generic label in several cards (for example ``Height``).
    """

    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise AliasConfigError("sections 必须是 JSON object：{QA Question: Makro Section}。")

    output: dict[str, str] = {}
    for raw_question, raw_section in payload.items():
        question = str(raw_question or "").strip()
        section = str(raw_section or "").strip()
        normalized_question = normalize_key(question)
        if not normalized_question:
            raise AliasConfigError("sections 中存在空 question key。")
        if not section:
            raise AliasConfigError(f"sections[{question!r}] 不能为空。")
        if normalized_question in output and output[normalized_question] != section:
            raise AliasConfigError(f"question {question!r} 声明了多个 section。")
        output[normalized_question] = section
    return output


def load_alias_config(
    path: str | Path,
    *,
    expected_vertical: str | None = None,
) -> AliasConfig:
    """Load explicit, auditable QA -> live-field matching metadata.

    Accepted shape::

        {
          "schema_version": 1,
          "vertical": "vehicle_camera_system",
          "aliases": {
            "Video Resolution": ["Image Resolution"]
          },
          "sections": {
            "Height": "Additional Description"
          }
        }

    ``aliases`` changes only the accepted live label/key. ``sections`` adds a
    deterministic section constraint and is the correct way to resolve duplicate
    generic labels without hard-coding a vertical or SKU in Python.
    """

    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AliasConfigError("alias config 根节点必须是 JSON object。")

    version = payload.get("schema_version", 1)
    if version != 1:
        raise AliasConfigError(f"不支持 alias config schema_version={version!r}。")

    vertical = _clean_vertical(payload.get("vertical"))
    expected = _clean_vertical(expected_vertical)
    if expected and vertical and vertical != expected:
        raise AliasConfigError(
            f"alias config vertical={vertical!r} 与当前 expected_vertical={expected!r} 不一致。"
        )
    if expected and not vertical:
        raise AliasConfigError(
            "alias config 缺少 vertical；为避免跨类目误用，实时 Makro planner 要求显式 vertical。"
        )

    aliases = _parse_alias_mapping(payload.get("aliases"))
    sections = _parse_section_mapping(payload.get("sections"))
    return AliasConfig(
        vertical=vertical,
        aliases=aliases,
        sections=sections,
        source_path=str(source.resolve()),
    )
