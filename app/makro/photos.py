from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from .sections import find_section, open_section_for_edit

PRODUCT_PHOTOS_SECTION = "Product Photos"
PHOTO_SLOT_IDS = tuple(f"thumbnail_{index}" for index in range(5))


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
    """Return the nearest Product Photos ancestor that owns the five thumbnails.

    The real Makro editor renders ``#thumbnail_0`` .. ``#thumbnail_4`` plus one
    shared ``input[type=file]``. The thumbnail gallery can be a sibling of the
    title card, so walk a few ancestors instead of assuming one CSS wrapper.
    """

    current = page.locator(section_path)
    if current.count() != 1:
        return current
    best = current
    for _ in range(4):
        if current.count() != 1:
            break
        slot_count = current.locator('[id^="thumbnail_"]').count()
        input_count = current.locator('input[type="file"]').count()
        if slot_count >= 5 or (slot_count > 0 and input_count > 0):
            return current
        best = current
        parent = current.locator("xpath=..")
        if parent.count() != 1:
            break
        current = parent
    return best


def _slot_snapshot(page: Page, section_path: str) -> list[dict[str, Any]]:
    surface = _photo_surface(page, section_path)
    return surface.evaluate(
        r"""surface => {
          const visible = el => {
            if (!el) return false;
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return rect.width > 0 && rect.height > 0 &&
              style.display !== 'none' && style.visibility !== 'hidden';
          };
          const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
          const slots = [];
          for (let index = 0; index < 5; index += 1) {
            const id = `thumbnail_${index}`;
            const slot = surface.querySelector(`#${id}`);
            if (!slot) continue;
            const plus = Array.from(slot.querySelectorAll('i.fa-plus, .fa-plus')).find(visible) || null;
            const check = Array.from(slot.querySelectorAll('i.fa-check, .fa-check, .fa-check-circle')).find(visible) || null;
            const labelCandidates = Array.from(slot.querySelectorAll('span'))
              .map(el => clean(el.innerText || el.textContent))
              .filter(Boolean);
            const images = Array.from(slot.querySelectorAll('img'))
              .map(img => clean(img.getAttribute('src')))
              .filter(Boolean);
            slots.push({
              id,
              index,
              label: labelCandidates[labelCandidates.length - 1] || '',
              has_plus: Boolean(plus),
              has_check: Boolean(check),
              image_sources: images,
            });
          }
          return slots;
        }"""
    )


def _photo_state(page: Page, section_path: str) -> dict[str, Any]:
    surface = _photo_surface(page, section_path)
    if surface.count() != 1:
        return {"found": False, "detail": f"Product Photos surface 匹配 {surface.count()} 个节点"}

    slots = _slot_snapshot(page, section_path)
    inputs = surface.locator('input[type="file"]')
    input_meta: list[dict[str, Any]] = []
    for index in range(inputs.count()):
        candidate = inputs.nth(index)
        try:
            input_meta.append(
                {
                    "accept": str(candidate.get_attribute("accept") or "").strip(),
                    "multiple": bool(candidate.get_attribute("multiple") is not None),
                    "disabled": bool(candidate.is_disabled()),
                }
            )
        except Exception:
            continue

    section = find_section(page, PRODUCT_PHOTOS_SECTION)
    title = str((section or {}).get("title") or "")
    counter = parse_completion_counter(title)
    empty_slot_ids = [str(slot["id"]) for slot in slots if slot.get("has_plus")]
    filled_slot_ids = [str(slot["id"]) for slot in slots if not slot.get("has_plus")]
    sources = [
        source
        for slot in slots
        for source in (slot.get("image_sources") or [])
        if str(source).strip()
    ]
    return {
        "found": True,
        "title": title,
        "completion_count": counter[0] if counter else None,
        "capacity": counter[1] if counter else (5 if len(slots) == 5 else None),
        "slot_count": len(slots),
        "slots": slots,
        "empty_slot_ids": empty_slot_ids,
        "filled_slot_ids": filled_slot_ids,
        "add_image_tile_count": len(empty_slot_ids),
        "file_input_count": len(input_meta),
        "file_inputs": input_meta,
        "visible_image_count": len(sources),
        "visible_image_sources": sources,
    }


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
    """Return Makro's one shared Product Photos file input."""

    inputs = _photo_surface(page, section_path).locator('input[type="file"]')
    usable = []
    for index in range(inputs.count()):
        candidate = inputs.nth(index)
        try:
            if not candidate.is_disabled():
                usable.append(candidate)
        except Exception:
            continue
    if len(usable) > 1:
        raise RuntimeError(
            f"Product Photos 出现 {len(usable)} 个可用共享 file input；拒绝猜测。"
        )
    return usable[0] if usable else None


def _next_empty_photo_slot(page: Page, section_path: str) -> tuple[str, Any] | None:
    """Return the first real empty ``#thumbnail_N`` slot in DOM order."""

    surface = _photo_surface(page, section_path)
    for slot_id in PHOTO_SLOT_IDS:
        slot = surface.locator(f"#{slot_id}")
        if slot.count() != 1:
            continue
        plus = slot.locator("i.fa-plus, .fa-plus")
        for index in range(plus.count()):
            candidate = plus.nth(index)
            try:
                if candidate.is_visible():
                    return slot_id, slot
            except Exception:
                continue
    return None


