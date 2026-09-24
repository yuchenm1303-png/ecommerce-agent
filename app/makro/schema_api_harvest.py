from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

from playwright.sync_api import Page


MAKRO_ORIGIN = "https://seller.makro.co.za"
STEP1_URL = f"{MAKRO_ORIGIN}/index.html#dashboard/addListings/single"
CATEGORY_TREE_PATH = "/napi/listing/get-complete-category-tree"
VERTICAL_DEFINITION_PATH = "/napi/createProductV2/verticalDefinition"
VERTICAL_DEFINITION_V2_PATH = "/napi/createProductV2/verticalDefinitionV2"
VARIANT_DEFINITION_PATH = "/napi/createProductV2/v1/fetch-variant-definition"

_ALLOWED_READ_PATHS = {
    VERTICAL_DEFINITION_PATH,
    VERTICAL_DEFINITION_V2_PATH,
    VARIANT_DEFINITION_PATH,
}
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


class MakroSchemaApiHarvestError(RuntimeError):
    pass


def _norm_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _is_sensitive_key(key: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key or "").casefold()).strip("_")
    return (
        normalized in _SENSITIVE_KEYS
        or normalized.endswith("_token")
        or normalized.endswith("_secret")
        or normalized.endswith("_password")
    )


def sanitize_schema_payload(value: Any) -> Any:
    """Remove account/session fields while preserving marketplace schema data."""

    if isinstance(value, dict):
        return {
            str(key): sanitize_schema_payload(item)
            for key, item in value.items()
            if not _is_sensitive_key(key)
        }
    if isinstance(value, list):
        return [sanitize_schema_payload(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_schema_payload(item) for item in value]
    return value


def _iter_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _iter_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_dicts(item)


def _lookup_casefold(mapping: dict[str, Any], *names: str) -> Any:
    wanted = {_norm_key(name) for name in names}
    for key, value in mapping.items():
        if _norm_key(key) in wanted:
            return value
    return None


def extract_vertical_catalog(payload: Any) -> list[dict[str, str]]:
    """Extract canonical Vertical ids from Makro's complete category tree."""

    by_name: dict[str, dict[str, str]] = {}
    for node in _iter_dicts(payload):
        vertical_name = _lookup_casefold(node, "verticalName")
        if not isinstance(vertical_name, str) or not vertical_name.strip():
            continue
        canonical = vertical_name.strip()
        display = _lookup_casefold(node, "verticalDisplayName")
        description = _lookup_casefold(node, "verticalDescription")
        entry = by_name.setdefault(
            canonical,
            {
                "vertical": canonical,
                "display_name": "",
                "description": "",
            },
        )
        if isinstance(display, str) and display.strip():
            entry["display_name"] = display.strip()
        if isinstance(description, str) and description.strip():
            entry["description"] = description.strip()
    return sorted(by_name.values(), key=lambda item: item["vertical"].casefold())


def chunked(values: list[str], size: int) -> Iterable[list[str]]:
    if size <= 0:
        raise ValueError("chunk size must be positive")
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _vertical_name_from_dict(node: dict[str, Any]) -> str:
    value = _lookup_casefold(node, "verticalName", "vertical", "name")
    if isinstance(value, str):
        return value.strip()
    return ""


def split_vertical_payload(payload: Any, requested_verticals: list[str]) -> dict[str, Any]:
    """Partition a multi-Vertical response without assuming one API envelope.

    The portal has already shown both V1 and V2 schema endpoints. This function
    recognizes two safe identities only: a response dictionary keyed by the
    exact canonical Vertical id, or a nested object carrying an exact
    verticalName/vertical field. If a multi-Vertical response cannot be split
    uniquely, the caller must retry those Verticals individually.
    """

    requested = {str(item).strip() for item in requested_verticals if str(item).strip()}
    found: dict[str, Any] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                key_text = str(key).strip()
                if key_text in requested and key_text not in found:
                    found[key_text] = item
            named = _vertical_name_from_dict(node)
            if named in requested and named not in found:
                found[named] = node
            for item in node.values():
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    if len(requested) == 1 and not found:
        only = next(iter(requested))
        found[only] = payload
    return found


def extract_attribute_records(payload: Any) -> list[dict[str, Any]]:
    """Collect attribute-definition objects mechanically by attributeName."""

    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for node in _iter_dicts(payload):
        name = _lookup_casefold(node, "attributeName")
        if not isinstance(name, str) or not name.strip():
            continue
        attr_type = _lookup_casefold(node, "attributeType")
        value_type = _lookup_casefold(node, "attributeValueType")
        signature = (
            name.strip(),
            str(attr_type or ""),
            str(value_type or ""),
        )
        if signature in seen:
            continue
        seen.add(signature)
        output.append(sanitize_schema_payload(node))
    output.sort(key=lambda item: str(_lookup_casefold(item, "attributeName") or "").casefold())
    return output


def _listify_scalar_values(value: Any) -> list[str]:
    output: list[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, (str, int, float, bool)):
            text = str(node).strip()
            if text and text not in output:
                output.append(text)
        elif isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, dict):
            for key in ("value", "name", "displayValue", "displayName"):
                candidate = _lookup_casefold(node, key)
                if isinstance(candidate, (str, int, float, bool)):
                    text = str(candidate).strip()
                    if text and text not in output:
                        output.append(text)
                    return

    visit(value)
    return output


