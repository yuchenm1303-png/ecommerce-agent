"""Grounded product identity extraction for listing bootstrap.

Supplier pages remain restricted to product-focused structured evidence so generic
page chrome can never become product identity. Customer Product Packs are already
mechanically curated at intake, so their normalized document text is an explicit
product evidence source as well.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol

from .source_snapshot import SourceSnapshot


_ENGLISH_PHRASE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 '&/()+.,-]*$")
_ENGLISH_LETTER = re.compile(r"[A-Za-z]")


class JSONTaskProvider(Protocol):
    name: str

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        ...


class ProductIdentityError(ValueError):
    """Grounded evidence did not establish one physical product."""


@dataclass(slots=True, frozen=True)
class ProductIdentity:
    entity_kind: str
    product_type_en: str
    brand: str
    brand_status: str
    product_summary: str
    confidence: float
    evidence_refs: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_kind": self.entity_kind,
            "product_type_en": self.product_type_en,
            "brand": self.brand,
            "brand_status": self.brand_status,
            "product_summary": self.product_summary,
            "confidence": self.confidence,
            "evidence_refs": list(self.evidence_refs),
        }


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _bounded_json(value: Any, *, max_chars: int) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:
        text = str(value or "")
    return _clean(text)[:max_chars]


def _product_json_ld_nodes(value: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if len(output) >= 8:
            return
        if isinstance(node, dict):
            raw_type = node.get("@type")
            types = raw_type if isinstance(raw_type, list) else [raw_type]
            if any(str(item or "").casefold() == "product" for item in types):
                output.append(node)
            for child in node.values():
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return output


def build_product_identity_sources(
    snapshot: SourceSnapshot,
    *,
    image_paths: Iterable[str | Path] = (),
) -> list[dict[str, Any]]:
    """Build bounded identity evidence from the normalized primary input."""

    sources: list[dict[str, Any]] = []

    def add_text(source_id: str, source_type: str, origin: str, content: str) -> None:
        text = _clean(content)
        if not text:
            return
        sources.append(
            {
                "source_id": source_id,
                "source_type": source_type,
                "kind": "text",
                "origin": origin,
                "content": text,
            }
        )

    is_customer_pack = (
        str(snapshot.meta.get("input_mode") or "").strip().casefold()
        == "customer_product_pack"
    )
    if is_customer_pack:
        # Unlike a supplier page, this text is generated only from files the
        # customer explicitly selected as this product's evidence. It therefore
        # has no site navigation/chrome contamination and is safe for identity.
        add_text(
            "identity:customer-pack-text",
            "customer_product_document",
            snapshot.final_url or snapshot.requested_url,
            snapshot.visible_text[:12000],
        )
    else:
        add_text(
            "identity:page-title",
            "supplier_product_heading",
            snapshot.final_url or snapshot.requested_url,
            snapshot.title,
        )

    preferred_meta = (
        "og:title",
        "og:description",
        "product:brand",
        "description",
    )
    for key in preferred_meta:
        value = snapshot.meta.get(key)
        if value:
            add_text(
                f"identity:meta:{key}",
                "customer_product_metadata" if is_customer_pack else "supplier_product_metadata",
                f"meta:{key}",
                value[:1800],
            )

    json_ld_index = 0
    for root in snapshot.json_ld:
        for node in _product_json_ld_nodes(root):
            json_ld_index += 1
            add_text(
                f"identity:jsonld-product:{json_ld_index}",
                "supplier_structured_product",
                "json-ld:Product",
                _bounded_json(node, max_chars=3000),
            )
            if json_ld_index >= 6:
                break
        if json_ld_index >= 6:
            break

    table_chars = 0
    table_count = 0
    for row in snapshot.table_rows:
        if table_count >= 80 or table_chars >= 9000:
            break
        key = _clean(row.key)
        value = _clean(row.value)
        if not key or not value:
            continue
        content = f"{key}: {value}"[:1000]
        source_id = f"identity:attribute:{row.table_index}:{row.row_index}"
        add_text(
            source_id,
            "customer_product_attribute" if is_customer_pack else "supplier_product_attribute",
            source_id,
            content,
        )
        table_chars += len(content)
        table_count += 1

    embedded_chars = 0
    embedded_count = 0
    for index, item in enumerate(snapshot.embedded_data, start=1):
        if embedded_count >= 8 or embedded_chars >= 4000:
            break
        text = _clean(item)
        if not text:
            continue
        # Source capture already limits embedded_data to product identity / SKU /
        # specification / offer / detail-document structures. Keep this bounded
        # and never add generic script or page-body text here.
        text = text[:1200]
        add_text(
            f"identity:embedded:{index}",
            "supplier_product_embedded_data",
            f"embedded:{index}",
            text,
        )
        embedded_chars += len(text)
        embedded_count += 1

    seen_images: set[str] = set()
    image_count = 0
    for raw_path in image_paths:
        if image_count >= 3:
            break
        path = Path(raw_path)
        normalized = str(path.resolve()) if path.exists() else str(path)
        if normalized in seen_images or not path.is_file():
            continue
        seen_images.add(normalized)
        image_count += 1
        sources.append(
            {
                "source_id": f"identity:image:{image_count}",
                "source_type": "customer_product_image" if is_customer_pack else "supplier_product_image",
                "kind": "image",
                "image_path": str(path),
            }
        )

    return sources


def build_product_identity_request(
    snapshot: SourceSnapshot,
    *,
    image_paths: Iterable[str | Path] = (),
) -> dict[str, Any]:
    sources = build_product_identity_sources(snapshot, image_paths=image_paths)
    allowed_refs = [str(item.get("source_id") or "") for item in sources if item.get("source_id")]
    return {
        "task": "infer_grounded_supplier_product_identity",
        "system_instruction": (
            "Identify the physical item actually being offered for sale from product-focused evidence. "
            "A supplier website/platform itself is never the product unless the evidence explicitly offers "
            "a service rather than a physical item. Evidence may be in any language. Return canonical "
            "English product identity. JSON only."
        ),
        "prompt_instruction": (
            "Use only grounded_sources. Ignore site navigation, marketplace branding, seller-platform "
            "descriptions, procurement slogans and other page chrome. Determine whether the evidence "
            "establishes one physical product. For a physical product, cite the exact source_id values "
            "that support product identity and brand status."
        ),
        "context": {
            "product_url": snapshot.final_url or snapshot.requested_url,
            "allowed_evidence_refs": allowed_refs,
        },
        "grounded_sources": sources,
        "rules": [
            "entity_kind must be physical_product, service_or_platform, or unknown.",
            "Use physical_product only when the evidence establishes a tangible item being sold.",
            "For physical_product, product_type_en must be one concise ordinary English noun phrase for the item itself.",
            "For service_or_platform or unknown, product_type_en must be empty.",
            "product_summary must describe the offered item, not the supplier website.",
            "evidence_refs may contain only exact source_id values from allowed_evidence_refs.",
            "For physical_product, evidence_refs must contain at least one source that identifies the item itself.",
            "Treat model numbers, variants and descriptive words as non-brand unless evidence explicitly identifies them as a brand.",
            "brand_status=explicit only when evidence explicitly identifies a brand.",
            "brand_status=unbranded only when evidence explicitly indicates neutral/no-brand/OEM/unbranded status.",
            "Otherwise brand_status=unknown and brand must be empty.",
            "confidence is 0..1 and reflects confidence in product identity from the supplied evidence.",
        ],
        "json_contract": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "entity_kind": {
                    "type": "string",
                    "enum": ["physical_product", "service_or_platform", "unknown"],
                },
                "product_type_en": {"type": "string"},
                "brand": {"type": "string"},
                "brand_status": {
                    "type": "string",
                    "enum": ["explicit", "unbranded", "unknown"],
                },
                "product_summary": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "evidence_refs": {
                    "type": "array",
                    "items": {"type": "string", "enum": allowed_refs},
                    "maxItems": 12,
                },
            },
            "required": [
                "entity_kind",
                "product_type_en",
                "brand",
                "brand_status",
                "product_summary",
                "confidence",
                "evidence_refs",
            ],
        },
        "strict_json_schema": True,
    }


def _parse_product_identity(raw: Any, *, allowed_refs: set[str]) -> ProductIdentity:
    if not isinstance(raw, dict):
        raise ProductIdentityError("product identity response must be a JSON object")

    entity_kind = _clean(raw.get("entity_kind")).casefold()
    if entity_kind not in {"physical_product", "service_or_platform", "unknown"}:
        raise ProductIdentityError(f"invalid product identity entity_kind={entity_kind!r}")

    product_type = _clean(raw.get("product_type_en"))
    if entity_kind != "physical_product":
        raise ProductIdentityError(
            f"product evidence did not establish one physical product (entity_kind={entity_kind or 'unknown'})"
        )
    if (
        len(product_type) < 2
        or not product_type.isascii()
        or not _ENGLISH_PHRASE.fullmatch(product_type)
        or not _ENGLISH_LETTER.search(product_type)
    ):
        raise ProductIdentityError(
            f"physical product identity did not contain a canonical English product type: {product_type!r}"
        )

    brand_status = _clean(raw.get("brand_status")).casefold()
    if brand_status not in {"explicit", "unbranded", "unknown"}:
        raise ProductIdentityError(f"invalid product identity brand_status={brand_status!r}")
    brand = _clean(raw.get("brand"))
    if brand_status == "explicit" and not brand:
        raise ProductIdentityError("explicit product identity brand_status requires brand")
    if brand_status != "explicit":
        brand = ""

    summary = _clean(raw.get("product_summary"))
    if not summary:
        summary = product_type

    try:
        confidence = float(raw.get("confidence"))
    except (TypeError, ValueError) as exc:
        raise ProductIdentityError("product identity confidence must be numeric") from exc
    if not 0.0 <= confidence <= 1.0:
        raise ProductIdentityError("product identity confidence must be within 0..1")

    refs: list[str] = []
    seen: set[str] = set()
    for value in raw.get("evidence_refs") or []:
        ref = _clean(value)
        if not ref or ref in seen:
            continue
        if ref not in allowed_refs:
            raise ProductIdentityError(f"product identity cited unknown evidence ref: {ref!r}")
        seen.add(ref)
        refs.append(ref)
    if not refs:
        raise ProductIdentityError("physical product identity requires grounded evidence_refs")

    return ProductIdentity(
        entity_kind="physical_product",
        product_type_en=product_type,
        brand=brand,
        brand_status=brand_status,
        product_summary=summary,
        confidence=confidence,
        evidence_refs=tuple(refs),
    )


def infer_product_identity(
    provider: JSONTaskProvider,
    snapshot: SourceSnapshot,
    *,
    image_paths: Iterable[str | Path] = (),
) -> ProductIdentity:
    request = build_product_identity_request(snapshot, image_paths=image_paths)
    allowed_refs = {
        str(item.get("source_id") or "")
        for item in request.get("grounded_sources") or []
        if str(item.get("source_id") or "")
    }
    return _parse_product_identity(provider.extract_json(request), allowed_refs=allowed_refs)


def build_vertical_search_terms_request(identity: ProductIdentity) -> dict[str, Any]:
    return {
        "task": "derive_product_type_search_terms",
        "system_instruction": (
            "Derive concise English marketplace search phrases from an already grounded physical-product "
            "identity. Do not inspect or reinterpret the supplier website. JSON only."
        ),
        "prompt_instruction": (
            "Use only context.product_identity. Return ordinary English product-type noun phrases useful "
            "for searching a marketplace taxonomy. Keep the meaning of product_type_en; synonyms may be "
            "added only when they denote the same physical product type."
        ),
        "context": {"product_identity": identity.as_dict()},
        "rules": [
            "Return 1 to 4 concise English product-type noun phrases, most specific first.",
            "Do not return a website, platform, seller, marketplace, service, department, or generic word such as product/category/vertical.",
            "Do not invent product attributes or a marketplace-specific taxonomy label.",
            "Each term must denote the same physical product type as product_type_en.",
        ],
        "json_contract": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "vertical_search_terms": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 4,
                    "items": {"type": "string", "minLength": 2},
                }
            },
            "required": ["vertical_search_terms"],
        },
        "strict_json_schema": True,
    }


__all__ = [
    "JSONTaskProvider",
    "ProductIdentity",
    "ProductIdentityError",
    "build_product_identity_request",
    "build_product_identity_sources",
    "build_vertical_search_terms_request",
    "infer_product_identity",
]
