from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from app.fill_plan import LiveFillPlan
from app.makro.domain import MakroDomainAdapter
from app.makro.photos import _photo_state, _select_file_input, _stage_accepted
from makro_preview_listing import (
    _base_result_payload,
    _item_identity,
    _open_and_index_section,
    _safe_name,
    _section_candidates,
    _verify_saved_values,
    execution_answer_for_item,
    preview_mode_for_item,
)

PRODUCT_PHOTOS = "Product Photos"
DIAGNOSTIC_SCREENSHOT_TIMEOUT_MS = 5_000


def _capture_diagnostic_screenshot(
    adapter: MakroDomainAdapter,
    path: Path,
    report: dict[str, Any],
    key: str,
) -> None:
    """Capture a best-effort diagnostic without changing transaction outcome."""

    try:
        adapter.page.screenshot(
            path=str(path),
            full_page=True,
            timeout=DIAGNOSTIC_SCREENSHOT_TIMEOUT_MS,
        )
        report[key] = str(path.resolve())
    except Exception as exc:
        report[f"{key}_error"] = str(exc)


def _collect_save_failure_diagnostics(
    adapter: MakroDomainAdapter,
    section_title: str,
) -> dict[str, Any]:
    """Collect cheap live evidence after Makro rejects a section Save."""

    diagnostics: dict[str, Any] = {
        "visible_errors": [],
        "error_text_candidates": [],
        "invalid_controls": [],
    }
    live = adapter.find_section(section_title)
    if live is None:
        diagnostics["detail"] = "Save 失败后找不到目标 section。"
        return diagnostics

    path = str(live.get("path") or "")
    if not path:
        diagnostics["detail"] = "Save 失败后的 section 缺少稳定 DOM path。"
        return diagnostics

    diagnostics["visible_errors"] = adapter.visible_section_errors(path)
    card = adapter.page.locator(path)

    try:
        text = card.inner_text(timeout=3_000)
        keywords = (
            "error",
            "required",
            "invalid",
            "please",
            "must",
            "cannot",
            "can't",
            "minimum",
            "maximum",
            "greater",
            "less than",
            "upload",
        )
        lines: list[str] = []
        for raw in str(text or "").splitlines():
            clean = " ".join(raw.split()).strip()
            if not clean:
                continue
            folded = clean.casefold()
            if any(token in folded for token in keywords) and clean not in lines:
                lines.append(clean)
        diagnostics["error_text_candidates"] = lines[:40]
    except Exception as exc:
        diagnostics["section_text_error"] = str(exc)

    try:
        invalid = card.locator(
            'input:invalid, textarea:invalid, select:invalid, [aria-invalid="true"]'
        )
        count = min(invalid.count(), 40)
        controls: list[dict[str, Any]] = []
        for index in range(count):
            node = invalid.nth(index)
            item: dict[str, Any] = {
                "name": node.get_attribute("name"),
                "id": node.get_attribute("id"),
                "type": node.get_attribute("type"),
                "aria_invalid": node.get_attribute("aria-invalid"),
                "aria_describedby": node.get_attribute("aria-describedby"),
            }
            try:
                item["value"] = node.input_value(timeout=1_000)
            except Exception:
                try:
                    item["value"] = node.get_attribute("value")
                except Exception:
                    item["value"] = None
            try:
                item["validation_message"] = node.evaluate(
                    "el => el.validationMessage || ''"
                )
            except Exception:
                item["validation_message"] = ""
            controls.append(item)
        diagnostics["invalid_controls"] = controls
    except Exception as exc:
        diagnostics["invalid_control_scan_error"] = str(exc)

    return diagnostics


