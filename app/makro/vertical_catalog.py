from __future__ import annotations

import csv
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from playwright.sync_api import Page

from .listing import parse_makro_listing_url
from .listing_creation import (
    MAKRO_NEW_LISTING_URL,
    _vertical_confirmation_content,
    is_brand_step,
    is_product_info_step,
)
from .taxonomy_resilient import ResilientMakroTaxonomyBrowser
from .vertical_selection import is_vertical_interaction_ready


CATALOG_SCHEMA_VERSION = 1
CHECKPOINT_NAME = "vertical-catalog-checkpoint.json"
CATALOG_NAME = "makro-vertical-catalog.json"
LEAVES_CSV_NAME = "makro-vertical-leaves.csv"


def _clean_label(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def path_key(path: list[str] | tuple[str, ...]) -> str:
    return "\x1f".join(_clean_label(item).casefold() for item in path if _clean_label(item))


def _column_signature(values: list[str] | None) -> tuple[str, ...]:
    return tuple(_clean_label(item).casefold() for item in (values or []) if _clean_label(item))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_catalog_state(*, source_url: str = MAKRO_NEW_LISTING_URL) -> dict[str, Any]:
    now = _utc_now()
    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "source_url": str(source_url),
        "started_at": now,
        "updated_at": now,
        "completed_at": "",
        "roots": [],
        "pending": [],
        "branches": [],
        "leaves": [],
        "failed": [],
        "error_history": [],
        "processed_count": 0,
    }


def _resolved_path_keys(state: dict[str, Any]) -> set[str]:
    output: set[str] = set()
    for bucket in ("branches", "leaves"):
        for item in state.get(bucket) or []:
            key = path_key(list(item.get("path") or []))
            if key:
                output.add(key)
    return output


def prepare_resume_state(state: dict[str, Any]) -> dict[str, Any]:
    """Requeue unresolved paths from an interrupted or failed harvest."""

    resolved = _resolved_path_keys(state)
    pending: list[list[str]] = []
    seen = set(resolved)

    def add(raw_path: Any) -> None:
        path = [_clean_label(item) for item in (raw_path or []) if _clean_label(item)]
        key = path_key(path)
        if not key or key in seen:
            return
        seen.add(key)
        pending.append(path)

    for path in state.get("pending") or []:
        add(path)
    for item in state.get("failed") or []:
        add(item.get("path"))

    state["pending"] = pending
    state["failed"] = []
    state["completed_at"] = ""
    state["updated_at"] = _utc_now()
    return state


