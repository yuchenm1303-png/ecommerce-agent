from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .image_evidence import ImageObservation
from .semantic_grounding import GroundingCatalog, TEXT_KIND


COMPACT_EVIDENCE_VERSION = 4
_ROW_PREFIX = "Structured page row; preserve key/value meaning exactly: "
_VISIBLE_TEXT_LIMIT = 1600
_DIMENSION_COLUMNS = {
    "length": ("length", "长"),
    "breadth": ("breadth", "width", "宽"),
    "height": ("height", "高"),
    "weight": ("weight", "重量"),
    "volume": ("volume", "体积"),
}


def _one_line(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _structured_row(content: str) -> tuple[str, str] | None:
    if not content.startswith(_ROW_PREFIX):
        return None
    try:
        row = json.loads(content[len(_ROW_PREFIX) :])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(row, dict):
        return None
    key = _one_line(row.get("key"))
    value = _one_line(row.get("value"))
    return (key, value) if key and value else None


def _compact_text(source_id: str, content: str, *, visible_text: bool = False) -> str:
    value = content.strip()
    row = _structured_row(value)
    if row is not None:
        value = f"{row[0]}={row[1]}"
    for prefix in (
        "Page identity/meta:\n",
        "Embedded page/variant data:\n",
        "Rendered page text:\n",
    ):
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    if visible_text:
        value = value[:_VISIBLE_TEXT_LIMIT]
    return f"[{source_id}] {_one_line(value)}"


def _row_is_redundant(row: tuple[str, str], atomic_rows: set[tuple[str, str]]) -> bool:
    """Drop combined HTML-table rows when their cells also exist as atomic rows."""

    key, value = row
    cells = [_one_line(cell) for cell in value.split("|")]
    if len(cells) < 3 or len(cells) % 2 == 0:
        return False
    pairs = [(key, cells[0])]
    pairs.extend((cells[index], cells[index + 1]) for index in range(1, len(cells), 2))
    return all(pair in atomic_rows for pair in pairs)


def _dimension_column(value: str) -> tuple[str, str] | None:
    normalized = _one_line(value).casefold()
    for key, markers in _DIMENSION_COLUMNS.items():
        if any(normalized.startswith(marker) for marker in markers):
            unit_match = re.search(r"\(([^)]+)\)", normalized)
            return key, _one_line(unit_match.group(1)) if unit_match else ""
    return None


def _scoped_dimension_rows(
    text_sources: list[Any],
    parsed_rows: dict[str, tuple[str, str]],
) -> tuple[dict[str, str], set[str]]:
    """Rejoin adjacent HTML header/data rows without interpreting product categories."""

    overrides: dict[str, str] = {}
    consumed_headers: set[str] = set()
    row_sources = [source for source in text_sources if source.source_id in parsed_rows]
    for header_source, data_source in zip(row_sources, row_sources[1:]):
        header = parsed_rows[header_source.source_id]
        data = parsed_rows[data_source.source_id]
        headers = [header[0], *[_one_line(cell) for cell in header[1].split("|")]]
        values = [data[0], *[_one_line(cell) for cell in data[1].split("|")]]
        if len(headers) != len(values):
            continue
        typed = [_dimension_column(column) for column in headers]
        if sum(item is not None for item in typed) < 4:
            continue
        rendered: list[str] = ["scope=packaging"]
        for column, value, dimension in zip(headers, values, typed):
            if not value:
                continue
            if dimension is None:
                rendered.append(f"{_one_line(column)}={value}")
                continue
            key, unit = dimension
            rendered.append(f"{key}={value}{(' ' + unit) if unit else ''}")
        overrides[data_source.source_id] = "; ".join(rendered)
        consumed_headers.add(header_source.source_id)
    return overrides, consumed_headers


@dataclass(slots=True, frozen=True)
class CompactEvidence:
    web_text: str
    image_facts: str
    text_source_count: int
    image_count: int
    image_fact_count: int
    citation_aliases: dict[str, str]
    sha256: str

    @property
    def chars(self) -> int:
        return len(self.web_text) + len(self.image_facts)

    def request_sources(self) -> list[dict[str, str]]:
        sources: list[dict[str, str]] = []
        if self.web_text:
            sources.append(
                {
                    "source_id": "compact:web",
                    "source_type": "compact_supplier_evidence",
                    "kind": "text",
                    "origin": "local compact evidence",
                    "content": self.web_text,
                }
            )
        if self.image_facts:
            sources.append(
                {
                    "source_id": "compact:images",
                    "source_type": "compact_image_facts",
                    "kind": "text",
                    "origin": "cached image observations",
                    "content": self.image_facts,
                }
            )
        return sources

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": COMPACT_EVIDENCE_VERSION,
            "sha256": self.sha256,
            "text_source_count": self.text_source_count,
            "image_count": self.image_count,
            "image_fact_count": self.image_fact_count,
            "citation_aliases": dict(self.citation_aliases),
            "chars": self.chars,
            "web_text": self.web_text,
            "image_facts": self.image_facts,
        }


def build_compact_evidence(
    grounding: GroundingCatalog,
    image_observations: Iterable[ImageObservation],
) -> CompactEvidence:
    text_sources = [source for source in grounding.sources if source.kind == TEXT_KIND]
    parsed_rows = {
        source.source_id: row
        for source in text_sources
        if (row := _structured_row(source.content.strip())) is not None
    }
    atomic_rows = {row for row in parsed_rows.values() if "|" not in row[1]}
    dimension_overrides, dimension_headers = _scoped_dimension_rows(text_sources, parsed_rows)
    text_lines: list[str] = []
    citation_aliases: dict[str, str] = {}
    visible_text_kept = False
    for source in text_sources:
        row = parsed_rows.get(source.source_id)
        if source.source_id in dimension_headers:
            continue
        if row is not None and _row_is_redundant(row, atomic_rows):
            continue
        visible_text = source.origin.endswith("#evidence=visible-text")
        if visible_text and visible_text_kept:
            continue
        alias = f"s{len(text_lines) + 1}"
        if source.source_id in dimension_overrides:
            line = f"[{alias}] {dimension_overrides[source.source_id]}"
        else:
            line = _compact_text(alias, source.content, visible_text=visible_text)
        if line.strip():
            text_lines.append(line)
            citation_aliases[alias] = source.source_id
        if visible_text:
            visible_text_kept = True
    observations = list(image_observations)
    fact_lines: list[str] = []
    for image_index, observation in enumerate(observations, start=1):
        alias = f"i{image_index}"
        citation_aliases[alias] = observation.image_id
        for fact in observation.facts:
            value = _one_line(fact.value)
            qualifier = _one_line(fact.qualifier)
            rendered_value = f"{value} {qualifier}".strip()
            fact_lines.append(
                f"[{alias}] "
                f"{_one_line(fact.name)}({_one_line(fact.scope)})={rendered_value}"
            )
    web_text = "\n".join(line for line in text_lines if line.strip())
    image_facts = "\n".join(fact_lines)
    raw = json.dumps(
        {
            "version": COMPACT_EVIDENCE_VERSION,
            "web_text": web_text,
            "image_facts": image_facts,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return CompactEvidence(
        web_text=web_text,
        image_facts=image_facts,
        text_source_count=len(text_lines),
        image_count=len(observations),
        image_fact_count=len(fact_lines),
        citation_aliases=citation_aliases,
        sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    )


def write_compact_evidence(evidence: CompactEvidence, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(evidence.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target