def fill_one_section(
    adapter: MakroDomainAdapter,
    plan: LiveFillPlan,
    section_title: str,
    *,
    include_review_candidates: bool,
    persist: bool,
    scroll_wait_ms: int,
    max_scroll_steps: int,
    recheck_wait_ms: int,
    run_dir: Path,
) -> dict[str, Any]:
    """Execute every READY candidate once it uniquely binds to the live field.

    READY is the upstream write decision. The executor does not re-decide the
    business/AI question by inspecting whether Makro currently renders a value.
    Existing DOM values, default units and unchecked radio values therefore can
    never silently turn READY into skipped_existing.
    """

    candidates = _section_candidates(
        plan,
        section_title,
        include_review_candidates=include_review_candidates,
    )
    report: dict[str, Any] = {
        "section": section_title,
        "candidate_count": len(candidates),
        "writes_attempted": 0,
        "review_candidates_attempted": 0,
        "validated": 0,
        "validation_failed": 0,
        "fill_error": 0,
        "skipped_existing": 0,
        "skipped_live_match": 0,
        "save_attempted": False,
        "saved": False,
        "persisted_verified": 0,
        "review_candidates_persisted": 0,
        "persisted_validation_failed": 0,
        "results": [],
        "persisted_verifications": [],
    }
    if not candidates:
        report["status"] = "no_candidates"
        return report

    try:
        section_path, live = _open_and_index_section(
            adapter,
            section_title,
            wait_ms=scroll_wait_ms,
            max_scroll_steps=max_scroll_steps,
        )
    except Exception as exc:
        report["status"] = "section_error"
        report["detail"] = str(exc)
        return report

    validated_identities: set[tuple[str, str, str]] = set()
    for item in candidates:
        mode = preview_mode_for_item(
            item,
            include_review_candidates=include_review_candidates,
        )
        base_payload = _base_result_payload(item, mode)
        identity = _item_identity(item)
        matches = live.get(identity, [])
        if len(matches) != 1:
            report["skipped_live_match"] += 1
            report["results"].append(
                {
                    **base_payload,
                    "execution_status": "skipped_live_match",
                    "detail": f"展开 section 后 live field 匹配数={len(matches)}，期望恰好 1。",
                }
            )
            continue

        answer = execution_answer_for_item(
            item,
            include_review_candidates=include_review_candidates,
        )
        report["writes_attempted"] += 1
        if mode == "review":
            report["review_candidates_attempted"] += 1
        verification = adapter.fill_resolved_field(
            matches[0],
            answer,
            section_path=section_path,
            recheck_wait_ms=recheck_wait_ms,
        )
        if verification.status == "validated":
            report["validated"] += 1
            validated_identities.add(identity)
        elif verification.status == "fill_error":
            report["fill_error"] += 1
        else:
            report["validation_failed"] += 1
        report["results"].append(
            {
                **base_payload,
                "execution_status": verification.status,
                "verification": verification.as_dict(),
            }
        )

    before_save = run_dir / f"{_safe_name(section_title)}-before-save.png"
    _capture_diagnostic_screenshot(
        adapter,
        before_save,
        report,
        "screenshot_before_save",
    )

    if not persist:
        report["status"] = "preview_open"
        return report

    report["save_attempted"] = True
    try:
        adapter.save_section(section_title)
        report["saved"] = True
        persisted, errors = _verify_saved_values(
            adapter,
            candidates,
            validated_identities,
            section_title,
            include_review_candidates=include_review_candidates,
            wait_ms=scroll_wait_ms,
            max_scroll_steps=max_scroll_steps,
        )
        report["persisted_verifications"] = persisted
        report["persisted_verified"] = sum(
            1 for item in persisted if item.get("status") == "persisted_verified"
        )
        report["review_candidates_persisted"] = sum(
            1
            for item in persisted
            if item.get("status") == "persisted_verified"
            and item.get("preview_mode") == "review"
        )
        report["persisted_validation_failed"] = len(persisted) - report["persisted_verified"]
        report["post_save_errors"] = errors
    except Exception as exc:
        report["status"] = "save_failed"
        report["save_error"] = str(exc)
        diagnostics = _collect_save_failure_diagnostics(adapter, section_title)
        report["save_failure_diagnostics"] = diagnostics
        report["visible_errors_after_save_failure"] = diagnostics.get(
            "visible_errors", []
        )
        failed_shot = run_dir / f"{_safe_name(section_title)}-save-failed.png"
        _capture_diagnostic_screenshot(
            adapter,
            failed_shot,
            report,
            "screenshot_save_failed",
        )
        try:
            live_section = adapter.find_section(section_title)
            if live_section is not None and not live_section.get("has_edit"):
                adapter.cancel_section(section_title)
                report["cancelled_unsaved_after_failure"] = True
        except Exception as cleanup_exc:
            report["cleanup_error"] = str(cleanup_exc)
        return report

    execution_incomplete = bool(
        report["validation_failed"]
        or report["fill_error"]
        or report["skipped_live_match"]
    )
    if errors or report["persisted_validation_failed"]:
        report["status"] = "persisted_validation_failed"
    elif execution_incomplete:
        report["status"] = "partial_persisted"
    else:
        report["status"] = "persisted_verified"

    after_save = run_dir / f"{_safe_name(section_title)}-after-save-reopen.png"
    _capture_diagnostic_screenshot(
        adapter,
        after_save,
        report,
        "screenshot_after_save",
    )
    try:
        adapter.cancel_section(section_title)
    except Exception as cleanup_exc:
        report["post_save_cleanup_error"] = str(cleanup_exc)
    return report


