from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .source_snapshot import SourceSnapshot, source_snapshot_from_json


TEXT_KIND = "text"
IMAGE_KIND = "image"


@dataclass(slots=True, frozen=True)
class GroundedSource:
    """One exact source unit that a semantic extractor is allowed to cite.

    Text sources are deliberately chunked before being sent to a model so every
    returned fact can cite one bounded piece of captured source text. Image
    sources use stable ids and retain the local path only in the manifest.
    """

    source_id: str
    source_type: str
    kind: str
    origin: str
    content: str = ""
    image_path: str = ""

    def as_request_dict(self) -> dict[str, str]:
        payload = {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "kind": self.kind,
            "origin": self.origin,
        }
        if self.kind == TEXT_KIND:
            payload["content"] = self.content
        elif self.kind == IMAGE_KIND:
            payload["image_path"] = self.image_path
        return payload

    def as_manifest_dict(self) -> dict[str, str]:
        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "kind": self.kind,
            "origin": self.origin,
            "image_path": self.image_path,
            "content": self.content,
        }


@dataclass(slots=True)
class GroundingCatalog:
    sources: list[GroundedSource] = field(default_factory=list)

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for source in self.sources:
            if not source.source_id:
                raise ValueError("grounded source 缺少 source_id。")
            if source.source_id in seen:
                raise ValueError(f"grounded source_id 重复：{source.source_id}")
            seen.add(source.source_id)
            if source.kind not in {TEXT_KIND, IMAGE_KIND}:
                raise ValueError(f"不支持的 grounded source kind：{source.kind!r}")
            if source.kind == TEXT_KIND and not source.content.strip():
                raise ValueError(f"文本 source {source.source_id} 内容为空。")
            if source.kind == IMAGE_KIND and not source.image_path.strip():
                raise ValueError(f"图片 source {source.source_id} 缺少 image_path。")

    def by_id(self, source_id: str) -> GroundedSource | None:
        wanted = source_id.strip()
        for source in self.sources:
            if source.source_id == wanted:
                return source
        return None

    def as_request_list(self) -> list[dict[str, str]]:
        return [source.as_request_dict() for source in self.sources]

    def as_manifest(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "source_count": len(self.sources),
            "sources": [source.as_manifest_dict() for source in self.sources],
        }


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _snapshot_text(snapshot: SourceSnapshot) -> str:
    parts: list[str] = []
    if snapshot.title:
        parts.append(f"Title: {snapshot.title}")
    for key, value in snapshot.meta.items():
        if value:
            parts.append(f"Meta {key}: {value}")
    if snapshot.visible_text.strip():
        parts.append(snapshot.visible_text.strip())
    return "\n".join(parts).strip()


def chunk_text(
    text: str,
    *,
    max_chars: int = 3000,
    overlap_chars: int = 250,
) -> list[str]:
    """Deterministically split captured text while retaining small overlap.

    This is character based rather than token based on purpose: the provider
    adapter remains model-neutral. Boundaries are moved left to whitespace when
    practical so evidence snippets are less likely to be split mid-word.
    """

    if max_chars < 500:
        raise ValueError("max_chars 不能小于 500。")
    if overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("overlap_chars 必须满足 0 <= overlap_chars < max_chars。")

    value = text.strip()
    if not value:
        return []
    if len(value) <= max_chars:
        return [value]

    chunks: list[str] = []
    start = 0
    length = len(value)
    while start < length:
        tentative_end = min(length, start + max_chars)
        end = tentative_end
        if tentative_end < length:
            lower_bound = start + max_chars // 2
            whitespace = max(
                value.rfind("\n", lower_bound, tentative_end),
                value.rfind(" ", lower_bound, tentative_end),
            )
            if whitespace > start:
                end = whitespace
        chunk = value[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= length:
            break
        next_start = max(start + 1, end - overlap_chars)
        start = next_start
    return chunks


def _sources_from_snapshot(
    snapshot_path: str | Path,
    *,
    prefix: str,
    source_type: str,
    ordinal: int,
    max_chars: int,
    overlap_chars: int,
) -> list[GroundedSource]:
    path = Path(snapshot_path)
    snapshot = source_snapshot_from_json(path)
    text = _snapshot_text(snapshot)
    chunks = chunk_text(text, max_chars=max_chars, overlap_chars=overlap_chars)
    origin = snapshot.final_url or snapshot.requested_url or str(path.resolve())
    return [
        GroundedSource(
            source_id=f"{prefix}:{ordinal:03d}:text:{index:04d}",
            source_type=source_type,
            kind=TEXT_KIND,
            origin=origin,
            content=chunk,
        )
        for index, chunk in enumerate(chunks, start=1)
    ]


def build_grounding_catalog(
    *,
    image_paths: Iterable[str] = (),
    supplier_snapshots: Iterable[str] = (),
    official_snapshots: Iterable[str] = (),
    supplemental_text: str = "",
    max_text_chars: int = 3000,
    overlap_chars: int = 250,
) -> GroundingCatalog:
    """Create the exact source universe visible to a semantic model.

    The returned source ids are the *only* references accepted later by grounded
    packet validation. This prevents a model from citing a URL, image or text
    chunk that was never supplied to the extraction step.
    """

    sources: list[GroundedSource] = []

    for index, raw_path in enumerate(image_paths, start=1):
        path = Path(raw_path)
        sources.append(
            GroundedSource(
                source_id=f"image:{index:03d}",
                source_type="product_image",
                kind=IMAGE_KIND,
                origin=str(path.resolve()),
                image_path=str(path),
            )
        )

    for index, path in enumerate(supplier_snapshots, start=1):
        sources.extend(
            _sources_from_snapshot(
                path,
                prefix="supplier",
                source_type="supplier_web",
                ordinal=index,
                max_chars=max_text_chars,
                overlap_chars=overlap_chars,
            )
        )

    for index, path in enumerate(official_snapshots, start=1):
        sources.extend(
            _sources_from_snapshot(
                path,
                prefix="official",
                source_type="official_web",
                ordinal=index,
                max_chars=max_text_chars,
                overlap_chars=overlap_chars,
            )
        )

    if supplemental_text.strip():
        for index, chunk in enumerate(
            chunk_text(
                supplemental_text,
                max_chars=max_text_chars,
                overlap_chars=overlap_chars,
            ),
            start=1,
        ):
            sources.append(
                GroundedSource(
                    source_id=f"customer-text:001:text:{index:04d}",
                    source_type="customer_file",
                    kind=TEXT_KIND,
                    origin="supplemental_text",
                    content=chunk,
                )
            )

    return GroundingCatalog(sources=sources)
