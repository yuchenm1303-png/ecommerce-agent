from __future__ import annotations

import secrets
from urllib.parse import urlsplit

from .source_bundle import ProductSourceBundle, normalize_key


WARRANTY_SUMMARY_POLICY = "Manufacturing Quality Support"
WARRANTY_SERVICE_TYPE_POLICY = (
    "12-month limited quality support covers only inherent factory manufacturing defects. "
    "Any damage caused by daily wear, accidental impact, improper assembly or wrong usage will not be supported."
)

BUSINESS_ATTRIBUTE_ALIASES: dict[str, tuple[str, ...]] = {
    "sku_id": ("sku", "sku id", "sku_id", "商品sku", "商品编码"),
    "listing_status": ("listing status", "listing_status", "status", "上架状态"),
    "mrp": ("mrp", "base price", "base_price", "原价", "基础价格"),
    "flipkart_selling_price": (
        "selling price",
        "your selling price",
        "flipkart_selling_price",
        "售价",
        "销售价",
    ),
    "minimum_order_quantity": (
        "minimum order quantity",
        "minmoq",
        "min moq",
        "minimum_order_quantity",
        "最小起订量",
    ),
    "max_order_quantity_allowed": (
        "maximum order quantity",
        "maxoq",
        "max moq",
        "max_order_quantity_allowed",
        "最大订购量",
    ),
    "shipping_days": ("shipping days", "pick pack sla", "shipping_days", "发货天数"),
    "service_profile": (
        "service profile",
        "service_profile",
        "fulfillment by",
        "fulfilment by",
        "fulfillment",
        "fbs",
    ),
    "forbid_shipping": (
        "selling region preference",
        "selling region",
        "shipping region",
        "forbid_shipping",
    ),
    "warranty_summary": (
        "warranty summary",
        "warranty_summary",
    ),
    "warranty_service_type": (
        "warranty service type",
        "warranty_service_type",
    ),
}

BUSINESS_ALLOWED_SOURCE_TYPES = {"structured", "business", "config", "rule"}

_BUSINESS_QUESTION_NAMES = {
    normalize_key(name)
    for attribute_key, aliases in BUSINESS_ATTRIBUTE_ALIASES.items()
    for name in (attribute_key, *aliases)
}
_BUSINESS_QUESTION_NAMES.update(
    normalize_key(name)
    for name in (
        "stock",
        "stock quantity",
        "available stock",
        "inventory",
        "inventory quantity",
        "quantity in stock",
    )
)


def is_business_question(question: str) -> bool:
    """Return whether a field is seller-operated rather than a product fact."""

    return normalize_key(question) in _BUSINESS_QUESTION_NAMES


def _stable_product_url(value: str) -> str:
    """Normalize only URL transport noise; never interpret product semantics."""

    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}"


def _validated_listing_sku(value: str) -> str:
    sku = str(value or "").strip()
    if len(sku) != 12 or not sku.isdigit():
        raise ValueError("自动生成 Makro SKU 必须是 12 位纯数字。")
    return sku


def generate_listing_sku(product_url: str) -> str:
    """Create a fresh 12-digit numeric seller SKU for one listing attempt.

    SKU is a seller-controlled identifier, not a product fact. Reusing a stable
    SKU for the same supplier URL is unsafe because Makro rejects an identifier
    that was already consumed by an earlier listing or test draft. Generate from
    a large cryptographic random space instead; callers that need the same value
    throughout one run must generate it once and pass it to
    ``generated_business_bundle``.
    """

    if not _stable_product_url(product_url):
        raise ValueError("自动生成 Makro SKU 需要有效 product URL。")

    # 100000000000..999999999999: 900 billion possible 12-digit values.
    return str(100_000_000_000 + secrets.randbelow(900_000_000_000))


def generated_business_bundle(
    product_url: str,
    *,
    sku: str | None = None,
) -> ProductSourceBundle:
    """Return only mechanical seller/business policy that needs no product reasoning."""

    bundle = ProductSourceBundle(product_url=product_url or None)
    if not product_url.strip():
        return bundle
    resolved_sku = _validated_listing_sku(sku) if sku is not None else generate_listing_sku(product_url)
    bundle.add_evidence(
        key="SKU ID",
        value=resolved_sku,
        source_type="rule",
        source_reference="generated:fresh-listing-sku",
        priority=5,
        confidence=1.0,
        evidence_text=f"Automatically generated fresh seller SKU={resolved_sku} for this listing attempt.",
        note="mechanical seller identifier; not a product attribute",
    )
    bundle.add_evidence(
        key="Warranty Summary",
        value=WARRANTY_SUMMARY_POLICY,
        source_type="rule",
        source_reference="policy:listing-content:warranty-summary",
        priority=10,
        confidence=1.0,
        evidence_text=f"Seller warranty policy Warranty Summary={WARRANTY_SUMMARY_POLICY}.",
        note="seller policy supplied for all products; not inferred from product evidence",
    )
    bundle.add_evidence(
        key="Warranty Service Type",
        value=WARRANTY_SERVICE_TYPE_POLICY,
        source_type="rule",
        source_reference="policy:listing-content:warranty-service-type",
        priority=10,
        confidence=1.0,
        evidence_text=f"Seller warranty policy Warranty Service Type={WARRANTY_SERVICE_TYPE_POLICY}",
        note="seller policy supplied for all products; not inferred from product evidence",
    )
    return bundle
