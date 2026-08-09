from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit

from .source_bundle import ProductSourceBundle, normalize_key


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


def generate_listing_sku(product_url: str) -> str:
    """Create a stable numeric seller SKU from the exact source URL.

    SKU is a seller-controlled identifier, not a product fact.  It therefore
    does not belong in the AI semantic path.  The same source URL yields the
    same 12-digit value across resolver/planner/executor reruns.
    """

    stable = _stable_product_url(product_url)
    if not stable:
        raise ValueError("自动生成 Makro SKU 需要有效 product URL。")

    digest = hashlib.sha256(stable.encode("utf-8")).hexdigest()
    match = re.search(r"/offer/(\d+)(?:\.html)?$", urlsplit(stable).path, flags=re.IGNORECASE)
    if match:
        prefix = match.group(1)[-6:].zfill(6)
        suffix = f"{int(digest[:12], 16) % 1_000_000:06d}"
        return prefix + suffix

    return f"{int(digest[:24], 16) % 900_000_000_000 + 100_000_000_000:012d}"


def generated_business_bundle(product_url: str) -> ProductSourceBundle:
    """Return only mechanical business defaults that do not require product reasoning."""

    bundle = ProductSourceBundle(product_url=product_url or None)
    if not product_url.strip():
        return bundle
    sku = generate_listing_sku(product_url)
    bundle.add_evidence(
        key="SKU ID",
        value=sku,
        source_type="rule",
        source_reference="generated:product-url-sku",
        priority=5,
        confidence=1.0,
        evidence_text=f"Automatically generated seller SKU={sku} from exact source product URL.",
        note="mechanical seller identifier; not a product attribute",
    )
    return bundle
