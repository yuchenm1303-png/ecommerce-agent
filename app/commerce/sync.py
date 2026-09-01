from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from typing import Any


_CHANNEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def _required(value: object, *, field: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    if len(text) > limit:
        raise ValueError(f"{field} is too long")
    return text


def _optional(value: object, *, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        raise ValueError("value is too long")
    return text


def _stable_id(kind: str, identity: str) -> str:
    raw = f"listing-studio-commerce-v1\0{kind}\0{identity}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_authoritative_listing_success(
    *,
    request_identity: str,
    supplier_url: str,
    display_name: str,
    channel: str,
    channel_account_id: str,
    channel_account_label: str,
    external_listing_id: str,
    workspace_id: str = "",
    product_type_en: str = "",
    brand: str = "",
    sku: str = "",
    source_system: str = "",
    external_product_id: str = "",
    event_id: str = "",
    occurred_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the one event allowed to activate a server ChannelListing.

    ``external_listing_id`` must be a marketplace-issued listing identifier. A
    Chromium ``makro_target_id``/page target, transient requestId, title match or
    other inferred value is not accepted by this boundary.

    Product reuse is deliberately deterministic and conservative: the same exact
    request_identity resolves to the same internal Product id; a different source
    identity never gets mechanically merged into it.
    """

    source_identity = _required(request_identity, field="request_identity", limit=4000)
    supplier = _required(supplier_url, field="supplier_url", limit=4000)
    title = _required(display_name, field="display_name", limit=500)
    normalized_channel = _required(channel, field="channel", limit=32).casefold()
    if not _CHANNEL_RE.fullmatch(normalized_channel):
        raise ValueError(f"invalid channel: {channel!r}")
    account_id = _required(channel_account_id, field="channel_account_id", limit=200)
    account_label = _required(channel_account_label, field="channel_account_label", limit=120)
    external_listing = _required(external_listing_id, field="external_listing_id", limit=300)

    product_id = _stable_id("product", source_identity)
    variant_id = _stable_id("default-variant", product_id)
    source_product_id = _stable_id("source-product", source_identity)
    listing_identity = f"{normalized_channel}\0{account_id}\0{external_listing}"
    listing_id = _stable_id("channel-listing", listing_identity)
    observed = occurred_at or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)

    output: dict[str, Any] = {
        "action": "listing_success",
        "event_id": _optional(event_id, limit=200) or uuid.uuid4().hex,
        "occurred_at": observed.astimezone(timezone.utc).isoformat(),
        "payload": {
            "product": {
                "product_id": product_id,
                "variant_id": variant_id,
                "display_name": title,
                "product_type_en": _optional(product_type_en, limit=240),
                "brand": _optional(brand, limit=200),
                "sku": _optional(sku, limit=160),
            },
            "source": {
                "source_product_id": source_product_id,
                "supplier_url": supplier,
                "request_identity": source_identity,
                "source_system": _optional(source_system, limit=120),
                "external_product_id": _optional(external_product_id, limit=240),
            },
            "channel_account": {
                "channel": normalized_channel,
                "channel_account_id": account_id,
                "label": account_label,
            },
            "listing": {
                "listing_id": listing_id,
                "external_listing_id": external_listing,
            },
        },
    }
    if str(workspace_id or "").strip():
        output["workspace_id"] = str(workspace_id).strip()
    return output


__all__ = ["build_authoritative_listing_success"]