def _fresh_photo_state(adapter: MakroDomainAdapter) -> tuple[str, dict[str, Any]]:
    section = adapter.find_section(PRODUCT_PHOTOS)
    if section is None:
        raise RuntimeError("当前页面找不到 Product Photos section。")
    if section.get("has_edit"):
        adapter.open_section_for_edit(section)
        section = adapter.find_section(PRODUCT_PHOTOS) or section
    path = str(section.get("path") or "")
    if not path:
        raise RuntimeError("Product Photos section 缺少稳定 DOM path。")
    return path, _photo_state(adapter.page, path)


def _wait_for_file_input(
    adapter: MakroDomainAdapter,
    *,
    timeout_ms: int,
) -> tuple[str, Any, dict[str, Any]] | None:
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        try:
            section_path, state = _fresh_photo_state(adapter)
            target = _select_file_input(adapter.page, section_path)
            if target is not None:
                return section_path, target, state
        except Exception:
            pass
        adapter.page.wait_for_timeout(200)
    return None


def _wait_for_photo_acceptance(
    adapter: MakroDomainAdapter,
    *,
    before_images: int,
    before_sources: set[str],
    before_completion: int | None,
    before_add_tiles: int | None,
    timeout_ms: int,
) -> dict[str, Any]:
    """Poll until Makro visibly consumes the target image slot."""

    deadline = time.monotonic() + timeout_ms / 1000.0
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            _section_path, latest = _fresh_photo_state(adapter)
            if _stage_accepted(
                latest,
                before_images=before_images,
                before_sources=before_sources,
                before_completion=before_completion,
                before_add_tiles=before_add_tiles,
            ):
                return latest
        except Exception:
            pass
        adapter.page.wait_for_timeout(200)
    return latest


def _cancel_open_photo_transaction(adapter: MakroDomainAdapter) -> None:
    live = adapter.find_section(PRODUCT_PHOTOS)
    if live is not None and not live.get("has_edit"):
        adapter.cancel_section(PRODUCT_PHOTOS)


def _photo_upload_budget(
    *,
    requested: int,
    initial_count: int,
    capacity: int | None,
    visible_empty_slots: int,
) -> dict[str, int | None]:
    """Return the only safe upload subset the live gallery can accept.

    The Makro counter is authoritative when present. Visible empty slots are a
    fallback only when the counter does not expose a capacity. Requested images
    beyond the live budget are not upload failures; they are explicit omissions
    caused by an already occupied gallery.
    """

    if requested < 0 or initial_count < 0 or visible_empty_slots < 0:
        raise ValueError("photo counts must be non-negative")
    if capacity is not None:
        if capacity < 0 or initial_count > capacity:
            raise ValueError(
                f"invalid photo capacity state: initial={initial_count}, capacity={capacity}"
            )
        available = max(0, capacity - initial_count)
    else:
        available = visible_empty_slots
    upload_count = min(requested, available)
    return {
        "available_slots": available,
        "upload_count": upload_count,
        "omitted_count": requested - upload_count,
        "capacity": capacity,
    }


def _persisted_gallery_report(
    *,
    requested: int,
    initial_count: int,
    capacity: int | None,
    available_slots: int,
    omitted_paths: list[Path],
    request_status: str,
    detail: str,
) -> dict[str, Any]:
    """Describe a gallery that is already persisted and needs no Save click."""

    return {
        "status": "persisted_verified",
        "request_status": request_status,
        "request_complete": not omitted_paths,
        "capacity_limited": bool(omitted_paths),
        "requested": requested,
        "available_slots": available_slots,
        "attempted": 0,
        "staged": 0,
        "persisted": initial_count,
        "persisted_this_run": 0,
        "initial_count": initial_count,
        "final_count": initial_count,
        "capacity": capacity,
        "omitted_count": len(omitted_paths),
        "omitted_due_capacity": [str(path) for path in omitted_paths],
        "listing_photo_requirement_satisfied": initial_count >= 1,
        "persistence": {
            "status": "persisted_verified",
            "initial_count": initial_count,
            "final_count": initial_count,
            "expected_added": 0,
        },
        "items": [],
        "save_attempted": False,
        "save_count": 0,
        "saved": False,
        "detail": detail,
    }