def attribute_contract(attribute: dict[str, Any]) -> dict[str, Any]:
    """Normalize only mechanical Makro schema properties; keep raw payload too."""

    qualifier_values = _lookup_casefold(attribute, "qualifierAllowedValues")
    display_value_map = _lookup_casefold(attribute, "attributeDisplayValueMap")
    dependency = _lookup_casefold(attribute, "attributeDependency")
    priority = _lookup_casefold(attribute, "attributePriority")
    max_length = _lookup_casefold(attribute, "maxAttributeValLength")
    return {
        "attribute_name": str(_lookup_casefold(attribute, "attributeName") or ""),
        "display_name": str(_lookup_casefold(attribute, "attributeDisplayName") or ""),
        "attribute_type": str(_lookup_casefold(attribute, "attributeType") or ""),
        "value_type": str(_lookup_casefold(attribute, "attributeValueType") or ""),
        "priority": priority,
        "max_length": max_length,
        "default_qualifier": _lookup_casefold(attribute, "defaultQualifier"),
        "qualifier_allowed_values": _listify_scalar_values(qualifier_values),
        "has_display_value_map": bool(display_value_map),
        "has_dependency": bool(dependency),
        "is_identifying": _lookup_casefold(attribute, "isIdentifyingAttribute"),
        "is_external_identifying": _lookup_casefold(attribute, "isExternalIdentifyingAttribute"),
        "is_itemizable": _lookup_casefold(attribute, "isItemizableAttribute"),
        "raw": sanitize_schema_payload(attribute),
    }


def _first_text(payload: Any, *keys: str) -> str:
    for node in _iter_dicts(payload):
        value = _lookup_casefold(node, *keys)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def build_vertical_registry_entry(
    *,
    vertical: str,
    catalog_item: dict[str, str] | None,
    definition: Any,
    definition_v2: Any,
    variant_definition: Any | None,
) -> dict[str, Any]:
    definition_safe = sanitize_schema_payload(definition)
    definition_v2_safe = sanitize_schema_payload(definition_v2)
    variant_safe = sanitize_schema_payload(variant_definition) if variant_definition is not None else None
    attributes = [attribute_contract(item) for item in extract_attribute_records(definition_safe)]
    variant_attributes = (
        [attribute_contract(item) for item in extract_attribute_records(variant_safe)]
        if variant_safe is not None
        else []
    )
    display_name = (
        (catalog_item or {}).get("display_name")
        or _first_text(definition_v2_safe, "verticalDisplayName")
        or _first_text(definition_safe, "verticalDisplayName")
    )
    description = (
        (catalog_item or {}).get("description")
        or _first_text(definition_v2_safe, "verticalDescription")
        or _first_text(definition_safe, "verticalDescription")
    )
    return {
        "vertical": vertical,
        "display_name": display_name,
        "description": description,
        "attribute_count": len(attributes),
        "variant_attribute_count": len(variant_attributes),
        "attributes": attributes,
        "variant_attributes": variant_attributes,
        "vertical_properties": definition_v2_safe,
        "variant_definition": variant_safe,
    }


