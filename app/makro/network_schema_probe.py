from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.sync_api import Page, Response


_SCHEMA_KEY_HINTS = (
    "attribute",
    "vertical",
    "qualifier",
    "option",
    "allowed_value",
    "valid_value",
    "value_list",
    "required",
    "mandatory",
    "schema",
    "field",
    "validation",
    "data_type",
    "input_type",
    "display_type",
)
_SCHEMA_URL_HINTS = (
    "attribute",
    "vertical",
    "schema",
    "catalog",
    "product",
    "listing",
    "graphql",
)
_SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "csrf",
    "csrf_token",
    "fk_csrf_token",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "secret",
    "session",
    "session_id",
    "seller_id",
    "sellerid",
    "user_id",
    "userid",
    "email",
    "phone",
    "mobile",
    "address",
}
_SAFE_LEAF_HINTS = (
    "attribute",
    "vertical",
    "qualifier",
    "option",
    "required",
    "mandatory",
    "schema",
    "field",
    "validation",
    "type",
    "name",
    "label",
)
_MAX_SAFE_LEAVES = 80
_MAX_STRUCTURE_DEPTH = 6
_MAX_ARRAY_SAMPLE = 3


class MakroNetworkProbeError(RuntimeError):
    """Raised when the read-only Makro network probe cannot run safely."""


def _norm_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").casefold()).strip("_")


def _is_sensitive_key(key: object) -> bool:
    normalized = _norm_key(key)
    return (
        normalized in _SENSITIVE_KEYS
        or normalized.endswith("_token")
        or normalized.endswith("_secret")
        or normalized.endswith("_password")
    )


def sanitize_url(url: str) -> str:
    """Redact secret-bearing query parameters while retaining schema identifiers."""

    parts = urlsplit(str(url or ""))
    safe_query: list[tuple[str, str]] = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        safe_query.append((key, "[REDACTED]" if _is_sensitive_key(key) else value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(safe_query), parts.fragment))


def assert_safe_makro_listing_url(url: str) -> None:
    parts = urlsplit(str(url or ""))
    if parts.scheme not in {"http", "https"} or parts.hostname != "seller.makro.co.za":
        raise MakroNetworkProbeError("网络 probe 只允许复制当前 seller.makro.co.za 页面。")
    if "addListings/single" not in (parts.path + "#" + parts.fragment):
        raise MakroNetworkProbeError(
            "当前 Makro 页面不是 Add Listing single 流程；拒绝导航未知页面。"
        )


def _redacted_scalar(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.casefold().startswith(("bearer ", "basic ")):
            return "[REDACTED]"
        return stripped[:240]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return str(type(value).__name__)


def _json_structure(value: Any, *, depth: int = 0) -> Any:
    if depth >= _MAX_STRUCTURE_DEPTH:
        return type(value).__name__
    if isinstance(value, dict):
        return {
            str(key): _json_structure(item, depth=depth + 1)
            for key, item in list(value.items())[:80]
            if not _is_sensitive_key(key)
        }
    if isinstance(value, list):
        return {
            "__type__": "list",
            "length": len(value),
            "sample": [
                _json_structure(item, depth=depth + 1)
                for item in value[:_MAX_ARRAY_SAMPLE]
            ],
        }
    return type(value).__name__


def _schema_key_match(key: object) -> bool:
    normalized = _norm_key(key)
    if not normalized or _is_sensitive_key(normalized):
        return False
    return any(hint in normalized for hint in _SCHEMA_KEY_HINTS)


def analyze_json_payload(value: Any) -> dict[str, Any]:
    """Describe schema-like JSON without persisting arbitrary product/account values."""

    matched_keys: Counter[str] = Counter()
    safe_leaves: list[dict[str, Any]] = []
    node_count = 0

    def walk(node: Any, path: str = "$", depth: int = 0) -> None:
        nonlocal node_count
        if node_count >= 20_000 or depth > 10:
            return
        node_count += 1
        if isinstance(node, dict):
            for key, item in node.items():
                if _is_sensitive_key(key):
                    continue
                key_text = str(key)
                child_path = f"{path}.{key_text}"
                if _schema_key_match(key_text):
                    matched_keys[_norm_key(key_text)] += 1
                    if (
                        len(safe_leaves) < _MAX_SAFE_LEAVES
                        and not isinstance(item, (dict, list))
                        and any(hint in _norm_key(key_text) for hint in _SAFE_LEAF_HINTS)
                    ):
                        safe_leaves.append(
                            {"path": child_path, "value": _redacted_scalar(item)}
                        )
                walk(item, child_path, depth + 1)
        elif isinstance(node, list):
            for index, item in enumerate(node[:200]):
                walk(item, f"{path}[{index}]", depth + 1)

    walk(value)

    score = 0
    for key, count in matched_keys.items():
        weight = 1
        if any(token in key for token in ("attribute", "vertical", "schema")):
            weight = 3
        elif any(token in key for token in ("qualifier", "allowed_value", "value_list")):
            weight = 2
        score += min(count, 8) * weight

    top_level_keys = (
        [str(key) for key in list(value.keys())[:100] if not _is_sensitive_key(key)]
        if isinstance(value, dict)
        else []
    )
    return {
        "score": score,
        "matched_keys": dict(matched_keys.most_common(80)),
        "top_level_keys": top_level_keys,
        "safe_schema_leaves": safe_leaves,
        "structure": _json_structure(value),
        "node_count_scanned": node_count,
    }


def _request_payload_summary(response: Response) -> dict[str, Any] | None:
    request = response.request
    raw = request.post_data
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {
            "kind": "text",
            "length": len(raw),
            "sha256": hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest(),
        }
    analysis = analyze_json_payload(parsed)
    return {
        "kind": "json",
        "score": analysis["score"],
        "matched_keys": analysis["matched_keys"],
        "safe_schema_leaves": analysis["safe_schema_leaves"],
        "structure": analysis["structure"],
    }


@dataclass(slots=True)
class NetworkResponseRecord:
    method: str
    resource_type: str
    url: str
    status: int
    content_type: str
    body_sha256: str = ""
    body_bytes: int = 0
    json_analysis: dict[str, Any] | None = None
    request_payload: dict[str, Any] | None = None
    error: str = ""

    @property
    def schema_score(self) -> int:
        body_score = int((self.json_analysis or {}).get("score") or 0)
        url_score = sum(
            1 for hint in _SCHEMA_URL_HINTS if hint in self.url.casefold()
        )
        return body_score + url_score

    def as_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "resource_type": self.resource_type,
            "url": self.url,
            "status": self.status,
            "content_type": self.content_type,
            "schema_score": self.schema_score,
            "body_sha256": self.body_sha256,
            "body_bytes": self.body_bytes,
            "json_analysis": self.json_analysis,
            "request_payload": self.request_payload,
            "error": self.error,
        }


