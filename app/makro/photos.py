from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from .sections import find_section, open_section_for_edit

PRODUCT_PHOTOS_SECTION = "Product Photos"


@dataclass(slots=True)
class PhotoUploadResult:
    """State of listing images accepted into the open Product Photos editor."""

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


def _photo_surface(page: Page, section_path: str):
    """Return Product Photos plus the sibling image-slot gallery."""

    card = page.locator(section_path)
    if card.count() != 1:
        return card
    parent = card.locator("xpath=..")
    if parent.count() == 1:
        markers = parent.locator(
            '[class*="ImageGalleryWrapper"], [class*="AddProductImage"], input[type="file"]'
        )
        if markers.count() > 0:
            return parent
    return card


def _visible_add_product_image_tiles(page: Page, section_path: str) -> list[Any]:
    """Return all visible orange add-image slots in DOM/gallery order.

    Makro Cases & Covers renders one fixed slot per required photo role. After
    Front View is filled, for example, four AddProductImage controls remain for
    Side View / Feature View / Close Up / Life Style. Multiple visible controls
    are therefore expected and are not an ambiguity.
    """

    tiles = _photo_surface(page, section_path).locator('[class*="AddProductImage"]')
    visible: list[Any] = []
    for index in range(tiles.count()):
        candidate = tiles.nth(index)
        try:
            if candidate.is_visible():
                visible.append(candidate)
        except Exception:
            continue
    return visible