def build_global_catalog(vertical_entries: Iterable[dict[str, Any]]) -> dict[str, Any]:
    catalog: dict[str, dict[str, Any]] = {}
    family_counts: Counter[str] = Counter()
    for vertical_entry in vertical_entries:
        vertical = str(vertical_entry.get("vertical") or "")
        for source_name in ("attributes", "variant_attributes"):
            for attribute in vertical_entry.get(source_name) or []:
                name = str(attribute.get("attribute_name") or "")
                if not name:
                    continue
                item = catalog.setdefault(
                    name,
                    {
                        "attribute_name": name,
                        "display_names": [],
                        "verticals": [],
                        "variant_verticals": [],
                        "attribute_types": [],
                        "value_types": [],
                        "priorities": [],
                        "qualifier_allowed_values": [],
                    },
                )
                for bucket, value in (
                    ("display_names", attribute.get("display_name")),
                    ("attribute_types", attribute.get("attribute_type")),
                    ("value_types", attribute.get("value_type")),
                    ("priorities", attribute.get("priority")),
                ):
                    text = str(value or "").strip()
                    if text and text not in item[bucket]:
                        item[bucket].append(text)
                if vertical and vertical not in item["verticals"]:
                    item["verticals"].append(vertical)
                if source_name == "variant_attributes" and vertical and vertical not in item["variant_verticals"]:
                    item["variant_verticals"].append(vertical)
                for value in attribute.get("qualifier_allowed_values") or []:
                    if value not in item["qualifier_allowed_values"]:
                        item["qualifier_allowed_values"].append(value)
                family = "|".join(
                    [
                        str(attribute.get("attribute_type") or "<none>"),
                        str(attribute.get("value_type") or "<none>"),
                        "qualifier" if attribute.get("qualifier_allowed_values") else "no_qualifier",
                        "dependent" if attribute.get("has_dependency") else "independent",
                    ]
                )
                family_counts[family] += 1
    return {
        "attributes": dict(sorted(catalog.items(), key=lambda item: item[0].casefold())),
        "field_families": dict(sorted(family_counts.items())),
    }


def build_registry(
    *,
    catalog: list[dict[str, str]],
    vertical_entries: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    batch_fallbacks: list[dict[str, Any]],
    variants_included: bool,
) -> dict[str, Any]:
    entries_by_vertical = {
        str(item.get("vertical")): item
        for item in vertical_entries
        if str(item.get("vertical") or "")
    }
    attribute_occurrences = sum(int(item.get("attribute_count") or 0) for item in vertical_entries)
    variant_occurrences = sum(int(item.get("variant_attribute_count") or 0) for item in vertical_entries)
    global_catalog = build_global_catalog(vertical_entries)
    return {
        "contract_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "catalog": CATEGORY_TREE_PATH,
            "field_schema": VERTICAL_DEFINITION_PATH,
            "vertical_properties": VERTICAL_DEFINITION_V2_PATH,
            "variant_schema": VARIANT_DEFINITION_PATH if variants_included else None,
            "transport": "same-origin authenticated browser GET; credentials remain inside Edge",
        },
        "stats": {
            "catalog_vertical_count": len(catalog),
            "harvested_vertical_count": len(vertical_entries),
            "failed_vertical_count": len({str(item.get('vertical') or '') for item in failures if item.get('vertical')}),
            "attribute_occurrence_count": attribute_occurrences,
            "variant_attribute_occurrence_count": variant_occurrences,
            "unique_attribute_count": len(global_catalog["attributes"]),
            "field_family_count": len(global_catalog["field_families"]),
            "batch_fallback_count": len(batch_fallbacks),
        },
        "vertical_catalog": catalog,
        "verticals": dict(sorted(entries_by_vertical.items(), key=lambda item: item[0].casefold())),
        "field_catalog": global_catalog["attributes"],
        "field_families": global_catalog["field_families"],
        "failures": failures,
        "batch_fallbacks": batch_fallbacks,
        "safety": {
            "original_listing_navigated": False,
            "original_listing_reloaded": False,
            "listing_writes": 0,
            "save_clicked": False,
            "send_to_qc_clicked": False,
            "request_headers_persisted": False,
            "cookies_persisted": False,
            "csrf_persisted": False,
        },
    }


