from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


def supplier_request_identity(value: str) -> str:
    """Return the exact HTTP request identity for one supplier product URL.

    Only scheme and host are case-insensitive. Path, query spelling/order, and
    trailing slash are preserved because supplier sites may use any of them to
    select a SKU or variant. Fragment is browser-local navigation state and is
    intentionally excluded from product request identity.
    """

    url = str(value or "").strip()
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"不是完整 http(s) 商品链接：{url}")
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path,
            parsed.query,
            "",
        )
    )


__all__ = ["supplier_request_identity"]
