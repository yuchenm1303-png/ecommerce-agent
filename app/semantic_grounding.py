from __future__ import annotations

import hashlib
import json
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
    """One exact source unit the AI may cite."""

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


_PRODUCT_DATA_ANCHOR = re.compile(
    r"\b(?:offerId|offerLoginId|skuId|sku2|skuMap|skuProps|specId|detailUrl)\b",
    re.IGNORECASE,
)


def _structured_assignments(value: str) -> list[str]:
    """Extract exact scalar/object assignments for known supplier transport keys."""

    output: list[str] = []
    for match in re.finditer(
        r'''(?P<key>["']?(?:offerId|offerLoginId|skuId|sku2|skuMap|skuProps|specId|detailUrl)["']?)\s*[:=]\s*''',
        value,
        flags=re.IGNORECASE,
    ):
        start = match.start("key")
        cursor = match.end()
        if cursor >= len(value):
            continue
        opening = value[cursor]
        end = cursor
        if opening in {'"', "'"}:
            quote = opening
            end += 1
            escaped = False
            while end < len(value):
                char = value[end]
                end += 1
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    break
            else:
                continue
        elif opening in "[{":
            pairs = {"[": "]", "{": "}"}
            stack = [pairs[opening]]
            end += 1
            quote = ""
            escaped = False
            while end < len(value) and stack:
                char = value[end]
                end += 1
                if quote:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == quote:
                        quote = ""
                    continue
                if char in {'"', "'"}:
                    quote = char
                elif char in pairs:
                    stack.append(pairs[char])
                elif char == stack[-1]:
                    stack.pop()
            if stack:
                continue
        else:
            token = re.match(r"[^,;\s)}\]]+", value[cursor:])
            if token is None:
                continue
            end = cursor + token.end()
        exact = value[start:end].strip()
        if exact:
            output.append(exact)
    return output


def _compact_embedded_data(items: Iterable[str]) -> list[str]:
    """Keep exact product/variant records while discarding generic page scripts.

    Source snapshots remain untouched on disk. This function only builds the
    compact, citable text view sent to AI. It deliberately uses structural page
    markers rather than product-category or marketplace-field semantics.
    """

    candidates: list[str] = []
    seen: set[str] = set()
    for raw in items:
        value = re.sub(r"\s+", " ", str(raw or "")).strip()
        if not value:
            continue
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = None
        is_dom_record = bool(
            isinstance(parsed, dict)
            and set(parsed).issubset({"tag", "text", "attrs"})
            and (str(parsed.get("text") or "").strip() or parsed.get("attrs"))
        )
        extracted = [value] if is_dom_record else _structured_assignments(value)
        for exact in extracted:
            fingerprint = exact.casefold()
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            candidates.append(exact)

    # Capture currently emits overlapping windows around nearby structured-data
    # markers. Keeping only the longest exact container is lossless and prevents
    # the same page JSON from being sent repeatedly.
    kept: list[str] = []
    kept_folded: list[str] = []
    for value in sorted(candidates, key=len, reverse=True):
        folded = value.casefold()
        if any(folded in existing for existing in kept_folded):
            continue
        kept.append(value)
        kept_folded.append(folded)
    kept.reverse()
    return kept


def _compact_visible_text(value: str) -> str:
    """Remove session-only storefront chrome from otherwise citable page text."""

    text = str(value or "").strip()
    if not text:
        return ""
    # Delivery destinations depend on the signed-in browser/session, not the
    # product. Preserve the surrounding labels while dropping the address.
    text = re.sub(r"(?m)(^|\n)送至\s*\n[^\n]*(?=\n预计)", r"\1送至\n预计", text)
    # Marketplace price explanations are generic legal boilerplate and the
    # captured tail can end at a different character while the page is loading.
    for marker in ("【平台活动下价格】", "【非平台活动下价格】"):
        index = text.find(marker)
        if index >= 0:
            text = text[:index].rstrip()
            break
    return text


