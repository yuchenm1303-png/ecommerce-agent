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
    status: str
    initial_count: int | None = None
    final_count: int | None = None
    capacity: int | None = None
    attempted: int = 0
    uploaded: int = 0
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
            "uploaded": self.uploaded,
            "accept": self.accept,
            "multiple": self.multiple,
            "items": self.items,
            "detail": self.detail,
        }


def parse_completion_counter(title: str) -> tuple[int, int] | None:
    match = re.search(r"\((\d+)\s*/\s*(\d+)\)\s*$", str(title or ""))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _photo_state(page: Page, section_path: str) -> dict[str, Any]:
    card = page.locator(section_path)
    if card.count() != 1:
        return {"found": False, "detail": f"section path 匹配 {card.count()} 个节点"}

    payload = card.evaluate(
        """card => {
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
            })),
            visible_image_count: images.length,
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


def _wait_for_upload_progress(
    page: Page,
    section_path: str,
    *,
    before_count: int | None,
    before_images: int,
    timeout_ms: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_ms / 1000.0
    latest = _photo_state(page, section_path)
    while time.monotonic() < deadline:
        latest = _photo_state(page, section_path)
        count = latest.get("completion_count")
        images = int(latest.get("visible_image_count") or 0)
        if before_count is not None and count is not None and int(count) > before_count:
            return latest
        if images > before_images:
            return latest
        page.wait_for_timeout(250)
    return latest


def upload_product_photos(
    page: Page,
    image_paths: Iterable[str | Path],
    *,
    timeout_ms: int = 30_000,
) -> PhotoUploadResult:
    """Upload explicit listing images through the live Product Photos file input.

    This is a browser execution primitive only. It never chooses images from
    evidence automatically and never clicks section Save / Cancel / Send to QC.
    Callers must pass the exact files intended for the listing.
    """

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
        return PhotoUploadResult(
            status="not_found",
            detail="当前页面找不到 Product Photos section。",
        )
    open_section_for_edit(page, section)
    section = find_section(page, PRODUCT_PHOTOS_SECTION) or section
    section_path = str(section.get("path") or "")
    if not section_path:
        return PhotoUploadResult(
            status="not_found",
            detail="Product Photos section 缺少稳定 DOM path。",
        )

    state = _photo_state(page, section_path)
    initial_count = state.get("completion_count")
    capacity = state.get("capacity")
    initial_images = int(state.get("visible_image_count") or 0)
    input_meta = list(state.get("file_inputs") or [])
    if not input_meta:
        return PhotoUploadResult(
            status="unsupported",
            initial_count=initial_count,
            final_count=initial_count,
            capacity=capacity,
            detail=(
                "Product Photos 已展开，但卡片内没有 input[type=file]；"
                "拒绝猜测其它按钮。需要用真实 DOM 补充该上传控件定位。"
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

    current_count = initial_count
    current_images = initial_images
    for path in resolved_paths:
        if capacity is not None and current_count is not None and current_count >= capacity:
            result.items.append(
                {
                    "path": str(path),
                    "status": "skipped_full",
                    "detail": f"Product Photos 已达到容量 {capacity}。",
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
        before_count = current_count
        before_images = current_images
        try:
            file_input.set_input_files(str(path))
            settled = _wait_for_upload_progress(
                page,
                section_path,
                before_count=before_count,
                before_images=before_images,
                timeout_ms=timeout_ms,
            )
            current_count = settled.get("completion_count")
            current_images = int(settled.get("visible_image_count") or 0)
            progressed = (
                before_count is not None
                and current_count is not None
                and int(current_count) > before_count
            ) or current_images > before_images
            if progressed:
                result.uploaded += 1
                result.items.append(
                    {
                        "path": str(path),
                        "status": "validated",
                        "before_count": before_count,
                        "after_count": current_count,
                        "detail": "文件选择后 Product Photos 计数或可见缩略图增加。",
                    }
                )
            else:
                result.items.append(
                    {
                        "path": str(path),
                        "status": "validation_failed",
                        "before_count": before_count,
                        "after_count": current_count,
                        "detail": (
                            f"set_input_files 已执行，但 {timeout_ms}ms 内未观察到"
                            "图片计数或缩略图增加。"
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
    if result.uploaded == result.attempted and result.attempted > 0:
        result.status = "validated"
        result.detail = "所有尝试上传的图片均观察到页面进度变化。"
    elif result.uploaded > 0:
        result.status = "partial"
        result.detail = "部分图片上传已验证，部分失败或无法确认。"
    elif result.attempted == 0:
        result.status = "skipped"
        result.detail = "没有实际执行图片上传。"
    else:
        result.status = "validation_failed"
        result.detail = "已执行图片选择，但没有任何图片通过页面进度验证。"
    return result