def _assert_read_path(path: str) -> None:
    base = str(path).split("?", 1)[0]
    if base not in _ALLOWED_READ_PATHS:
        raise MakroSchemaApiHarvestError(f"拒绝调用未白名单 Makro endpoint: {base}")


def fetch_json(page: Page, path: str, *, timeout_ms: int = 45_000) -> dict[str, Any]:
    """GET one whitelisted same-origin endpoint without exposing session credentials."""

    _assert_read_path(path)
    result = page.evaluate(
        """
        async ({path, timeoutMs}) => {
          const controller = new AbortController();
          const timer = setTimeout(() => controller.abort(), timeoutMs);
          try {
            const response = await fetch(path, {
              method: 'GET',
              credentials: 'include',
              headers: {'accept': 'application/json'},
              signal: controller.signal,
            });
            const text = await response.text();
            let jsonValue = null;
            let jsonError = '';
            try { jsonValue = text ? JSON.parse(text) : null; }
            catch (error) { jsonError = String(error); }
            return {
              status: response.status,
              ok: response.ok,
              contentType: response.headers.get('content-type') || '',
              json: jsonValue,
              jsonError,
              bodyLength: text.length,
            };
          } finally {
            clearTimeout(timer);
          }
        }
        """,
        {"path": path, "timeoutMs": int(timeout_ms)},
    )
    if not isinstance(result, dict):
        raise MakroSchemaApiHarvestError(f"Makro endpoint 返回不可识别结果: {path}")
    if not result.get("ok"):
        raise MakroSchemaApiHarvestError(
            f"Makro endpoint HTTP {result.get('status')}: {path}"
        )
    if result.get("jsonError"):
        raise MakroSchemaApiHarvestError(
            f"Makro endpoint 非 JSON: {path}: {result.get('jsonError')}"
        )
    return {
        "status": int(result.get("status") or 0),
        "content_type": str(result.get("contentType") or ""),
        "body_length": int(result.get("bodyLength") or 0),
        "payload": sanitize_schema_payload(result.get("json")),
    }