def load_checkpoint(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Makro vertical catalog checkpoint must be a JSON object")
    if int(payload.get("schema_version") or 0) != CATALOG_SCHEMA_VERSION:
        raise ValueError(
            "unsupported Makro vertical catalog checkpoint schema_version="
            f"{payload.get('schema_version')!r}"
        )
    return prepare_resume_state(payload)


def save_checkpoint(state: dict[str, Any], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = _utc_now()
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(target)
    return target


def _insert_tree_node(
    root: list[dict[str, Any]],
    path: list[str],
    leaf: dict[str, Any] | None,
) -> None:
    children = root
    for index, label in enumerate(path):
        matches = [
            node
            for node in children
            if _clean_label(node.get("label")).casefold() == label.casefold()
        ]
        if matches:
            node = matches[0]
        else:
            node = {
                "label": label,
                "kind": "branch",
                "canonical_vertical": "",
                "children": [],
            }
            children.append(node)
        if leaf is not None and index == len(path) - 1:
            node["kind"] = "leaf"
            node["canonical_vertical"] = str(leaf.get("canonical_vertical") or "")
            node["children"] = []
        children = node["children"]


def build_catalog_payload(state: dict[str, Any]) -> dict[str, Any]:
    tree: list[dict[str, Any]] = []
    leaves: list[dict[str, Any]] = []
    for raw in state.get("leaves") or []:
        path = [_clean_label(item) for item in (raw.get("path") or []) if _clean_label(item)]
        canonical = _clean_label(raw.get("canonical_vertical"))
        if not path or not canonical:
            continue
        item = {
            "label": path[-1],
            "path": path,
            "canonical_vertical": canonical,
        }
        leaves.append(item)
        _insert_tree_node(tree, path, item)

    # Keep already discovered branch-only structure visible in partial catalogs.
    for raw in state.get("branches") or []:
        path = [_clean_label(item) for item in (raw.get("path") or []) if _clean_label(item)]
        if path:
            _insert_tree_node(tree, path, None)

    leaves.sort(key=lambda item: tuple(part.casefold() for part in item["path"]))
    canonical_index: dict[str, list[list[str]]] = {}
    for leaf in leaves:
        canonical_index.setdefault(leaf["canonical_vertical"], []).append(list(leaf["path"]))

    pending_count = len(state.get("pending") or [])
    failed_count = len(state.get("failed") or [])
    complete = pending_count == 0 and failed_count == 0
    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "generated_at": _utc_now(),
        "source_url": str(state.get("source_url") or MAKRO_NEW_LISTING_URL),
        "complete": complete,
        "stats": {
            "root_count": len(state.get("roots") or []),
            "branch_count": len(state.get("branches") or []),
            "leaf_path_count": len(leaves),
            "unique_vertical_count": len(canonical_index),
            "pending_count": pending_count,
            "failed_count": failed_count,
            "processed_count": int(state.get("processed_count") or 0),
        },
        "roots": list(state.get("roots") or []),
        "tree": tree,
        "leaves": leaves,
        "canonical_index": canonical_index,
        "safety": {
            "dedicated_probe_tab": True,
            "brand_selected": False,
            "listing_created": False,
            "step3_writes": 0,
            "save_clicked": False,
            "send_to_qc_clicked": False,
            "long_lived_makro_edge_closed": False,
        },
    }


def write_catalog_outputs(
    state: dict[str, Any],
    output_dir: str | Path,
) -> tuple[Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    payload = build_catalog_payload(state)

    catalog_path = root / CATALOG_NAME
    temp = catalog_path.with_suffix(catalog_path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(catalog_path)

    csv_path = root / LEAVES_CSV_NAME
    csv_temp = csv_path.with_suffix(csv_path.suffix + ".tmp")
    with csv_temp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["canonical_vertical", "leaf_label", "taxonomy_path", "depth"])
        for leaf in payload["leaves"]:
            writer.writerow(
                [
                    leaf["canonical_vertical"],
                    leaf["label"],
                    " / ".join(leaf["path"]),
                    len(leaf["path"]),
                ]
            )
    csv_temp.replace(csv_path)
    return catalog_path, csv_path


def _canonical_vertical(page: Page) -> str:
    try:
        target = parse_makro_listing_url(str(page.url or ""))
    except (ValueError, AttributeError):
        return ""
    return _clean_label(target.vertical)


def _leaf_state(page: Page) -> bool:
    canonical = _canonical_vertical(page)
    if not canonical:
        return False
    try:
        return bool(_vertical_confirmation_content(page) or is_brand_step(page))
    except Exception:
        return bool(canonical)


def _wait_until(
    page: Page,
    predicate: Callable[[], bool],
    *,
    timeout_s: float,
    poll_ms: int = 200,
) -> bool:
    deadline = time.monotonic() + max(0.5, float(timeout_s))
    while time.monotonic() < deadline:
        if predicate():
            return True
        page.wait_for_timeout(max(50, int(poll_ms)))
    return bool(predicate())


def reset_probe_to_step1(page: Page, *, timeout_s: float = 30.0) -> None:
    page.goto(
        MAKRO_NEW_LISTING_URL,
        wait_until="domcontentloaded",
        timeout=int(max(5.0, timeout_s) * 1000),
    )

    def ready() -> bool:
        try:
            return bool(
                is_vertical_interaction_ready(page)
                and not is_brand_step(page)
                and not is_product_info_step(page)
            )
        except Exception:
            return False

    if not _wait_until(page, ready, timeout_s=timeout_s, poll_ms=250):
        raise RuntimeError(
            "dedicated Makro catalog probe tab did not reach an operable Step 1 taxonomy state"
        )


def _wait_for_click_outcome(
    page: Page,
    browser: ResilientMakroTaxonomyBrowser,
    *,
    level: int,
    previous_child: tuple[str, ...],
    timeout_s: float,
    max_items_per_level: int,
) -> tuple[str, list[str], str]:
    deadline = time.monotonic() + max(1.0, float(timeout_s))
    while time.monotonic() < deadline:
        canonical = _canonical_vertical(page)
        if canonical and _leaf_state(page):
            return "leaf", [], canonical

        columns = browser.columns(max_items_per_level=max_items_per_level)
        child = list(columns[level + 1]) if level + 1 < len(columns) else []
        signature = _column_signature(child)
        if child and signature != previous_child:
            return "branch", child, ""
        page.wait_for_timeout(180)

    canonical = _canonical_vertical(page)
    if canonical:
        return "leaf", [], canonical
    columns = browser.columns(max_items_per_level=max_items_per_level)
    child = list(columns[level + 1]) if level + 1 < len(columns) else []
    if child and _column_signature(child) != previous_child:
        return "branch", child, ""
    raise RuntimeError(
        f"Makro taxonomy click produced neither a child column nor a canonical leaf at level={level}"
    )


def inspect_taxonomy_path(
    page: Page,
    path: list[str],
    *,
    step1_timeout_s: float = 30.0,
    transition_timeout_s: float = 8.0,
    max_items_per_level: int = 200,
) -> dict[str, Any]:
    wanted_path = [_clean_label(item) for item in path if _clean_label(item)]
    if not wanted_path:
        raise ValueError("taxonomy path cannot be empty")

    reset_probe_to_step1(page, timeout_s=step1_timeout_s)
    browser = ResilientMakroTaxonomyBrowser(page)

    for level, wanted in enumerate(wanted_path):
        columns = browser.columns(max_items_per_level=max_items_per_level)
        if level >= len(columns):
            raise RuntimeError(
                f"Makro taxonomy path disappeared before level={level}: {wanted_path!r}"
            )
        matches = [
            item
            for item in columns[level]
            if _clean_label(item).casefold() == wanted.casefold()
        ]
        if len(matches) != 1:
            raise RuntimeError(
                "Makro taxonomy path node is not one unique live candidate: "
                f"level={level}, wanted={wanted!r}, matches={matches!r}"
            )
        selected = matches[0]
        before_child = (
            _column_signature(columns[level + 1])
            if level + 1 < len(columns)
            else ()
        )
        if not browser.click_node(
            level,
            selected,
            max_items_per_level=max_items_per_level,
        ):
            raise RuntimeError(
                f"Makro taxonomy probe could not click exact live node level={level}: {selected!r}"
            )

        kind, children, canonical = _wait_for_click_outcome(
            page,
            browser,
            level=level,
            previous_child=before_child,
            timeout_s=transition_timeout_s,
            max_items_per_level=max_items_per_level,
        )
        final = level == len(wanted_path) - 1
        if kind == "leaf":
            if not final:
                raise RuntimeError(
                    "Makro taxonomy replay reached a leaf before the requested path ended: "
                    f"path={wanted_path!r}, level={level}, canonical={canonical!r}"
                )
            return {
                "kind": "leaf",
                "path": wanted_path,
                "label": selected,
                "canonical_vertical": canonical,
            }

        if final:
            clean_children: list[str] = []
            seen: set[str] = set()
            for raw in children:
                value = _clean_label(raw)
                key = value.casefold()
                if not value or key in seen:
                    continue
                seen.add(key)
                clean_children.append(value)
            if not clean_children:
                raise RuntimeError(
                    f"Makro taxonomy branch returned an empty child set: {wanted_path!r}"
                )
            return {
                "kind": "branch",
                "path": wanted_path,
                "label": selected,
                "children": clean_children,
            }

    raise RuntimeError(
        f"Makro taxonomy path inspection ended without a result: {wanted_path!r}"
    )


def harvest_vertical_catalog(
    page: Page,
    output_dir: str | Path,
    *,
    resume: bool = True,
    max_paths: int = 0,
    step1_timeout_s: float = 30.0,
    transition_timeout_s: float = 8.0,
    retries: int = 2,
    max_items_per_level: int = 200,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Harvest the whole live taxonomy in a disposable, resumable probe tab.

    A leaf click may temporarily advance only the dedicated probe tab to the
    brand-selection boundary so the canonical Vertical can be read from the URL.
    The harvester never selects a brand, creates a listing, writes Step 3, saves,
    or clicks Send to QC.
    """

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    checkpoint = root / CHECKPOINT_NAME

    if resume and checkpoint.exists():
        state = load_checkpoint(checkpoint)
    else:
        state = new_catalog_state()
        reset_probe_to_step1(page, timeout_s=step1_timeout_s)
        columns = ResilientMakroTaxonomyBrowser(page).columns(
            max_items_per_level=max_items_per_level
        )
        if not columns or not columns[0]:
            raise RuntimeError("Makro Step 1 exposed no live root taxonomy nodes")
        roots: list[str] = []
        seen_roots: set[str] = set()
        for raw in columns[0]:
            value = _clean_label(raw)
            key = value.casefold()
            if not value or key in seen_roots:
                continue
            seen_roots.add(key)
            roots.append(value)
        state["roots"] = roots
        state["pending"] = [[item] for item in roots]
        save_checkpoint(state, checkpoint)

    resolved = _resolved_path_keys(state)
    queued = {
        path_key(path)
        for path in state.get("pending") or []
        if path_key(path)
    }
    processed_this_run = 0

    while state["pending"] and (
        max_paths <= 0 or processed_this_run < max_paths
    ):
        path = list(state["pending"].pop(0))
        key = path_key(path)
        queued.discard(key)
        if not key or key in resolved:
            continue

        result: dict[str, Any] | None = None
        last_error = ""
        attempts = max(1, int(retries) + 1)
        for attempt in range(1, attempts + 1):
            try:
                result = inspect_taxonomy_path(
                    page,
                    path,
                    step1_timeout_s=step1_timeout_s,
                    transition_timeout_s=transition_timeout_s,
                    max_items_per_level=max_items_per_level,
                )
                break
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                state["error_history"].append(
                    {
                        "at": _utc_now(),
                        "path": path,
                        "attempt": attempt,
                        "error": last_error,
                    }
                )

        if result is None:
            state["failed"].append(
                {
                    "path": path,
                    "attempts": attempts,
                    "error": last_error,
                }
            )
            status = "failed"
        elif result["kind"] == "leaf":
            state["leaves"].append(result)
            resolved.add(key)
            status = "leaf"
        else:
            state["branches"].append(result)
            resolved.add(key)
            status = "branch"
            for child in result.get("children") or []:
                child_path = [*path, child]
                child_key = path_key(child_path)
                if not child_key or child_key in resolved or child_key in queued:
                    continue
                state["pending"].append(child_path)
                queued.add(child_key)

        processed_this_run += 1
        state["processed_count"] = int(state.get("processed_count") or 0) + 1
        save_checkpoint(state, checkpoint)
        write_catalog_outputs(state, root)

        if progress is not None:
            progress(
                {
                    "status": status,
                    "path": path,
                    "processed_this_run": processed_this_run,
                    "processed_total": state["processed_count"],
                    "pending": len(state["pending"]),
                    "leaves": len(state["leaves"]),
                    "branches": len(state["branches"]),
                    "failed": len(state["failed"]),
                    "error": last_error if status == "failed" else "",
                }
            )

    complete = not state["pending"] and not state["failed"]
    if complete:
        state["completed_at"] = _utc_now()
    save_checkpoint(state, checkpoint)
    catalog_path, csv_path = write_catalog_outputs(state, root)
    payload = build_catalog_payload(state)
    payload["catalog_path"] = str(catalog_path.resolve())
    payload["csv_path"] = str(csv_path.resolve())
    payload["checkpoint_path"] = str(checkpoint.resolve())
    return payload


__all__ = [
    "CATALOG_NAME",
    "CHECKPOINT_NAME",
    "LEAVES_CSV_NAME",
    "build_catalog_payload",
    "harvest_vertical_catalog",
    "inspect_taxonomy_path",
    "load_checkpoint",
    "new_catalog_state",
    "path_key",
    "prepare_resume_state",
    "reset_probe_to_step1",
    "save_checkpoint",
    "write_catalog_outputs",
]
