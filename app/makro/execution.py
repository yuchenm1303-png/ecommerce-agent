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
        # Kept in the report schema for old GUI/report readers. Production never
        # increments this field: READY is not gated by existing DOM values.
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
    adapter.page.screenshot(path=str(before_save), full_page=True)
    report["screenshot_before_save"] = str(before_save.resolve())

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

        after_save = run_dir / f"{_safe_name(section_title)}-after-save-reopen.png"
        adapter.page.screenshot(path=str(after_save), full_page=True)
        report["screenshot_after_save"] = str(after_save.resolve())
        adapter.cancel_section(section_title)

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
    except Exception as exc:
        report["status"] = "save_failed"
        report["save_error"] = str(exc)
        live_section = adapter.find_section(section_title)
        if live_section is not None and not live_section.get("has_edit"):
            path = str(live_section.get("path") or "")
            if path:
                report["visible_errors_after_save_failure"] = adapter.visible_section_errors(path)
            failed_shot = run_dir / f"{_safe_name(section_title)}-save-failed.png"
            adapter.page.screenshot(path=str(failed_shot), full_page=True)
            report["screenshot_save_failed"] = str(failed_shot.resolve())
            try:
                adapter.cancel_section(section_title)
                report["cancelled_unsaved_after_failure"] = True
            except Exception as cleanup_exc:
                report["cleanup_error"] = str(cleanup_exc)
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
            file_input = _select_file_input(adapter.page, section_path)
            if file_input is not None:
                return section_path, file_input, state
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
    timeout_ms: int,
) -> dict[str, Any]:
    """Poll by rediscovering the Product Photos card after every React render."""

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