def fetch_many_json(
    page: Page,
    paths: list[str],
    *,
    concurrency: int = 4,
    timeout_ms: int = 45_000,
) -> list[dict[str, Any]]:
    for path in paths:
        _assert_read_path(path)
    if concurrency <= 0 or concurrency > 8:
        raise ValueError("concurrency must be between 1 and 8")
    result = page.evaluate(
        """
        async ({paths, concurrency, timeoutMs}) => {
          const out = new Array(paths.length);
          let nextIndex = 0;
          async function worker() {
            while (true) {
              const index = nextIndex++;
              if (index >= paths.length) return;
              const path = paths[index];
              const controller = new AbortController();
              const timer = setTimeout(() => controller.abort(), timeoutMs);
              try {
                const response = await fetch(path, {
                  method: 'GET',
                  credentials: 'include',
                  headers: {'accept': 'application/json'},
                  signal: controller.signal,
                });
                const text = await response.text();
                let jsonValue = null;
                let jsonError = '';
                try { jsonValue = text ? JSON.parse(text) : null; }
                catch (error) { jsonError = String(error); }
                out[index] = {
                  path, status: response.status, ok: response.ok,
                  contentType: response.headers.get('content-type') || '',
                  json: jsonValue, jsonError, bodyLength: text.length,
                };
              } catch (error) {
                out[index] = {path, status: 0, ok: false, error: String(error)};
              } finally {
                clearTimeout(timer);
              }
            }
          }
          await Promise.all(Array.from({length: Math.min(concurrency, paths.length)}, worker));
          return out;
        }
        """,
        {
            "paths": paths,
            "concurrency": int(concurrency),
            "timeoutMs": int(timeout_ms),
        },
    )
    output: list[dict[str, Any]] = []
    for item in result or []:
        output.append(
            {
                "path": str((item or {}).get("path") or ""),
                "status": int((item or {}).get("status") or 0),
                "ok": bool((item or {}).get("ok")),
                "content_type": str((item or {}).get("contentType") or ""),
                "body_length": int((item or {}).get("bodyLength") or 0),
                "json_error": str((item or {}).get("jsonError") or (item or {}).get("error") or ""),
                "payload": sanitize_schema_payload((item or {}).get("json")),
            }
        )
    return output


def vertical_definition_path(verticals: list[str]) -> str:
    encoded = quote(",".join(verticals), safe=",_")
    return f"{VERTICAL_DEFINITION_PATH}?verticals={encoded}"


def vertical_definition_v2_path(verticals: list[str]) -> str:
    encoded = quote(",".join(verticals), safe=",_")
    return f"{VERTICAL_DEFINITION_V2_PATH}?verticals={encoded}&context=VERTICAL_PROP"


def variant_definition_path(vertical: str) -> str:
    return f"{VARIANT_DEFINITION_PATH}?vertical={quote(vertical, safe='_')}"


@dataclass
class BatchFetchResult:
    by_vertical: dict[str, Any] = field(default_factory=dict)
    failures: list[dict[str, Any]] = field(default_factory=list)
    batch_fallbacks: list[dict[str, Any]] = field(default_factory=list)


def fetch_partitioned_endpoint(
    page: Page,
    verticals: list[str],
    *,
    path_builder,
    batch_size: int,
    endpoint_name: str,
) -> BatchFetchResult:
    result = BatchFetchResult()
    for batch in chunked(verticals, batch_size):
        try:
            response = fetch_json(page, path_builder(batch))
            split = split_vertical_payload(response["payload"], batch)
        except Exception as exc:
            split = {}
            result.batch_fallbacks.append(
                {"endpoint": endpoint_name, "verticals": list(batch), "reason": f"{type(exc).__name__}: {exc}"}
            )

        missing = [vertical for vertical in batch if vertical not in split]
        if not missing:
            result.by_vertical.update(split)
            continue

        # Keep any identities that were unambiguous in the batch response, then
        # fail closed to one Vertical per GET only for unresolved members.
        result.by_vertical.update({key: value for key, value in split.items() if key in batch})
        result.batch_fallbacks.append(
            {
                "endpoint": endpoint_name,
                "verticals": list(batch),
                "reason": f"batch response could not be uniquely partitioned; retrying {len(missing)} individually",
            }
        )
        for vertical in missing:
            try:
                single = fetch_json(page, path_builder([vertical]))
                separated = split_vertical_payload(single["payload"], [vertical])
                if vertical not in separated:
                    raise MakroSchemaApiHarvestError("single-Vertical response still lacks exact identity")
                result.by_vertical[vertical] = separated[vertical]
            except Exception as exc:
                result.failures.append(
                    {"endpoint": endpoint_name, "vertical": vertical, "error": f"{type(exc).__name__}: {exc}"}
                )
    return result


def write_registry(payload: dict[str, Any], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target
