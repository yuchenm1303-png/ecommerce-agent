from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from playwright.sync_api import Page

from .sections import find_section, open_section_for_edit

PRODUCT_PHOTOS_SECTION = "Product Photos"


@dataclass(slots=True)
class PhotoUploadResult:
    """State of listing images accepted into the open Product Photos editor.

    ``staged`` means Makro has produced an observable card-level acceptance
    signal (new preview/source or counter growth). Merely placing a file into
    ``input.files`` is not enough and is recorded only as diagnostics.
    """

    status: str
    initial_count: int | None = None
    final_count: int | None = None
    capacity: int | None = None
    attempted: int = 0
    staged: int = 0
    accept: str = ""
    multiple: bool = False
    items: list[dict[str, Any]] = field(default_factory=list)
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "initial_count": self.initial_count,
            "final_count": self.final_count,
            "capacity": self.capacity,
            "attempted": self.attempted,
            "staged": self.staged,
            "accept": self.accept,
            "multiple": self.multiple,
            "items": self.items,
            "detail": self.detail,
        }


def parse_completion_counter(title: str) -> tuple[int, int] | None:
    match = re.search(r"\(\s*(\d+)\s*/\s*(\d+)\s*\)\s*$", str(title or ""))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _photo_state(page: Page, section_path: str) -> dict[str, Any]:
    card = page.locator(section_path)
    if card.count() != 1:
        return {"found": False, "detail": f"section path 匹配 {card.count()} 个节点"}

    payload = card.evaluate(
        r"""card => {
          const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
          const titleEl = card.querySelector(
            '[class*="styles__Title-"], [class*="Title-ef7o31"], [class*="Title-"]'
          );
          const inputs = Array.from(card.querySelectorAll('input[type="file"]'));
          const images = Array.from(card.querySelectorAll('img')).filter(img => {
            const src = clean(img.getAttribute('src'));
            const rect = img.getBoundingClientRect();
            return Boolean(src) && rect.width > 0 && rect.height > 0;
          });
          return {
            found: true,
            title: titleEl ? clean(titleEl.innerText || titleEl.textContent) : '',
            file_input_count: inputs.length,
            file_inputs: inputs.map(input => ({
              accept: clean(input.getAttribute('accept')),
              multiple: input.multiple === true || input.hasAttribute('multiple'),
              disabled: input.disabled === true || input.hasAttribute('disabled'),
              files: input.files ? input.files.length : 0,
            })),
            visible_image_count: images.length,
            visible_image_sources: images.map(img => clean(img.getAttribute('src'))).filter(Boolean),
          };
        }"""
    )
    counter = parse_completion_counter(str(payload.get("title") or ""))
    payload["completion_count"] = counter[0] if counter else None
    payload["capacity"] = counter[1] if counter else None
    return payload


def inspect_product_photos(page: Page) -> dict[str, Any]:
    section = find_section(page, PRODUCT_PHOTOS_SECTION)
    if section is None:
        return {"found": False, "detail": "当前页面找不到 Product Photos section。"}
    path = str(section.get("path") or "")
    if not path:
        return {"found": False, "detail": "Product Photos section 缺少稳定 DOM path。"}
    state = _photo_state(page, path)
    state["section_title"] = section.get("title")
    state["section_path"] = path
    state["expanded"] = not bool(section.get("has_edit"))
    return state


def _select_file_input(page: Page, section_path: str):
    inputs = page.locator(section_path).locator('input[type="file"]')
    for index in range(inputs.count()):
        candidate = inputs.nth(index)
        if not candidate.is_disabled():
            return candidate
    return None


def _stage_accepted(
    state: dict[str, Any],
    *,
    before_images: int,
    before_sources: set[str],
    before_completion: int | None,
) -> bool:
    """Return True only for a Makro-visible acceptance signal.

    ``input.files`` is deliberately ignored: browsers populate it immediately
    after ``set_input_files`` even when the application has not processed or
    accepted the image yet.
    """

    images = int(state.get("visible_image_count") or 0)
    sources = {
        str(value).strip()
        for value in state.get("visible_image_sources") or []
        if str(value).strip()
    }
    raw_completion = state.get("completion_count")
    completion = int(raw_completion) if raw_completion is not None else None
    return bool(
        images > before_images
        or sources.difference(before_sources)
        or (
            before_completion is not None
            and completion is not None
            and completion > before_completion
        )
    )


