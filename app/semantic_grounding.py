from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .source_snapshot import SourceSnapshot, source_snapshot_from_json


TEXT_KIND = "text"
IMAGE_KIND = "image"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


@dataclass(slots=True, frozen=True)
class GroundedSource:
    """One exact source unit the whole-product AI may cite.

    Long text can be split into citation chunks, but chunking is only a source
    integrity/detail mechanism. It never determines model execution count.
    """

    source_id: str
    source_type: str
    kind: str
    origin: str
    content: str = ""
    image_path: str = ""
    sha256: str = ""

    @property
    def logical_source_id(self) -> str:
        marker = ":text:"
        if self.kind == TEXT_KIND and marker in self.source_id:
            return self.source_id.split(marker, 1)[0]
        return self.source_id

    def as_request_dict(self) -> dict[str, str]:
        payload = {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "kind": self.kind,
            "origin": self.origin,
            "sha256": self.sha256,
        }
        if self.kind == TEXT_KIND:
            payload["content"] = self.content
        elif self.kind == IMAGE_KIND:
            payload["image_path"] = self.image_path
        return payload

    def as_manifest_dict(self) -> dict[str, str]:
        return {
            "source_id": self.source_id,
            "logical_source_id": self.logical_source_id,
            "source_type": self.source_type,
            "kind": self.kind,
            "origin": self.origin,
            "sha256": self.sha256,
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
            if source.sha256 and not re.fullmatch(r"[0-9a-f]{64}", source.sha256):
                raise ValueError(f"source {source.source_id} 的 sha256 格式无效。")

    def by_id(self, source_id: str) -> GroundedSource | None:
        wanted = source_id.strip()
        for source in self.sources:
            if source.source_id == wanted:
                return source
        return None

    @property
    def logical_source_count(self) -> int:
        return len({source.logical_source_id for source in self.sources})

    def as_request_list(self) -> list[dict[str, str]]:
        return [source.as_request_dict() for source in self.sources]

    def as_manifest(self) -> dict[str, object]:
        return {
            "schema_version": 3,
            "source_count": len(self.sources),
            "logical_source_count": self.logical_source_count,
            "sources": [source.as_manifest_dict() for source in self.sources],
        }


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
    """Split long captured text for citation precision, not model batching."""

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
        start = max(start + 1, end - overlap_chars)
    return chunks


def _text_source(
    *,
    prefix: str,
    source_type: str,
    ordinal: int,
    chunk_index: int,
    origin: str,
    content: str,
) -> GroundedSource:
    digest = _sha256_text(content)
    return GroundedSource(
        source_id=f"{prefix}:{ordinal:03d}:text:{chunk_index:04d}:{digest[:12]}",
        source_type=source_type,
        kind=TEXT_KIND,
        origin=origin,
        content=content,
        sha256=digest,
    )


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
    if not path.is_file():
        raise FileNotFoundError(f"source snapshot 不存在：{path}")
    snapshot = source_snapshot_from_json(path)
    text = _snapshot_text(snapshot)
    chunks = chunk_text(text, max_chars=max_chars, overlap_chars=overlap_chars)
    origin = snapshot.final_url or snapshot.requested_url or str(path.resolve())
    return [
        _text_source(
            prefix=prefix,
            source_type=source_type,
            ordinal=ordinal,
            chunk_index=index,
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
    """Create the exact source universe visible to the whole-product AI.

    Source ids bind content digests, so stale AI decisions fail closed after any
    image/page/context change. All resulting sources are submitted in the same
    normal-path AI request.
    """

    sources: list[GroundedSource] = []

    for index, raw_path in enumerate(image_paths, start=1):
        path = Path(raw_path)
        if not path.is_file():
            raise FileNotFoundError(f"商品图片不存在：{path}")
        digest = _sha256_bytes(path.read_bytes())
        sources.append(
            GroundedSource(
                source_id=f"image:{index:03d}:{digest[:12]}",
                source_type="product_image",
                kind=IMAGE_KIND,
                origin=str(path.resolve()),
                image_path=str(path),
                sha256=digest,
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
            digest = _sha256_text(chunk)
            sources.append(
                GroundedSource(
                    source_id=f"customer-text:001:text:{index:04d}:{digest[:12]}",
                    source_type="customer_file",
                    kind=TEXT_KIND,
                    origin="supplemental_text",
                    content=chunk,
                    sha256=digest,
                )
            )

    return GroundingCatalog(sources=sources)