class _DynamicPhotoFileTarget:
    """Upload target bound to one concrete Makro ``#thumbnail_N`` slot."""

    def __init__(self, page: Page, section_path: str, slot_id: str) -> None:
        self.page = page
        self.section_path = section_path
        self.slot_id = slot_id
        self._selected = False

    def _current_path(self) -> str:
        section = find_section(self.page, PRODUCT_PHOTOS_SECTION)
        path = str((section or {}).get("path") or "")
        return path or self.section_path

    def set_input_files(self, files: str | Path) -> None:
        upload = str(Path(files).expanduser().resolve())
        current_path = self._current_path()
        surface = _photo_surface(self.page, current_path)
        slot = surface.locator(f"#{self.slot_id}")
        if slot.count() != 1:
            raise RuntimeError(f"Product Photos 找不到目标图片槽 #{self.slot_id}。")

        plus = slot.locator("i.fa-plus, .fa-plus")
        clickable = None
        for index in range(plus.count()):
            candidate = plus.nth(index)
            try:
                if candidate.is_visible():
                    clickable = candidate
                    break
            except Exception:
                continue
        if clickable is None:
            raise RuntimeError(f"图片槽 #{self.slot_id} 已没有可见橙色 +，不能重复上传。")

        # Critical real-DOM behavior: clicking a thumbnail chooses the role;
        # the entire Product Photos editor owns only one shared file input.
        try:
            with self.page.expect_file_chooser(timeout=2_000) as chooser_info:
                clickable.click()
            chooser_info.value.set_files(upload)
            self._selected = True
            return
        except PlaywrightTimeoutError:
            pass

        shared = _raw_file_input(self.page, current_path)
        if shared is None:
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                current_path = self._current_path()
                shared = _raw_file_input(self.page, current_path)
                if shared is not None:
                    break
                self.page.wait_for_timeout(150)
        if shared is None:
            raise RuntimeError(
                f"点击 #{self.slot_id} 的橙色 + 后没有 file chooser，也找不到共享 input[type=file]。"
            )
        shared.set_input_files(upload)
        self._selected = True

    def evaluate(self, _expression: str) -> int:
        return 1 if self._selected else 0


def _select_file_input(page: Page, section_path: str):
    """Bind the next empty real thumbnail slot to Makro's shared file input."""

    next_slot = _next_empty_photo_slot(page, section_path)
    if next_slot is None:
        return None
    slot_id, _slot = next_slot
    return _DynamicPhotoFileTarget(page, section_path, slot_id)


def _stage_accepted(
    state: dict[str, Any],
    *,
    before_images: int,
    before_sources: set[str],
    before_completion: int | None,
    before_add_tiles: int | None = None,
    target_slot_id: str | None = None,
) -> bool:
    """Return True when the selected fixed thumbnail is no longer empty."""

    empty_slots = {str(value) for value in state.get("empty_slot_ids") or []}
    if target_slot_id and target_slot_id not in empty_slots:
        return True

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
    target_slot_id: str | None = None,
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
            target_slot_id=target_slot_id,
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
    """Stage images into Makro's five fixed thumbnail slots; never Save."""

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
    result = PhotoUploadResult(
        status="running",
        initial_count=initial_count,
        final_count=initial_count,
        capacity=capacity,
        accept=str(((state.get("file_inputs") or [{}])[0]).get("accept") or ""),
        multiple=False,
    )

    current_images = int(state.get("visible_image_count") or 0)
    current_sources = {
        str(value).strip()
        for value in state.get("visible_image_sources") or []
        if str(value).strip()
    }
    current_completion = int(state["completion_count"]) if state.get("completion_count") is not None else None
    current_add_tiles = int(state.get("add_image_tile_count") or 0)

    for path in resolved_paths:
        section = find_section(page, PRODUCT_PHOTOS_SECTION) or section
        section_path = str(section.get("path") or section_path)
        target = _select_file_input(page, section_path)
        if target is None:
            result.items.append({"path": str(path), "status": "slot_missing", "detail": "没有下一个带橙色 + 的 #thumbnail_N 图片槽。"})
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
                target_slot_id=target.slot_id,
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
                target_slot_id=target.slot_id,
            )
            if accepted:
                result.staged += 1
                result.items.append(
                    {
                        "path": str(path),
                        "status": "staged",
                        "slot_id": target.slot_id,
                        "remaining_empty_slots": current_add_tiles,
                    }
                )
            else:
                result.items.append(
                    {
                        "path": str(path),
                        "status": "staging_unconfirmed",
                        "slot_id": target.slot_id,
                        "detail": "Makro 没有确认目标 thumbnail 槽已被占用。",
                    }
                )
        except Exception as exc:
            result.items.append(
                {"path": str(path), "status": "upload_error", "slot_id": target.slot_id, "detail": str(exc)}
            )

    section = find_section(page, PRODUCT_PHOTOS_SECTION) or section
    section_path = str(section.get("path") or section_path)
    final_state = _photo_state(page, section_path)
    result.final_count = final_state.get("completion_count")
    if result.staged == len(resolved_paths):
        result.status = "staged"
        result.detail = f"{result.staged}/{len(resolved_paths)} 个固定 thumbnail 槽已填入，等待 Save。"
    elif result.staged > 0:
        result.status = "partial_staged"
        result.detail = f"仅 {result.staged}/{len(resolved_paths)} 个固定 thumbnail 槽确认填入。"
    else:
        result.status = "staging_unconfirmed"
        result.detail = "没有任何固定 thumbnail 槽确认填入。"
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
            "不能证明图片已持久化。"
        ),
    }