def run_photos(
    adapter: MakroDomainAdapter,
    image_paths: list[str],
    *,
    allow_save: bool,
    upload_timeout_ms: int,
    run_dir: Path,
) -> dict[str, Any]:
    """Persist the safe subset of explicit listing images into Product Photos.

    The current live gallery owns the capacity decision. If fewer slots remain
    than explicitly requested, only the available left-to-right subset is
    uploaded and every omitted path is reported. Capacity saturation is not an
    execution failure when the listing already has at least one persisted photo;
    upload, Save, or persistence verification failures remain fatal.
    """

    resolved: list[Path] = []
    seen: set[str] = set()
    for raw in image_paths:
        path = Path(raw).expanduser().resolve()
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        if not path.is_file():
            return {
                "status": "invalid_input",
                "request_status": "invalid_input",
                "request_complete": False,
                "capacity_limited": False,
                "requested": len(image_paths),
                "attempted": 0,
                "staged": 0,
                "persisted": 0,
                "persisted_this_run": 0,
                "omitted_count": 0,
                "omitted_due_capacity": [],
                "detail": f"上传图片不存在或不是文件：{path}",
                "save_attempted": False,
                "save_count": 0,
                "saved": False,
            }
        resolved.append(path)

    requested = len(resolved)
    initial_section = adapter.find_section(PRODUCT_PHOTOS)
    initial_was_collapsed = bool(initial_section and initial_section.get("has_edit"))
    try:
        _initial_path, initial_state = _fresh_photo_state(adapter)
    except Exception as exc:
        return {
            "status": "not_found",
            "request_status": "not_found",
            "request_complete": False,
            "capacity_limited": False,
            "requested": requested,
            "attempted": 0,
            "staged": 0,
            "persisted": 0,
            "persisted_this_run": 0,
            "omitted_count": 0,
            "omitted_due_capacity": [],
            "detail": str(exc),
            "save_attempted": False,
            "save_count": 0,
            "saved": False,
        }

    raw_initial = initial_state.get("completion_count")
    initial_count = int(raw_initial) if raw_initial is not None else 0
    raw_capacity = initial_state.get("capacity")
    capacity = int(raw_capacity) if raw_capacity is not None else None
    visible_empty_slots = int(initial_state.get("add_image_tile_count") or 0)

    try:
        budget = _photo_upload_budget(
            requested=requested,
            initial_count=initial_count,
            capacity=capacity,
            visible_empty_slots=visible_empty_slots,
        )
    except ValueError as exc:
        try:
            if initial_was_collapsed:
                _cancel_open_photo_transaction(adapter)
        except Exception:
            pass
        return {
            "status": "capacity_state_invalid",
            "request_status": "capacity_state_invalid",
            "request_complete": False,
            "capacity_limited": False,
            "requested": requested,
            "attempted": 0,
            "staged": 0,
            "persisted": initial_count,
            "persisted_this_run": 0,
            "initial_count": initial_count,
            "final_count": initial_count,
            "capacity": capacity,
            "available_slots": 0,
            "omitted_count": 0,
            "omitted_due_capacity": [],
            "detail": str(exc),
            "save_attempted": False,
            "save_count": 0,
            "saved": False,
        }

    available_slots = int(budget["available_slots"] or 0)
    upload_count = int(budget["upload_count"] or 0)
    pending = resolved[:upload_count]
    omitted = resolved[upload_count:]

    if requested == 0:
        if initial_count >= 1:
            report = _persisted_gallery_report(
                requested=0,
                initial_count=initial_count,
                capacity=capacity,
                available_slots=available_slots,
                omitted_paths=[],
                request_status="not_requested",
                detail="没有传入 --upload-image；现有 Product Photos 已满足至少 1 张持久化图片要求。",
            )
        else:
            report = {
                "status": "skipped",
                "request_status": "not_requested",
                "request_complete": True,
                "capacity_limited": False,
                "requested": 0,
                "available_slots": available_slots,
                "attempted": 0,
                "staged": 0,
                "persisted": 0,
                "persisted_this_run": 0,
                "initial_count": 0,
                "final_count": 0,
                "capacity": capacity,
                "omitted_count": 0,
                "omitted_due_capacity": [],
                "listing_photo_requirement_satisfied": False,
                "persistence": {
                    "status": "missing_required_photo",
                    "initial_count": 0,
                    "final_count": 0,
                    "expected_added": 0,
                },
                "items": [],
                "save_attempted": False,
                "save_count": 0,
                "saved": False,
                "detail": "没有传入 --upload-image，且当前 Product Photos 没有已持久化图片。",
            }
        if initial_was_collapsed:
            try:
                _cancel_open_photo_transaction(adapter)
                report["restored_collapsed_state"] = True
            except Exception as cleanup_exc:
                report["post_skip_cleanup_error"] = str(cleanup_exc)
        return report

    if not pending:
        if initial_count < 1:
            report = {
                "status": "capacity_unavailable",
                "request_status": "skipped_no_capacity",
                "request_complete": False,
                "capacity_limited": True,
                "requested": requested,
                "available_slots": available_slots,
                "attempted": 0,
                "staged": 0,
                "persisted": 0,
                "persisted_this_run": 0,
                "initial_count": initial_count,
                "final_count": initial_count,
                "capacity": capacity,
                "omitted_count": len(omitted),
                "omitted_due_capacity": [str(path) for path in omitted],
                "listing_photo_requirement_satisfied": False,
                "items": [],
                "save_attempted": False,
                "save_count": 0,
                "saved": False,
                "detail": "Product Photos 没有可用图片槽，同时当前 listing 也没有任何已持久化图片。",
            }
        else:
            report = _persisted_gallery_report(
                requested=requested,
                initial_count=initial_count,
                capacity=capacity,
                available_slots=available_slots,
                omitted_paths=omitted,
                request_status="skipped_no_capacity",
                detail=(
                    f"Product Photos 已占用 {initial_count}/{capacity or initial_count}；"
                    f"没有剩余槽位，本次 {len(omitted)} 张明确上传图片全部跳过，不影响已持久化草稿。"
                ),
            )
            report["warning"] = (
                f"图片容量已满：requested={requested}, omitted={len(omitted)}, "
                f"existing={initial_count}, capacity={capacity}."
            )
        if initial_was_collapsed:
            try:
                _cancel_open_photo_transaction(adapter)
                report["restored_collapsed_state"] = True
            except Exception as cleanup_exc:
                report["post_capacity_cleanup_error"] = str(cleanup_exc)
        return report

    report: dict[str, Any] = {
        "status": "running",
        "request_status": "capacity_limited" if omitted else "complete",
        "request_complete": not omitted,
        "capacity_limited": bool(omitted),
        "requested": requested,
        "available_slots": available_slots,
        "initial_count": initial_count,
        "final_count": initial_count,
        "capacity": capacity,
        "attempted": 0,
        "staged": 0,
        "persisted": initial_count,
        "persisted_this_run": 0,
        "omitted_count": len(omitted),
        "omitted_due_capacity": [str(path) for path in omitted],
        "listing_photo_requirement_satisfied": initial_count >= 1,
        "items": [],
        "save_attempted": False,
        "save_count": 0,
        "saved": False,
    }

    for offset, image in enumerate(pending, start=1):
        current = _wait_for_file_input(adapter, timeout_ms=upload_timeout_ms)
        if current is None:
            report["items"].append(
                {
                    "path": str(image),
                    "status": "slot_missing",
                    "slot_position": initial_count + offset,
                    "detail": "找不到下一个带橙色 + 的未完成图片槽。",
                }
            )
            report["status"] = "incomplete_upload"
            break

        _section_path, target, before = current
        before_images = int(before.get("visible_image_count") or 0)
        before_sources = {
            str(value).strip()
            for value in before.get("visible_image_sources") or []
            if str(value).strip()
        }
        raw_completion = before.get("completion_count")
        before_completion = int(raw_completion) if raw_completion is not None else None
        before_add_tiles = int(before.get("add_image_tile_count") or 0)
        report["attempted"] += 1
        item_report: dict[str, Any] = {
            "path": str(image),
            "index": initial_count + offset,
            "slot_position": initial_count + offset,
            "before_completion_count": before_completion,
            "before_empty_slots": before_add_tiles,
        }
        report["items"].append(item_report)

        try:
            target.set_input_files(str(image))
            settled = _wait_for_photo_acceptance(
                adapter,
                before_images=before_images,
                before_sources=before_sources,
                before_completion=before_completion,
                before_add_tiles=before_add_tiles,
                timeout_ms=upload_timeout_ms,
            )
            accepted = bool(settled) and _stage_accepted(
                settled,
                before_images=before_images,
                before_sources=before_sources,
                before_completion=before_completion,
                before_add_tiles=before_add_tiles,
            )
            if not accepted:
                item_report["status"] = "staging_unconfirmed"
                item_report["detail"] = "Makro 没有确认这个图片槽已被填入。"
                report["status"] = "incomplete_upload"
                break
            report["staged"] += 1
            item_report["status"] = "staged"
            item_report["after_empty_slots"] = int(settled.get("add_image_tile_count") or 0)
        except Exception as exc:
            item_report["status"] = "upload_error"
            item_report["detail"] = str(exc)
            report["status"] = "incomplete_upload"
            break

    staged_shot = run_dir / "Product-Photos-staged.png"
    _capture_diagnostic_screenshot(
        adapter,
        staged_shot,
        report,
        "screenshot_staged",
    )

    expected_new = len(pending)
    if int(report["staged"]) != expected_new:
        report["detail"] = (
            f"本次容量计划允许上传 {expected_new} 张，只确认 staged={report['staged']}；"
            "真实上传事务未完成，因此没有 Save。"
        )
        try:
            _cancel_open_photo_transaction(adapter)
            report["cancelled_unsaved_partial"] = True
        except Exception as exc:
            report["cleanup_error"] = str(exc)
        return report

    if not allow_save:
        report["status"] = "staged"
        report["detail"] = f"{expected_new}/{expected_new} 个本次可用图片槽已填写，等待一次 Save。"
        return report

    report["save_attempted"] = True
    try:
        adapter.save_section(PRODUCT_PHOTOS)
        report["save_count"] = 1
        report["saved"] = True
    except Exception as exc:
        report["status"] = "save_failed"
        report["save_error"] = str(exc)
        report["save_failure_diagnostics"] = _collect_save_failure_diagnostics(
            adapter,
            PRODUCT_PHOTOS,
        )
        report["detail"] = (
            f"{expected_new} 个本次可用图片槽已填写，但 Product Photos Save 被 Makro 拒绝。"
        )
        failed_shot = run_dir / "Product-Photos-save-failed.png"
        _capture_diagnostic_screenshot(
            adapter,
            failed_shot,
            report,
            "screenshot_save_failed",
        )
        return report

    persistence = adapter.verify_persisted_photo_count(
        initial_count=initial_count,
        expected_added=expected_new,
    )
    report["persistence"] = persistence
    report["final_count"] = persistence.get("final_count")
    if persistence.get("status") != "persisted_verified":
        report["status"] = "persistence_failed"
        report["saved"] = False
        report["persisted"] = initial_count
        report["persisted_this_run"] = 0
        report["detail"] = "Product Photos Save 已点击，但完成计数没有达到本次容量计划的预期。"
        return report

    final_count = int(persistence.get("final_count") or (initial_count + expected_new))
    report["persisted"] = final_count
    report["persisted_this_run"] = expected_new
    report["listing_photo_requirement_satisfied"] = final_count >= 1
    report["status"] = "persisted_verified"
    report["saved"] = True
    if omitted:
        report["warning"] = (
            f"图片容量限制：requested={requested}, uploaded={expected_new}, "
            f"omitted={len(omitted)}, final={final_count}/{capacity or final_count}."
        )
        report["detail"] = (
            f"Product Photos 已保存本次可容纳的 {expected_new} 张；"
            f"另有 {len(omitted)} 张因 live gallery 容量不足未尝试上传。"
        )
    else:
        report["detail"] = (
            f"Product Photos 已按固定槽位完整保存：{final_count}/{capacity or final_count}。"
        )

    section = adapter.find_section(PRODUCT_PHOTOS)
    if section is not None:
        adapter.open_section_for_edit(section)
        reopened = adapter.inspect_product_photos()
        report["reopened_state"] = {
            "completion_count": reopened.get("completion_count"),
            "capacity": reopened.get("capacity"),
            "visible_image_count": reopened.get("visible_image_count"),
            "remaining_empty_slots": reopened.get("add_image_tile_count"),
        }
        saved_shot = run_dir / "Product-Photos-after-save-reopen.png"
        _capture_diagnostic_screenshot(
            adapter,
            saved_shot,
            report,
            "screenshot_after_save",
        )
        try:
            adapter.cancel_section(PRODUCT_PHOTOS)
        except Exception as cleanup_exc:
            report["post_save_cleanup_error"] = str(cleanup_exc)
    return report