def _photo_state(page: Page, section_path: str) -> dict[str, Any]:
    card = page.locator(section_path)
    if card.count() != 1:
        return {"found": False, "detail": f"section path 匹配 {card.count()} 个节点"}

    surface = _photo_surface(page, section_path)
    payload = surface.evaluate(
        r"""surface => {
          const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
          const titleEl = surface.querySelector(
            '[class*="styles__Title-"], [class*="Title-ef7o31"], [class*="Title-"]'
          );
          const inputs = Array.from(surface.querySelectorAll('input[type="file"]'));
          const addTiles = Array.from(
            surface.querySelectorAll('[class*="AddProductImage"]')
          ).filter(el => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
          });
          const images = Array.from(surface.querySelectorAll('img')).filter(img => {
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
            add_image_tile_count: addTiles.length,
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


def _raw_file_input(page: Page, section_path: str):
    """Return the first usable file input in the image-slot surface.

    If Makro renders several slot inputs at once, DOM order is the same visual
    left-to-right slot order, so the first usable input is the next empty slot.
    """

    inputs = _photo_surface(page, section_path).locator('input[type="file"]')
    for index in range(inputs.count()):
        candidate = inputs.nth(index)
        try:
            if not candidate.is_disabled():
                return candidate
        except Exception:
            continue
    return None


def _add_product_image_tile(page: Page, section_path: str):
    """Return the next empty image slot, left-to-right."""

    visible = _visible_add_product_image_tiles(page, section_path)
    return visible[0] if visible else None


class _DynamicPhotoFileTarget:
    """File target backed by Makro's next empty fixed image slot."""

    def __init__(self, page: Page, section_path: str) -> None:
        self.page = page
        self.section_path = section_path
        self._selected = False

    def _current_path(self) -> str:
        section = find_section(self.page, PRODUCT_PHOTOS_SECTION)
        path = str((section or {}).get("path") or "")
        return path or self.section_path

    def set_input_files(self, files: str | Path) -> None:
        upload = str(Path(files).expanduser().resolve())
        current_path = self._current_path()
        direct = _raw_file_input(self.page, current_path)
        if direct is not None:
            direct.set_input_files(upload)
            self._selected = True
            return

        tile = _add_product_image_tile(self.page, current_path)
        if tile is None:
            raise RuntimeError("Product Photos 已没有未完成的图片槽位。")

        try:
            with self.page.expect_file_chooser(timeout=2_500) as chooser_info:
                tile.click()
            chooser_info.value.set_files(upload)
            self._selected = True
            return
        except PlaywrightTimeoutError:
            # Some Makro builds mount a hidden input only after clicking +.
            pass

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            current_path = self._current_path()
            direct = _raw_file_input(self.page, current_path)
            if direct is not None:
                direct.set_input_files(upload)
                self._selected = True
                return
            self.page.wait_for_timeout(150)

        raise RuntimeError(
            "点击下一个 Product Photos 橙色 + 后既未出现 file chooser，也未挂载 file input。"
        )

    def evaluate(self, _expression: str) -> int:
        return 1 if self._selected else 0


def _select_file_input(page: Page, section_path: str):
    """Return a direct input or a target for the next empty image slot."""

    direct = _raw_file_input(page, section_path)
    if direct is not None:
        return direct
    if _add_product_image_tile(page, section_path) is not None:
        return _DynamicPhotoFileTarget(page, section_path)
    return None


def _stage_accepted(
    state: dict[str, Any],
    *,
    before_images: int,
    before_sources: set[str],
    before_completion: int | None,
    before_add_tiles: int | None = None,
) -> bool:
    """Return True only when the gallery visibly consumed one image slot."""

    images = int(state.get("visible_image_count") or 0)
    sources = {
        str(value).strip()
        for value in state.get("visible_image_sources") or []
        if str(value).strip()
    }
    raw_completion = state.get("completion_count")
    completion = int(raw_completion) if raw_completion is not None else None
    add_tiles = int(state.get("add_image_tile_count") or 0)
    return bool(
        images > before_images
        or sources.difference(before_sources)
        or (
            before_completion is not None
            and completion is not None
            and completion > before_completion
        )
        or (
            before_add_tiles is not None
            and add_tiles < before_add_tiles
        )
    )


def _wait_for_staged_signal(
    page: Page,
    section_path: str,
    *,
    before_images: int,
    before_sources: set[str],
    before_completion: int | None,
    before_add_tiles: int | None = None,
    timeout_ms: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_ms / 1000.0
    latest = _photo_state(page, section_path)
    while time.monotonic() < deadline:
        section = find_section(page, PRODUCT_PHOTOS_SECTION)
        live_path = str((section or {}).get("path") or section_path)
        latest = _photo_state(page, live_path)
        if _stage_accepted(
            latest,
            before_images=before_images,
            before_sources=before_sources,
            before_completion=before_completion,
            before_add_tiles=before_add_tiles,
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
    """Stage images into the fixed Product Photos slots; never Save the card."""

    resolved_paths: list[Path] = []
    seen: set[str] = set()
    for raw in image_paths:
        path = Path(raw).expanduser().resolve()
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        if not path.is_file():
            return PhotoUploadResult(status="invalid_input", detail=f"上传图片不存在或不是文件：{path}")
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
    available = int(state.get("add_image_tile_count") or 0)
    if available == 0 and _raw_file_input(page, section_path) is None:
        return PhotoUploadResult(
            status="unsupported",
            initial_count=initial_count,
            final_count=initial_count,
            capacity=capacity,
            detail="Product Photos 已展开，但没有未完成图片槽位或 file input。",
        )

    first_meta = (state.get("file_inputs") or [{}])[0] if state.get("file_inputs") else {}
    result = PhotoUploadResult(
        status="running",
        initial_count=initial_count,
        final_count=initial_count,
        capacity=capacity,
        accept=str(first_meta.get("accept") or ""),
        multiple=bool(first_meta.get("multiple")),
    )

    current_images = int(state.get("visible_image_count") or 0)
    current_sources = {
        str(value).strip()
        for value in state.get("visible_image_sources") or []
        if str(value).strip()
    }
    current_completion = int(state["completion_count"]) if state.get("completion_count") is not None else None
    current_add_tiles = int(state.get("add_image_tile_count") or 0)

    for slot_offset, path in enumerate(resolved_paths, start=1):
        section = find_section(page, PRODUCT_PHOTOS_SECTION) or section
        section_path = str(section.get("path") or section_path)
        target = _select_file_input(page, section_path)
        if target is None:
            result.items.append(
                {
                    "path": str(path),
                    "status": "file_input_missing",
                    "slot_offset": slot_offset,
                    "detail": "找不到下一个未完成图片槽位。",
                }
            )
            continue

        result.attempted += 1
        before_images = current_images
        before_sources = set(current_sources)
        before_completion = current_completion
        before_add_tiles = current_add_tiles
        try:
            target.set_input_files(str(path))
            settled = _wait_for_staged_signal(
                page,
                section_path,
                before_images=before_images,
                before_sources=before_sources,
                before_completion=before_completion,
                before_add_tiles=before_add_tiles,
                timeout_ms=timeout_ms,
            )
            current_images = int(settled.get("visible_image_count") or 0)
            current_sources = {
                str(value).strip()
                for value in settled.get("visible_image_sources") or []
                if str(value).strip()
            }
            current_completion = int(settled["completion_count"]) if settled.get("completion_count") is not None else None
            current_add_tiles = int(settled.get("add_image_tile_count") or 0)
            accepted = _stage_accepted(
                settled,
                before_images=before_images,
                before_sources=before_sources,
                before_completion=before_completion,
                before_add_tiles=before_add_tiles,
            )
            if accepted:
                result.staged += 1
                result.items.append(
                    {
                        "path": str(path),
                        "status": "staged",
                        "slot_offset": slot_offset,
                        "remaining_empty_slots": current_add_tiles,
                    }
                )
            else:
                result.items.append(
                    {
                        "path": str(path),
                        "status": "staging_unconfirmed",
                        "slot_offset": slot_offset,
                        "detail": "Makro 没有确认该图片槽已被占用。",
                    }
                )
        except Exception as exc:
            result.items.append(
                {
                    "path": str(path),
                    "status": "upload_error",
                    "slot_offset": slot_offset,
                    "detail": str(exc),
                }
            )

    section = find_section(page, PRODUCT_PHOTOS_SECTION) or section
    section_path = str(section.get("path") or section_path)
    final_state = _photo_state(page, section_path)
    result.final_count = final_state.get("completion_count")
    if result.staged == len(resolved_paths):
        result.status = "staged"
        result.detail = f"{result.staged}/{len(resolved_paths)} 张图片已按固定槽位顺序 staged，等待一次 Save。"
    elif result.staged > 0:
        result.status = "partial_staged"
        result.detail = f"只确认 {result.staged}/{len(resolved_paths)} 个图片槽已填写。"
    else:
        result.status = "staging_unconfirmed"
        result.detail = "没有图片槽得到 Makro 接受信号。"
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