@dataclass(slots=True)
class MakroNetworkSchemaProbe:
    page: Page
    records: list[NetworkResponseRecord] = field(default_factory=list)

    def _on_response(self, response: Response) -> None:
        request = response.request
        resource_type = str(request.resource_type or "")
        if resource_type not in {"fetch", "xhr"}:
            return

        headers = {str(k).casefold(): str(v) for k, v in (response.headers or {}).items()}
        content_type = headers.get("content-type", "")
        record = NetworkResponseRecord(
            method=str(request.method or ""),
            resource_type=resource_type,
            url=sanitize_url(response.url),
            status=int(response.status),
            content_type=content_type,
        )
        try:
            body = response.body()
            record.body_bytes = len(body)
            record.body_sha256 = hashlib.sha256(body).hexdigest()
            if "json" in content_type.casefold():
                try:
                    parsed = json.loads(body.decode("utf-8", errors="replace"))
                    record.json_analysis = analyze_json_payload(parsed)
                except (ValueError, json.JSONDecodeError):
                    pass
            record.request_payload = _request_payload_summary(response)
        except Exception as exc:  # response body can legitimately be unavailable
            record.error = f"{type(exc).__name__}: {exc}"
        self.records.append(record)

    def start(self) -> None:
        self.page.on("response", self._on_response)

    def stop(self) -> None:
        self.page.remove_listener("response", self._on_response)

    def report(self, *, source_url: str, probe_url: str) -> dict[str, Any]:
        all_records = [record.as_dict() for record in self.records]
        candidates = [
            record.as_dict()
            for record in sorted(
                self.records,
                key=lambda item: (item.schema_score, item.body_bytes),
                reverse=True,
            )
            if record.schema_score > 0
        ]

        endpoint_groups: dict[str, dict[str, Any]] = {}
        for record in self.records:
            parts = urlsplit(record.url)
            key = f"{record.method} {parts.scheme}://{parts.netloc}{parts.path}"
            bucket = endpoint_groups.setdefault(
                key,
                {
                    "method": record.method,
                    "endpoint": f"{parts.scheme}://{parts.netloc}{parts.path}",
                    "response_count": 0,
                    "max_schema_score": 0,
                    "statuses": [],
                    "content_types": [],
                },
            )
            bucket["response_count"] += 1
            bucket["max_schema_score"] = max(
                int(bucket["max_schema_score"]), record.schema_score
            )
            if record.status not in bucket["statuses"]:
                bucket["statuses"].append(record.status)
            if record.content_type and record.content_type not in bucket["content_types"]:
                bucket["content_types"].append(record.content_type)

        return {
            "contract_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "read_only_clone_current_listing_network_probe",
            "source_page_url": sanitize_url(source_url),
            "probe_page_url": sanitize_url(probe_url),
            "response_count": len(all_records),
            "candidate_count": len(candidates),
            "endpoint_groups": sorted(
                endpoint_groups.values(),
                key=lambda item: (
                    int(item["max_schema_score"]),
                    int(item["response_count"]),
                ),
                reverse=True,
            ),
            "candidates": candidates,
            "responses": all_records,
            "safety": {
                "original_page_navigated": False,
                "original_page_reloaded": False,
                "listing_writes": 0,
                "save_clicked": False,
                "send_to_qc_clicked": False,
                "request_headers_persisted": False,
                "response_bodies_persisted": False,
            },
        }


def write_probe_report(report: dict[str, Any], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target
