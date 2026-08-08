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
    """State of files staged into the open Product Photos editor.

    ``staged`` is intentionally not called persisted/uploaded: Makro keeps the
    card in an unsaved edit transaction until its section Save is clicked.
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


def _wait_for_staged_signal(
    page: Page,
    section_path: str,
    *,
    before_images: int,
    timeout_ms: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_ms / 1000.0
    latest = _photo_state(page, section_path)
    while time.monotonic() < deadline:
        latest = _photo_state(page, section_path)
        file_count = max(
            [int(item.get("files") or 0) for item in latest.get("file_inputs") or []]
            or [0]
        )
        images = int(latest.get("visible_image_count") or 0)
        if file_count > 0 or images > before_images:
            return latest
        page.wait_for_timeout(200)
    return latest


def upload_product_photos(
    page: Page,
    image_paths: Iterable[str | Path],
    *,
    timeout_ms: int = 8_000,
) -> PhotoUploadResult:
    """Stage explicit listing images into Product Photos; never Save the card.

    Persistence is a separate transaction. A caller must subsequently invoke the
    section Save primitive and verify the completion counter after re-open.
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
                timeout_ms=timeout_ms,
            )
            current_images = int(settled.get("visible_image_count") or 0)
            staged = immediate_files > 0 or current_images > before_images
            if staged:
                result.staged += 1
                result.items.append(
                    {
                        "path": str(path),
                        "status": "staged",
                        "input_files": immediate_files,
                        "before_visible_images": before_images,
                        "after_visible_images": current_images,
                        "detail": "文件已进入 Product Photos 编辑事务；尚未宣称持久化。",
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
                        "detail": (
                            "set_input_files 未报错，但未观察到 files/预览变化；"
                            "不能把它当成已上传或已保存。"
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
        result.detail = "所有尝试文件都已进入未保存的 Product Photos 编辑事务。"
    elif result.staged > 0:
        result.status = "partial_staged"
        result.detail = "部分文件已进入编辑事务，部分无法确认。"
    elif result.attempted == 0:
        result.status = "skipped"
        result.detail = "没有实际执行图片 staging。"
    else:
        result.status = "staging_unconfirmed"
        result.detail = "已执行文件选择，但没有任何文件得到 staging 信号。"
    return result


def verify_persisted_photo_count(
    page: Page,
    *,
    initial_count: int | None,
    expected_added: int,
) -> dict[str, Any]:
    """Verify Product Photos only after the section Save transaction finished."""

    state = inspect_product_photos(page)
    final_count = state.get("completion_count")
    if expected_added <= 0:
        return {
            "status": "skipped",
            "initial_count": initial_count,
            "final_count": final_count,
            "expected_added": expected_added,
            "detail": "没有 staged 图片需要持久化复核。",
        }
    if initial_count is None or final_count is None:
        return {
            "status": "validation_failed",
            "initial_count": initial_count,
            "final_count": final_count,
            "expected_added": expected_added,
            "detail": "Save 后无法读取 Product Photos 完成计数，不能证明图片已持久化。",
        }
    passed = int(final_count) >= int(initial_count) + expected_added
    return {
        "status": "persisted_verified" if passed else "validation_failed",
        "initial_count": initial_count,
        "final_count": final_count,
        "expected_added": expected_added,
        "detail": (
            "Product Photos Save 后完成计数按预期增加。"
            if passed
            else "Product Photos Save 后完成计数没有按 staged 图片数量增加。"
        ),
    }