def _wait_for_staged_signal(
    page: Page,
    section_path: str,
    *,
    before_images: int,
    before_sources: set[str],
    before_completion: int | None,
    timeout_ms: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_ms / 1000.0
    latest = _photo_state(page, section_path)
    while time.monotonic() < deadline:
        latest = _photo_state(page, section_path)
        if _stage_accepted(
            latest,
            before_images=before_images,
            before_sources=before_sources,
            before_completion=before_completion,
        ):
            return latest
        page.wait_for_timeout(200)
    return latest


def upload_product_photos(
    page: Page,
    image_paths: Iterable[str | Path],
    *,
    timeout_ms: int = 8_000,
) -> PhotoUploadResult:
    """Stage explicit listing images into Product Photos; never Save the card."""

    resolved_paths: list[Path] = []
    seen: set[str] = set()
    for raw in image_paths:
        path = Path(raw).expanduser().resolve()
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        if not path.is_file():
            return PhotoUploadResult(
                status="invalid_input",
                detail=f"上传图片不存在或不是文件：{path}",
            )
        resolved_paths.append(path)

    if not resolved_paths:
        return PhotoUploadResult(status="skipped", detail="没有传入 --upload-image。")

    section = find_section(page, PRODUCT_PHOTOS_SECTION)
    if section is None:
        return PhotoUploadResult(status="not_found", detail="当前页面找不到 Product Photos section。")
    open_section_for_edit(page, section)
    section = find_section(page, PRODUCT_PHOTOS_SECTION) or section
    section_path = str(section.get("path") or "")
    if not section_path:
        return PhotoUploadResult(status="not_found", detail="Product Photos section 缺少稳定 DOM path。")

    state = _photo_state(page, section_path)
    initial_count = state.get("completion_count")
    capacity = state.get("capacity")
    current_images = int(state.get("visible_image_count") or 0)
    current_sources = {
        str(value).strip()
        for value in state.get("visible_image_sources") or []
        if str(value).strip()
    }
    current_completion = (
        int(state["completion_count"])
        if state.get("completion_count") is not None
        else None
    )
    input_meta = list(state.get("file_inputs") or [])
    if not input_meta:
        return PhotoUploadResult(
            status="unsupported",
            initial_count=initial_count,
            final_count=initial_count,
            capacity=capacity,
            detail=(
                "Product Photos 已展开，但卡片内没有 input[type=file]；"
                "拒绝猜测其它按钮。需要真实 DOM 更新定位。"
            ),
        )

    first_meta = input_meta[0]
    result = PhotoUploadResult(
        status="running",
        initial_count=initial_count,
        final_count=initial_count,
        capacity=capacity,
        accept=str(first_meta.get("accept") or ""),
        multiple=bool(first_meta.get("multiple")),
    )

    for path in resolved_paths:
        if capacity is not None and initial_count is not None:
            if initial_count + result.staged >= capacity:
                result.items.append(
                    {
                        "path": str(path),
                        "status": "skipped_full",
                        "detail": f"Product Photos 最多 {capacity} 张。",
                    }
                )
                continue

        file_input = _select_file_input(page, section_path)
        if file_input is None:
            result.items.append(
                {
                    "path": str(path),
                    "status": "file_input_missing",
                    "detail": "上传过程中找不到可用 input[type=file]。",
                }
            )
            continue

        result.attempted += 1
        before_images = current_images
        before_sources = set(current_sources)
        before_completion = current_completion
        try:
            file_input.set_input_files(str(path))
            try:
                immediate_files = int(
                    file_input.evaluate("el => el.files ? el.files.length : 0") or 0
                )
            except Exception:
                immediate_files = 0

            settled = _wait_for_staged_signal(
                page,
                section_path,
                before_images=before_images,
                before_sources=before_sources,
                before_completion=before_completion,
                timeout_ms=timeout_ms,
            )
            current_images = int(settled.get("visible_image_count") or 0)
            current_sources = {
                str(value).strip()
                for value in settled.get("visible_image_sources") or []
                if str(value).strip()
            }
            current_completion = (
                int(settled["completion_count"])
                if settled.get("completion_count") is not None
                else None
            )
            accepted = _stage_accepted(
                settled,
                before_images=before_images,
                before_sources=before_sources,
                before_completion=before_completion,
            )
            if accepted:
                result.staged += 1
                result.items.append(
                    {
                        "path": str(path),
                        "status": "staged",
                        "input_files": immediate_files,
                        "before_visible_images": before_images,
                        "after_visible_images": current_images,
                        "before_completion_count": before_completion,
                        "after_completion_count": current_completion,
                        "new_visible_sources": sorted(current_sources.difference(before_sources)),
                        "detail": (
                            "Makro 已出现新增图片预览/来源或计数变化；"
                            "图片已进入未保存编辑事务。"
                        ),
                    }
                )
            else:
                result.items.append(
                    {
                        "path": str(path),
                        "status": "staging_unconfirmed",
                        "input_files": immediate_files,
                        "before_visible_images": before_images,
                        "after_visible_images": current_images,
                        "before_completion_count": before_completion,
                        "after_completion_count": current_completion,
                        "detail": (
                            "文件选择已执行，但在超时前 Makro 没有出现新增预览、"
                            "新图片来源或计数变化；input.files 本身不再视为上传成功。"
                        ),
                    }
                )
        except Exception as exc:
            result.items.append(
                {
                    "path": str(path),
                    "status": "upload_error",
                    "detail": str(exc),
                }
            )

    final_state = _photo_state(page, section_path)
    result.final_count = final_state.get("completion_count")
    if result.staged == result.attempted and result.attempted > 0:
        result.status = "staged"
        result.detail = "所有尝试图片都得到 Makro 页面接受信号，等待 section Save。"
    elif result.staged > 0:
        result.status = "partial_staged"
        result.detail = "部分图片得到 Makro 页面接受信号，部分无法确认。"
    elif result.attempted == 0:
        result.status = "skipped"
        result.detail = "没有实际执行图片 staging。"
    else:
        result.status = "staging_unconfirmed"
        result.detail = (
            "已执行文件选择，但 Makro 没有确认任何图片；"
            "调用方不得据此点击 Product Photos Save。"
        )
    return result


def verify_persisted_photo_count(
    page: Page,
    *,
    initial_count: int | None,
    expected_added: int,
    timeout_ms: int = 10_000,
) -> dict[str, Any]:
    """Poll the collapsed Product Photos counter after its Save transaction."""

    if expected_added <= 0:
        state = inspect_product_photos(page)
        return {
            "status": "skipped",
            "initial_count": initial_count,
            "final_count": state.get("completion_count"),
            "expected_added": expected_added,
            "detail": "没有 staged 图片需要持久化复核。",
        }
    if initial_count is None:
        return {
            "status": "validation_failed",
            "initial_count": initial_count,
            "final_count": None,
            "expected_added": expected_added,
            "detail": "Save 前无法读取 Product Photos 完成计数，不能证明计数增长。",
        }

    target = int(initial_count) + int(expected_added)
    deadline = time.monotonic() + timeout_ms / 1000.0
    final_count: int | None = None
    while time.monotonic() < deadline:
        state = inspect_product_photos(page)
        raw = state.get("completion_count")
        final_count = int(raw) if raw is not None else None
        if final_count is not None and final_count >= target:
            return {
                "status": "persisted_verified",
                "initial_count": initial_count,
                "final_count": final_count,
                "expected_added": expected_added,
                "detail": "Product Photos Save 后完成计数按预期增加。",
            }
        page.wait_for_timeout(250)

    return {
        "status": "validation_failed",
        "initial_count": initial_count,
        "final_count": final_count,
        "expected_added": expected_added,
        "detail": (
            f"Product Photos Save 后 {timeout_ms}ms 内完成计数未达到 {target}；"
            "不能证明 staged 图片已持久化。"
        ),
    }
