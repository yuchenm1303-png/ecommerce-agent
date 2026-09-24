from __future__ import annotations

from pathlib import Path
from typing import Any

from app.fill_plan import LiveFillPlan
from app.hard_field_validators import validate_resolved_answer
from app.makro.domain import MakroDomainAdapter
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
            "error", "required", "invalid", "please", "must", "cannot", "can't",
            "minimum", "maximum", "greater", "less than", "upload",
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


def _cancel_open_section_transaction(
    adapter: MakroDomainAdapter,
    section_title: str,
) -> bool:
    live = adapter.find_section(section_title)
    if live is None or live.get("has_edit"):
        return False
    adapter.cancel_section(section_title)
    return True


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
    """Execute one Step 3 card with field-local failure ownership.

    A field that cannot bind, pass mechanical preflight, fill, or validate fails
    only that field. Later fields continue. Every field that validates remains a
    candidate for Save and persistence verification. Section collapse after Save
    is the persistence boundary; a residual Makro validation badge describes
    completeness and does not erase values that Makro already persisted.

    Only loss of the section execution surface itself stops iteration because at
    that point there is no trustworthy live transaction in which to continue.
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
        "optional_prewrite_skipped": 0,
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
        _open_and_index_section(
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
    executed_candidates: list[Any] = []
    structural_failure = False

    for item in candidates:
        mode = preview_mode_for_item(
            item,
            include_review_candidates=include_review_candidates,
        )
        base_payload = _base_result_payload(item, mode)
        identity = _item_identity(item)
        required = bool(getattr(item, "required", False))

        live_section = adapter.find_section(section_title)
        if live_section is None or live_section.get("has_edit"):
            report["validation_failed"] += 1
            report["stopped_on_field"] = item.label
            report["results"].append(
                {
                    **base_payload,
                    "execution_status": "section_transaction_lost",
                    "detail": "字段写入前目标 section 已不存在或意外折叠；执行面已丢失，无法安全继续后续字段。",
                }
            )
            structural_failure = True
            break

        try:
            section_path, live = _open_and_index_section(
                adapter,
                section_title,
                wait_ms=scroll_wait_ms,
                max_scroll_steps=max_scroll_steps,
            )
        except Exception as exc:
            report["validation_failed"] += 1
            report["stopped_on_field"] = item.label
            report["results"].append(
                {
                    **base_payload,
                    "execution_status": "live_refresh_failed",
                    "detail": f"字段写入前刷新当前 React live schema 失败，执行面不可用：{exc}",
                }
            )
            structural_failure = True
            break

        matches = live.get(identity, [])
        if len(matches) != 1:
            report["skipped_live_match"] += 1
            if not required:
                report["optional_prewrite_skipped"] += 1
                report["results"].append(
                    {
                        **base_payload,
                        "execution_status": "optional_skipped_live_match",
                        "detail": (
                            f"可选字段写入前重新扫描后 live field 匹配数={len(matches)}，期望恰好 1；"
                            "该字段尚未发生任何写入，已局部跳过。"
                        ),
                    }
                )
                continue
            report["validation_failed"] += 1
            report["results"].append(
                {
                    **base_payload,
                    "execution_status": "required_live_match_failed",
                    "detail": (
                        f"必填字段写入前重新扫描后 live field 匹配数={len(matches)}，期望恰好 1；"
                        "仅该字段失败，继续处理同 section 的其他字段。"
                    ),
                }
            )
            continue

        answer = execution_answer_for_item(
            item,
            include_review_candidates=include_review_candidates,
        )
        live_field = matches[0]
        hard_validation = validate_resolved_answer(live_field, answer)
        if not hard_validation.valid:
            if not required:
                report["optional_prewrite_skipped"] += 1
                report["results"].append(
                    {
                        **base_payload,
                        "execution_status": "optional_preflight_rejected",
                        "detail": (
                            "可选字段与当前 live DOM 机械契约不兼容；尚未写入，已局部跳过："
                            f"{hard_validation.detail}"
                        ),
                    }
                )
                continue
            report["validation_failed"] += 1
            report["results"].append(
                {
                    **base_payload,
                    "execution_status": "preflight_rejected",
                    "detail": (
                        "当前必填 live field 与 Fill Plan 答案的机械契约不兼容；"
                        f"仅该字段未写入，继续处理其他字段：{hard_validation.detail}"
                    ),
                }
            )
            continue

        report["writes_attempted"] += 1
        if mode == "review":
            report["review_candidates_attempted"] += 1
        try:
            verification = adapter.fill_resolved_field(
                live_field,
                answer,
                section_path=section_path,
                recheck_wait_ms=recheck_wait_ms,
            )
        except Exception as exc:
            report["fill_error"] += 1
            report["results"].append(
                {
                    **base_payload,
                    "execution_status": "fill_exception",
                    "detail": f"该字段填写异常；保留当前页面状态并继续其他字段：{exc}",
                }
            )
            continue

        if verification.status == "validated":
            report["validated"] += 1
            validated_identities.add(identity)
            executed_candidates.append(item)
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

    report["field_failures"] = int(report["validation_failed"]) + int(report["fill_error"])

    if structural_failure:
        failed_shot = run_dir / f"{_safe_name(section_title)}-execution-surface-lost.png"
        _capture_diagnostic_screenshot(
            adapter,
            failed_shot,
            report,
            "screenshot_execution_surface_lost",
        )
        report["status"] = "section_execution_lost"
        report["detail"] = (
            "section 本身的 live 执行面已丢失，无法继续或可靠 Save；"
            "执行器没有主动 Cancel，也没有回滚此前字段。"
        )
        return report

    if not executed_candidates:
        if report["writes_attempted"] == 0:
            try:
                if _cancel_open_section_transaction(adapter, section_title):
                    report["cancelled_no_write_transaction"] = True
            except Exception as cleanup_exc:
                report["cleanup_error"] = str(cleanup_exc)
        report["status"] = "no_validated_writes"
        report["detail"] = (
            "本 section 没有任何通过回读验证的写入，因此没有可安全持久化的字段；"
            "若曾发生写入尝试，当前页面状态保持原样，不做整栏回滚。"
        )
        return report

    before_save = run_dir / f"{_safe_name(section_title)}-before-save.png"
    _capture_diagnostic_screenshot(
        adapter,
        before_save,
        report,
        "screenshot_before_save",
    )

    if not persist:
        report["status"] = "preview_partial" if report["field_failures"] else "preview_open"
        return report

    report["save_attempted"] = True
    try:
        adapter.save_section(section_title)
        report["saved"] = True
        persisted, errors = _verify_saved_values(
            adapter,
            executed_candidates,
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
        report["visible_errors_after_save_failure"] = diagnostics.get("visible_errors", [])
        report["detail"] = (
            "Makro section 在 Save 后没有恢复 EDIT，无法证明持久化；"
            "只保留现场诊断，不把该状态误报成已保存。"
        )
        failed_shot = run_dir / f"{_safe_name(section_title)}-save-failed.png"
        _capture_diagnostic_screenshot(
            adapter,
            failed_shot,
            report,
            "screenshot_save_failed",
        )
        return report

    if errors or report["persisted_validation_failed"]:
        report["status"] = "persisted_validation_failed"
        report["detail"] = (
            f"Makro 已持久化并复核 {report['persisted_verified']} 个成功字段；"
            "残余 validation error 只表示该 section 尚未完整，不再阻断后续 section。"
        )
    elif report["field_failures"]:
        report["status"] = "persisted_partial"
        report["detail"] = (
            f"已持久化并复核 {report['persisted_verified']} 个成功字段；"
            f"另有 {report['field_failures']} 个字段独立失败，没有触发整栏回滚。"
        )
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


def _fresh_photo_state(adapter: MakroDomainAdapter) -> dict[str, Any]:
    section = adapter.find_section(PRODUCT_PHOTOS)
    if section is None:
        raise RuntimeError("当前页面找不到 Product Photos section。")
    if section.get("has_edit"):
        adapter.open_section_for_edit(section)
    return adapter.inspect_product_photos()


def _cancel_open_photo_transaction(adapter: MakroDomainAdapter) -> bool:
    live = adapter.find_section(PRODUCT_PHOTOS)
    if live is None or live.get("has_edit"):
        return False
    adapter.cancel_section(PRODUCT_PHOTOS)
    return True


def _photo_upload_budget(
    *, requested: int, initial_count: int, capacity: int | None, visible_empty_slots: int,
) -> dict[str, int | None]:
    if requested < 0 or initial_count < 0 or visible_empty_slots < 0:
        raise ValueError("photo counts must be non-negative")
    if capacity is not None:
        if capacity < 0 or initial_count > capacity:
            raise ValueError(f"invalid photo capacity state: initial={initial_count}, capacity={capacity}")
        available = max(0, capacity - initial_count)
    else:
        available = visible_empty_slots
    return {
        "available_slots": available,
        "upload_count": min(requested, available),
        "omitted_count": max(0, requested - available),
        "capacity": capacity,
    }


def _photo_base_report(
    *,
    requested_unique: int,
    valid_requested: int,
    initial_count: int,
    capacity: int | None,
    available_slots: int,
    rejected_input: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "status": "running",
        "request_status": "complete",
        "request_complete": not rejected_input,
        "capacity_limited": False,
        "requested": requested_unique,
        "valid_requested": valid_requested,
        "rejected_pre_submit": list(rejected_input),
        "available_slots": available_slots,
        "initial_count": initial_count,
        "final_count": initial_count,
        "capacity": capacity,
        "attempted": 0,
        "staged": 0,
        "persisted": initial_count,
        "persisted_this_run": 0,
        "omitted_count": 0,
        "omitted_due_capacity": [],
        "listing_photo_requirement_satisfied": initial_count >= 1,
        "items": list(rejected_input),
        "save_attempted": False,
        "save_count": 0,
        "saved": False,
        "persistence": {
            "status": "persisted_verified" if initial_count >= 0 else "unknown",
            "initial_count": initial_count,
            "final_count": initial_count,
            "expected_added": 0,
        },
    }


def _cancel_failed_photo_transaction(
    adapter: MakroDomainAdapter,
    report: dict[str, Any],
    *,
    image: Path,
) -> bool:
    """Discard only the current image's unsaved slot transaction.

    Production persists every accepted image before the next image starts, so a
    Cancel here cannot erase a previously successful sibling image.
    """

    try:
        cancelled = _cancel_open_photo_transaction(adapter)
        if cancelled:
            report.setdefault("cancelled_image_transactions", []).append(str(image))
        return True
    except Exception as exc:
        report.setdefault("photo_transaction_cleanup_errors", []).append(
            {"path": str(image), "error": str(exc)}
        )
        return False


def run_photos(
    adapter: MakroDomainAdapter,
    image_paths: list[str],
    *,
    allow_save: bool,
    upload_timeout_ms: int,
    run_dir: Path,
) -> dict[str, Any]:
    """Execute Product Photos with one persistence transaction per image.

    The old gallery-wide transaction coupled unrelated images: if image N became
    post-submit uncertain, Cancel discarded images 0..N-1 even when their exact
    slots had already been accepted. Production now uses a strict image-owned
    transaction boundary: stage one image, Save it, verify the completion counter,
    then move to the next image. A failed image may Cancel only its own unsaved
    transaction, so later failures can never erase earlier persisted successes.
    """

    resolved: list[Path] = []
    rejected_input: list[dict[str, Any]] = []
    seen: set[str] = set()
    requested_unique = 0
    for raw in image_paths:
        path = Path(raw).expanduser().resolve()
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        requested_unique += 1
        if not path.is_file():
            rejected_input.append(
                {
                    "path": str(path),
                    "status": "rejected_pre_submit",
                    "detail": f"上传图片不存在或不是文件：{path}",
                    "failure_scope": "image",
                }
            )
            continue
        resolved.append(path)

    initial_section = adapter.find_section(PRODUCT_PHOTOS)
    initial_was_collapsed = bool(initial_section and initial_section.get("has_edit"))
    try:
        initial_state = _fresh_photo_state(adapter)
    except Exception as exc:
        return {
            "status": "not_found",
            "request_status": "not_found",
            "request_complete": False,
            "capacity_limited": False,
            "requested": requested_unique,
            "valid_requested": len(resolved),
            "rejected_pre_submit": rejected_input,
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
            requested=len(resolved),
            initial_count=initial_count,
            capacity=capacity,
            visible_empty_slots=visible_empty_slots,
        )
    except ValueError as exc:
        if initial_was_collapsed:
            try:
                _cancel_open_photo_transaction(adapter)
            except Exception:
                pass
        return {
            "status": "capacity_state_invalid",
            "request_status": "capacity_state_invalid",
            "request_complete": False,
            "capacity_limited": False,
            "requested": requested_unique,
            "valid_requested": len(resolved),
            "rejected_pre_submit": rejected_input,
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
    report = _photo_base_report(
        requested_unique=requested_unique,
        valid_requested=len(resolved),
        initial_count=initial_count,
        capacity=capacity,
        available_slots=available_slots,
        rejected_input=rejected_input,
    )

    if initial_was_collapsed:
        try:
            _cancel_open_photo_transaction(adapter)
            report["restored_collapsed_state_after_inspection"] = True
        except Exception as exc:
            report["inspection_cleanup_error"] = str(exc)
            report["status"] = "photo_surface_cleanup_failed"
            report["request_status"] = "photo_surface_cleanup_failed"
            report["request_complete"] = False
            return report

    if not resolved:
        report["request_status"] = "degraded_input" if rejected_input else "not_requested"
        report["request_complete"] = not rejected_input
        report["status"] = "persisted_verified" if initial_count >= 1 else "skipped"
        report["detail"] = (
            "没有新的可提交图片；现有持久化图片已满足 listing 要求。"
            if initial_count >= 1
            else "没有新的可提交图片，且当前 listing 没有已持久化图片。"
        )
        return report

    if available_slots <= 0:
        report["capacity_limited"] = True
        report["omitted_count"] = len(resolved)
        report["omitted_due_capacity"] = [str(path) for path in resolved]
        report["request_complete"] = False
        report["request_status"] = "skipped_no_capacity"
        report["status"] = "persisted_verified" if initial_count >= 1 else "capacity_unavailable"
        report["detail"] = (
            "Product Photos 已满；现有持久化图片保持不变。"
            if initial_count >= 1
            else "Product Photos 没有可用槽位，且当前 listing 没有任何已持久化图片。"
        )
        return report

    if not allow_save:
        preview_paths = resolved[:available_slots]
        omitted = resolved[available_slots:]
        staged_result = adapter.upload_product_photos(
            [str(path) for path in preview_paths],
            timeout_ms=upload_timeout_ms,
        )
        payload = staged_result.as_dict()
        report["attempted"] = int(payload.get("attempted") or 0)
        report["staged"] = int(payload.get("staged") or 0)
        report["items"].extend(list(payload.get("items") or []))
        report["omitted_count"] = len(omitted)
        report["omitted_due_capacity"] = [str(path) for path in omitted]
        report["capacity_limited"] = bool(omitted)
        report["request_complete"] = (
            not rejected_input
            and not omitted
            and str(payload.get("status") or "") == "staged"
            and report["staged"] == len(preview_paths)
        )
        report["request_status"] = "complete" if report["request_complete"] else "preview_partial"
        report["status"] = "staged" if report["staged"] else str(payload.get("status") or "preview_failed")
        report["detail"] = str(payload.get("detail") or "")
        return report

    current_count = initial_count
    failed_transactions = 0
    omitted: list[Path] = []
    operational_stop = False

    for index, image in enumerate(resolved):
        if report["persisted_this_run"] >= available_slots:
            omitted.extend(resolved[index:])
            break

        try:
            staged_result = adapter.upload_product_photos(
                [str(image)],
                timeout_ms=upload_timeout_ms,
            )
            payload = staged_result.as_dict()
        except Exception as exc:
            failed_transactions += 1
            report["attempted"] += 1
            report["items"].append(
                {
                    "path": str(image),
                    "status": "transaction_error",
                    "detail": str(exc),
                    "failure_scope": "image_transaction",
                }
            )
            if not _cancel_failed_photo_transaction(adapter, report, image=image):
                operational_stop = True
                break
            continue

        report["attempted"] += int(payload.get("attempted") or 0)
        stage_items = [dict(item) for item in list(payload.get("items") or [])]
        for item in stage_items:
            if item.get("status") == "rejected_pre_submit":
                report["rejected_pre_submit"].append(dict(item))

        staged_count = int(payload.get("staged") or 0)
        stage_status = str(payload.get("status") or "")
        if stage_status != "staged" or staged_count != 1:
            failed_transactions += 1
            report["items"].extend(stage_items)
            if not stage_items:
                report["items"].append(
                    {
                        "path": str(image),
                        "status": stage_status or "staging_unconfirmed",
                        "detail": str(payload.get("detail") or ""),
                        "failure_scope": "image_transaction",
                    }
                )
            if not _cancel_failed_photo_transaction(adapter, report, image=image):
                operational_stop = True
                break
            continue

        report["staged"] += 1
        report["save_attempted"] = True
        try:
            adapter.save_section(PRODUCT_PHOTOS)
            report["save_count"] += 1
        except Exception as exc:
            failed_transactions += 1
            for item in stage_items:
                item["status"] = "save_failed"
                item["save_error"] = str(exc)
            report["items"].extend(stage_items)
            report.setdefault("save_failures", []).append(
                {
                    "path": str(image),
                    "error": str(exc),
                    "diagnostics": _collect_save_failure_diagnostics(adapter, PRODUCT_PHOTOS),
                }
            )
            if not _cancel_failed_photo_transaction(adapter, report, image=image):
                operational_stop = True
                break
            continue

        persistence = adapter.verify_persisted_photo_count(
            initial_count=current_count,
            expected_added=1,
        )
        if persistence.get("status") != "persisted_verified":
            failed_transactions += 1
            for item in stage_items:
                item["status"] = "persistence_failed"
                item["persistence"] = dict(persistence)
            report["items"].extend(stage_items)
            report.setdefault("persistence_failures", []).append(
                {"path": str(image), **dict(persistence)}
            )
            raw_final = persistence.get("final_count")
            if raw_final is not None and int(raw_final) > current_count:
                current_count = int(raw_final)
            if not _cancel_failed_photo_transaction(adapter, report, image=image):
                operational_stop = True
            else:
                operational_stop = True
            break

        current_count = int(persistence.get("final_count") or (current_count + 1))
        report["persisted_this_run"] += 1
        report["persisted"] = current_count
        report["final_count"] = current_count
        report["saved"] = True
        for item in stage_items:
            item["status"] = "persisted_verified"
            item["persistence"] = dict(persistence)
        report["items"].extend(stage_items)

    if omitted:
        report["capacity_limited"] = True
        report["omitted_count"] = len(omitted)
        report["omitted_due_capacity"] = [str(path) for path in omitted]

    report["persisted"] = current_count
    report["final_count"] = current_count
    report["listing_photo_requirement_satisfied"] = current_count >= 1
    report["persistence"] = {
        "status": "persisted_verified" if current_count >= initial_count else "validation_failed",
        "initial_count": initial_count,
        "final_count": current_count,
        "expected_added": report["persisted_this_run"],
    }
    report["request_complete"] = bool(
        not rejected_input
        and not report["rejected_pre_submit"]
        and not omitted
        and failed_transactions == 0
        and not operational_stop
        and report["persisted_this_run"] == len(resolved)
    )

    if report["request_complete"]:
        report["request_status"] = "complete"
        report["status"] = "persisted_verified"
        report["detail"] = (
            f"Product Photos 已逐张独立持久化并复核：{current_count}/{capacity or current_count}。"
        )
    elif current_count >= 1:
        report["request_status"] = "partial_persisted"
        report["status"] = "persisted_verified"
        report["warning"] = (
            f"图片请求局部完成：requested={requested_unique}, persisted_this_run={report['persisted_this_run']}, "
            f"failed_transactions={failed_transactions}, omitted_capacity={len(omitted)}, final={current_count}."
        )
        report["detail"] = (
            "至少一张图片已可靠持久化；失败图片只回滚自己的未保存事务，"
            "不会再撤销先前成功图片。"
        )
    else:
        report["request_status"] = "incomplete_upload"
        report["status"] = "incomplete_upload"
        report["detail"] = "没有任何图片形成可证明的持久化结果。"

    if operational_stop:
        report["operational_stop"] = True

    final_shot = run_dir / "Product-Photos-final.png"
    _capture_diagnostic_screenshot(adapter, final_shot, report, "screenshot_final")
    return report