def run_photos(
    adapter: MakroDomainAdapter,
    image_paths: list[str],
    *,
    allow_save: bool,
    upload_timeout_ms: int,
    run_dir: Path,
) -> dict[str, Any]:
    """Upload Product Photos using Makro's real one-file edit lifecycle.

    The live Cases & Covers page removes its current ``input[type=file]`` after
    one accepted image. Formal persisted execution therefore treats each image
    as its own transaction: rediscover card/input -> upload one image -> Save ->
    verify the counter grew -> reopen for the next image. A requested 5-image
    set is complete only after five independently persisted uploads.
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
                "requested": len(image_paths),
                "attempted": 0,
                "staged": 0,
                "persisted": 0,
                "detail": f"上传图片不存在或不是文件：{path}",
                "save_attempted": False,
                "save_count": 0,
                "saved": False,
            }
        resolved.append(path)

    requested = len(resolved)
    if requested == 0:
        return {
            "status": "skipped",
            "requested": 0,
            "attempted": 0,
            "staged": 0,
            "persisted": 0,
            "detail": "没有传入 --upload-image；没有执行 Product Photos。",
            "save_attempted": False,
            "save_count": 0,
            "saved": False,
        }

    first = _wait_for_file_input(adapter, timeout_ms=upload_timeout_ms)
    if first is None:
        return {
            "status": "file_input_missing",
            "requested": requested,
            "attempted": 0,
            "staged": 0,
            "persisted": 0,
            "detail": "Product Photos 已展开，但找不到可用 input[type=file]。",
            "save_attempted": False,
            "save_count": 0,
            "saved": False,
        }

    _first_path, _first_input, initial_state = first
    initial_count = initial_state.get("completion_count")
    capacity = initial_state.get("capacity")
    if capacity is not None and initial_count is not None:
        available = max(0, int(capacity) - int(initial_count))
        if requested > available:
            try:
                _cancel_open_photo_transaction(adapter)
            except Exception:
                pass
            return {
                "status": "capacity_exceeded",
                "requested": requested,
                "attempted": 0,
                "staged": 0,
                "persisted": 0,
                "initial_count": initial_count,
                "capacity": capacity,
                "detail": f"请求上传 {requested} 张，但 Product Photos 只剩 {available} 个空位。",
                "save_attempted": False,
                "save_count": 0,
                "saved": False,
            }

    report: dict[str, Any] = {
        "status": "running",
        "requested": requested,
        "initial_count": initial_count,
        "final_count": initial_count,
        "capacity": capacity,
        "attempted": 0,
        "staged": 0,
        "persisted": 0,
        "items": [],
        "save_attempted": False,
        "save_count": 0,
        "saved": False,
    }

    for index, image in enumerate(resolved, start=1):
        current = _wait_for_file_input(adapter, timeout_ms=upload_timeout_ms)
        if current is None:
            report["items"].append(
                {
                    "path": str(image),
                    "status": "file_input_missing",
                    "detail": "重新打开 Product Photos 后仍找不到本张所需 file input。",
                }
            )
            report["status"] = "incomplete_upload"
            break

        _section_path, file_input, before = current
        before_images = int(before.get("visible_image_count") or 0)
        before_sources = {
            str(value).strip()
            for value in before.get("visible_image_sources") or []
            if str(value).strip()
        }
        raw_completion = before.get("completion_count")
        before_completion = int(raw_completion) if raw_completion is not None else None
        report["attempted"] += 1
        item_report: dict[str, Any] = {
            "path": str(image),
            "index": index,
            "before_completion_count": before_completion,
        }
        report["items"].append(item_report)

        try:
            file_input.set_input_files(str(image))
            settled = _wait_for_photo_acceptance(
                adapter,
                before_images=before_images,
                before_sources=before_sources,
                before_completion=before_completion,
                timeout_ms=upload_timeout_ms,
            )
            accepted = bool(settled) and _stage_accepted(
                settled,
                before_images=before_images,
                before_sources=before_sources,
                before_completion=before_completion,
            )
            if not accepted:
                item_report["status"] = "staging_unconfirmed"
                item_report["detail"] = "Makro 在超时前没有出现本张图片的新预览/计数信号。"
                report["status"] = "incomplete_upload"
                break
            report["staged"] += 1
            item_report["status"] = "staged"
        except Exception as exc:
            item_report["status"] = "upload_error"
            item_report["detail"] = str(exc)
            report["status"] = "incomplete_upload"
            break

        if not allow_save:
            continue

        report["save_attempted"] = True
        try:
            adapter.save_section(PRODUCT_PHOTOS)
            report["save_count"] += 1
        except Exception as exc:
            item_report["status"] = "save_failed"
            item_report["save_error"] = str(exc)
            report["status"] = "save_failed"
            break

        persistence = adapter.verify_persisted_photo_count(
            initial_count=before_completion,
            expected_added=1,
        )
        item_report["persistence"] = persistence
        report["final_count"] = persistence.get("final_count")
        if persistence.get("status") != "persisted_verified":
            item_report["status"] = "persistence_failed"
            report["status"] = "partial_persisted"
            break

        report["persisted"] += 1
        item_report["status"] = "persisted_verified"
        item_report["after_completion_count"] = persistence.get("final_count")

    staged_shot = run_dir / "Product-Photos-staged.png"
    adapter.page.screenshot(path=str(staged_shot), full_page=True)
    report["screenshot_staged"] = str(staged_shot.resolve())

    if not allow_save:
        if int(report["staged"]) == requested:
            report["status"] = "staged"
            report["detail"] = f"{requested}/{requested} 张图片均已进入未保存 Product Photos 编辑事务。"
        else:
            report["status"] = "incomplete_upload"
            report["detail"] = f"计划 {requested} 张，只确认 staged={report['staged']}；未保存。"
            try:
                _cancel_open_photo_transaction(adapter)
                report["cancelled_unsaved_partial"] = True
            except Exception as exc:
                report["cleanup_error"] = str(exc)
        return report

    if int(report["persisted"]) != requested:
        if report.get("status") == "running":
            report["status"] = "partial_persisted"
        report["saved"] = False
        report["detail"] = (
            f"计划 {requested} 张，已 attempted={report['attempted']}、staged={report['staged']}、"
            f"persisted={report['persisted']}；未达到完整 {requested}/{requested}。"
        )
        try:
            _cancel_open_photo_transaction(adapter)
        except Exception as exc:
            report["cleanup_error"] = str(exc)
        report["persistence"] = {
            "status": "partial_persisted",
            "initial_count": initial_count,
            "final_count": report.get("final_count"),
            "expected_added": requested,
            "persisted_added": report["persisted"],
            "detail": report["detail"],
        }
        return report

    report["saved"] = True
    report["status"] = "persisted_verified"
    report["detail"] = f"{requested}/{requested} 张图片均逐张 Save 并验证持久化。"
    report["persistence"] = {
        "status": "persisted_verified",
        "initial_count": initial_count,
        "final_count": report.get("final_count"),
        "expected_added": requested,
        "persisted_added": report["persisted"],
        "detail": report["detail"],
    }

    section = adapter.find_section(PRODUCT_PHOTOS)
    if section is not None:
        adapter.open_section_for_edit(section)
        reopened = adapter.inspect_product_photos()
        report["reopened_state"] = {
            "completion_count": reopened.get("completion_count"),
            "capacity": reopened.get("capacity"),
            "visible_image_count": reopened.get("visible_image_count"),
        }
        saved_shot = run_dir / "Product-Photos-after-save-reopen.png"
        adapter.page.screenshot(path=str(saved_shot), full_page=True)
        report["screenshot_after_save"] = str(saved_shot.resolve())
        adapter.cancel_section(PRODUCT_PHOTOS)
    return report