def _snapshot_non_row_parts(snapshot: SourceSnapshot) -> list[tuple[str, str]]:
    """Build a compact citable view while the full snapshot stays unchanged."""

    parts: list[tuple[str, str]] = []
    identity: list[str] = []
    if snapshot.title:
        identity.append(f"Title: {snapshot.title}")
    for key, value in snapshot.meta.items():
        if value:
            identity.append(f"Meta {key}: {value}")
    if identity:
        parts.append(("identity", "Page identity/meta:\n" + "\n".join(identity)))

    if snapshot.json_ld:
        parts.append(
            (
                "json-ld",
                "Page JSON-LD:\n"
                + json.dumps(snapshot.json_ld, ensure_ascii=False, separators=(",", ":")),
            )
        )
    embedded = _compact_embedded_data(snapshot.embedded_data)
    if embedded:
        parts.append(("embedded", "Embedded page/variant data:\n" + "\n".join(embedded)))
    visible_text = _compact_visible_text(snapshot.visible_text)
    if visible_text:
        parts.append(("visible-text", "Rendered page text:\n" + visible_text))
    return [(kind, part) for kind, part in parts if part.strip()]


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


def _row_source(
    *,
    prefix: str,
    source_type: str,
    ordinal: int,
    row_ordinal: int,
    origin: str,
    key: str,
    value: str,
    table_index: int,
    row_index: int,
) -> GroundedSource:
    content = (
        "Structured page row; preserve key/value meaning exactly: "
        + json.dumps(
            {
                "key": key,
                "value": value,
                "table_index": table_index,
                "row_index": row_index,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    digest = _sha256_text(content)
    return GroundedSource(
        source_id=f"{prefix}:{ordinal:03d}:text:row-{row_ordinal:04d}:{digest[:12]}",
        source_type=source_type,
        kind=TEXT_KIND,
        origin=f"{origin}#table={table_index}&row={row_index}",
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
    origin = snapshot.final_url or snapshot.requested_url or str(path.resolve())

    sources: list[GroundedSource] = []
    chunk_index = 1

    non_row_parts = _snapshot_non_row_parts(snapshot)
    if non_row_parts:
        part_kind, first = non_row_parts.pop(0)
        for chunk in chunk_text(first, max_chars=max_chars, overlap_chars=overlap_chars):
            sources.append(
                _text_source(
                    prefix=prefix,
                    source_type=source_type,
                    ordinal=ordinal,
                    chunk_index=chunk_index,
                    origin=f"{origin}#evidence={part_kind}",
                    content=chunk,
                )
            )
            chunk_index += 1

    for row_ordinal, row in enumerate(snapshot.table_rows, start=1):
        if not row.key or not row.value:
            continue
        sources.append(
            _row_source(
                prefix=prefix,
                source_type=source_type,
                ordinal=ordinal,
                row_ordinal=row_ordinal,
                origin=origin,
                key=row.key,
                value=row.value,
                table_index=row.table_index,
                row_index=row.row_index,
            )
        )

    for part_kind, part in non_row_parts:
        for chunk in chunk_text(part, max_chars=max_chars, overlap_chars=overlap_chars):
            sources.append(
                _text_source(
                    prefix=prefix,
                    source_type=source_type,
                    ordinal=ordinal,
                    chunk_index=chunk_index,
                    origin=f"{origin}#evidence={part_kind}",
                    content=chunk,
                )
            )
            chunk_index += 1
    return sources


def build_grounding_catalog(
    *,
    image_paths: Iterable[str] = (),
    supplier_snapshots: Iterable[str] = (),
    official_snapshots: Iterable[str] = (),
    supplemental_text: str = "",
    max_text_chars: int = 3000,
    overlap_chars: int = 250,
) -> GroundingCatalog:
    """Create the exact raw source universe visible to the field-filling AI."""

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
