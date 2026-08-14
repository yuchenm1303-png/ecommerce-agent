from __future__ import annotations

import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageOps, UnidentifiedImageError
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from .sections import find_section, open_section_for_edit

PRODUCT_PHOTOS_SECTION = "Product Photos"
PHOTO_SLOT_IDS = tuple(f"thumbnail_{index}" for index in range(5))
MAX_UPLOAD_EDGE = 4096
JPEG_QUALITY = 92
_PLACEHOLDER_SOURCE_TOKENS = (
    "placeholder",
    "no-image",
    "no_image",
    "empty-image",
    "empty_image",
    "add-image",
    "add_image",
    "image-icon",
    "image_icon",
)


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


def _meaningful_image_source(source: Any) -> bool:
    value = str(source or "").strip()
    if not value:
        return False
    folded = value.casefold()
    return not any(token in folded for token in _PLACEHOLDER_SOURCE_TOKENS)


def _slot_is_empty(slot: dict[str, Any]) -> bool:
    """Classify a Makro thumbnail by state, not by the orange plus alone.

    Makro can temporarily leave the plus icon mounted while replacing the slot
    preview. A check mark or a real image source therefore wins over the stale
    empty-state icon. This prevents the same logical slot from being selected
    twice after a successful upload.
    """

    if bool(slot.get("has_check")):
        return False
    if any(_meaningful_image_source(value) for value in slot.get("image_sources") or []):
        return False
    return bool(slot.get("has_plus"))


def _photo_surface(page: Page, section_path: str):
    """Return the nearest Product Photos ancestor that owns the five thumbnails."""

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
    raw_slots = surface.evaluate(
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
              .filter(visible)
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
    slots: list[dict[str, Any]] = []
    for raw in raw_slots or []:
        slot = dict(raw)
        slot["is_empty"] = _slot_is_empty(slot)
        slot["has_meaningful_image"] = any(
            _meaningful_image_source(value) for value in slot.get("image_sources") or []
        )
        slots.append(slot)
    return slots


def _uploading_visible(page: Page, section_path: str) -> bool:
    """Return whether Makro currently renders an Uploading status."""

    surface = _photo_surface(page, section_path)
    matches = surface.get_by_text(re.compile(r"\bUploading\b", re.IGNORECASE))
    for index in range(matches.count()):
        try:
            if matches.nth(index).is_visible():
                return True
        except Exception:
            continue
    return False


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
    empty_slot_ids = [str(slot["id"]) for slot in slots if bool(slot.get("is_empty"))]
    filled_slot_ids = [str(slot["id"]) for slot in slots if not bool(slot.get("is_empty"))]
    sources = [
        source
        for slot in slots
        for source in (slot.get("image_sources") or [])
        if _meaningful_image_source(source)
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
        "uploading": _uploading_visible(page, section_path),
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
    """Return the first logical empty thumbnail in fixed Makro role order."""

    surface = _photo_surface(page, section_path)
    snapshots = {str(slot.get("id")): slot for slot in _slot_snapshot(page, section_path)}
    for slot_id in PHOTO_SLOT_IDS:
        snapshot = snapshots.get(slot_id)
        if not snapshot or not bool(snapshot.get("is_empty")):
            continue
        slot = surface.locator(f"#{slot_id}")
        if slot.count() != 1:
            continue
        try:
            if slot.is_visible():
                return slot_id, slot
        except Exception:
            continue
    return None


def _visible_upload_photo_button(page: Page, section_path: str):
    """Return the active role panel's visible blue Upload Photo control."""

    surface = _photo_surface(page, section_path)
    text_matches = surface.get_by_text("Upload Photo", exact=True)
    visible = []
    for index in range(text_matches.count()):
        candidate = text_matches.nth(index)
        try:
            if candidate.is_visible():
                button = candidate.locator("xpath=ancestor-or-self::button[1]")
                control = button if button.count() == 1 else candidate
                if control.is_enabled():
                    visible.append(control)
        except Exception:
            continue
    if len(visible) > 1:
        raise RuntimeError(
            f"Product Photos 当前出现 {len(visible)} 个可见 Upload Photo；拒绝猜测。"
        )
    return visible[0] if visible else None


def _wait_for_upload_photo_button(page: Page, section_path: str, *, timeout_ms: int = 2_000):
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        current = _visible_upload_photo_button(page, section_path)
        if current is not None:
            return current
        page.wait_for_timeout(50)
    return None


def _acceptance_signal(
    state: dict[str, Any],
    *,
    before_images: int,
    before_sources: set[str],
    before_completion: int | None,
    before_add_tiles: int | None = None,
    target_slot_id: str | None = None,
) -> str:
    empty_slots = {str(value) for value in state.get("empty_slot_ids") or []}
    if target_slot_id and target_slot_id not in empty_slots:
        return "target_slot_consumed"

    images = int(state.get("visible_image_count") or 0)
    sources = {
        str(value).strip()
        for value in state.get("visible_image_sources") or []
        if str(value).strip()
    }
    raw_completion = state.get("completion_count")
    completion = int(raw_completion) if raw_completion is not None else None
    add_tiles = int(state.get("add_image_tile_count") or 0)

    if before_completion is not None and completion is not None and completion > before_completion:
        return "completion_counter_growth"
    if before_add_tiles is not None and add_tiles < before_add_tiles:
        return "empty_slot_count_decreased"
    if sources.difference(before_sources):
        return "new_preview_source"
    if images > before_images:
        return "visible_image_count_growth"
    return ""


def _stage_accepted(
    state: dict[str, Any],
    *,
    before_images: int,
    before_sources: set[str],
    before_completion: int | None,
    before_add_tiles: int | None = None,
    target_slot_id: str | None = None,
) -> bool:
    """Return True when Makro exposes any reliable acceptance signal."""

    return bool(
        _acceptance_signal(
            state,
            before_images=before_images,
            before_sources=before_sources,
            before_completion=before_completion,
            before_add_tiles=before_add_tiles,
            target_slot_id=target_slot_id,
        )
    )


def _state_diagnostic(state: dict[str, Any], slot_id: str) -> str:
    slot = next(
        (item for item in state.get("slots") or [] if str(item.get("id")) == slot_id),
        {},
    )
    return (
        f"completion={state.get('completion_count')}/{state.get('capacity')}, "
        f"empty_slots={state.get('empty_slot_ids')}, uploading={bool(state.get('uploading'))}, "
        f"slot_plus={slot.get('has_plus')}, slot_check={slot.get('has_check')}, "
        f"slot_images={len(slot.get('image_sources') or [])}"
    )


def _wait_for_target_slot_completion(
    page: Page,
    section_path: str,
    slot_id: str,
    *,
    before_state: dict[str, Any],
    soft_timeout_ms: int = 12_000,
    uploading_timeout_ms: int = 60_000,
    accepted_stability_ms: int = 750,
) -> dict[str, Any]:
    """Wait for one submitted image using the same acceptance contract everywhere.

    Uploading is a processing hint, not the completion predicate. A slot is
    accepted when the exact role is consumed, a new preview appears, the empty
    slot count drops, or the completion counter grows. Once one such signal is
    stable briefly, a stale Uploading label can no longer hold the workflow for
    a full minute.
    """

    before_images = int(before_state.get("visible_image_count") or 0)
    before_sources = {
        str(value).strip()
        for value in before_state.get("visible_image_sources") or []
        if str(value).strip()
    }
    raw_completion = before_state.get("completion_count")
    before_completion = int(raw_completion) if raw_completion is not None else None
    before_add_tiles = int(before_state.get("add_image_tile_count") or 0)

    started = time.monotonic()
    soft_deadline = started + soft_timeout_ms / 1000.0
    uploading_deadline: float | None = None
    accepted_since: float | None = None
    accepted_signal = ""
    uploading_seen = False
    latest: dict[str, Any] = {}

    while True:
        section = find_section(page, PRODUCT_PHOTOS_SECTION)
        live_path = str((section or {}).get("path") or section_path)
        latest = _photo_state(page, live_path)
        now = time.monotonic()

        signal = _acceptance_signal(
            latest,
            before_images=before_images,
            before_sources=before_sources,
            before_completion=before_completion,
            before_add_tiles=before_add_tiles,
            target_slot_id=slot_id,
        )
        if signal:
            if signal != accepted_signal:
                accepted_signal = signal
                accepted_since = now
            elif accepted_since is None:
                accepted_since = now
            if accepted_since is not None and (
                not bool(latest.get("uploading"))
                or (now - accepted_since) * 1000.0 >= accepted_stability_ms
            ):
                latest["uploading_seen"] = uploading_seen or bool(latest.get("uploading"))
                latest["acceptance_signal"] = accepted_signal
                return latest
        else:
            accepted_signal = ""
            accepted_since = None

        if bool(latest.get("uploading")):
            uploading_seen = True
            if uploading_deadline is None:
                uploading_deadline = now + uploading_timeout_ms / 1000.0

        if uploading_deadline is not None:
            if now >= uploading_deadline:
                raise RuntimeError(
                    f"#{slot_id} 已进入 Uploading，但 {uploading_timeout_ms}ms 内没有任何可稳定确认的接受信号；"
                    + _state_diagnostic(latest, slot_id)
                )
        elif now >= soft_deadline:
            raise RuntimeError(
                f"#{slot_id} 文件已提交，但 {soft_timeout_ms}ms 内既未进入 Uploading 也没有接受信号；"
                + _state_diagnostic(latest, slot_id)
            )
        page.wait_for_timeout(100)


def _normalize_for_makro_upload(source: Path, destination_dir: Path) -> tuple[Path, dict[str, Any]]:
    """Create a predictable RGB baseline JPEG derivative for Makro upload only."""

    source = source.expanduser().resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(source) as opened:
            source_format = str(opened.format or source.suffix.lstrip(".") or "unknown").upper()
            opened.seek(0)
            image = ImageOps.exif_transpose(opened)
            image.load()
            original_size = tuple(int(value) for value in image.size)

            if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                rgba = image.convert("RGBA")
                rgb = Image.new("RGB", rgba.size, (255, 255, 255))
                rgb.paste(rgba, mask=rgba.getchannel("A"))
                image = rgb
            elif image.mode != "RGB":
                image = image.convert("RGB")
            else:
                image = image.copy()

            if max(image.size) > MAX_UPLOAD_EDGE:
                image.thumbnail((MAX_UPLOAD_EDGE, MAX_UPLOAD_EDGE), Image.Resampling.LANCZOS)

            target = destination_dir / f"{source.stem}-makro-upload.jpg"
            image.save(
                target,
                format="JPEG",
                quality=JPEG_QUALITY,
                optimize=True,
                progressive=False,
                subsampling="4:2:0",
            )
            final_size = tuple(int(value) for value in image.size)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise RuntimeError(f"图片无法标准化为 Makro JPEG：{source} ({exc})") from exc

    return target, {
        "source_format": source_format,
        "upload_format": "JPEG",
        "original_size": original_size,
        "upload_size": final_size,
        "quality": JPEG_QUALITY,
    }


class _DynamicPhotoFileTarget:
    """Upload one file exactly once into one concrete Makro thumbnail role."""

    def __init__(self, page: Page, section_path: str, slot_id: str) -> None:
        self.page = page
        self.section_path = section_path
        self.slot_id = slot_id
        self._selected = False
        self.last_acceptance: dict[str, Any] = {}
        self.upload_meta: dict[str, Any] = {}

    def _current_path(self) -> str:
        section = find_section(self.page, PRODUCT_PHOTOS_SECTION)
        path = str((section or {}).get("path") or "")
        return path or self.section_path

    def set_input_files(self, files: str | Path) -> None:
        source = Path(files).expanduser().resolve()
        current_path = self._current_path()
        before_state = _photo_state(self.page, current_path)
        if self.slot_id not in {str(value) for value in before_state.get("empty_slot_ids") or []}:
            raise RuntimeError(f"Product Photos 目标图片槽 #{self.slot_id} 已不是空槽，拒绝重复提交。")

        surface = _photo_surface(self.page, current_path)
        slot = surface.locator(f"#{self.slot_id}")
        if slot.count() != 1:
            raise RuntimeError(f"Product Photos 找不到目标图片槽 #{self.slot_id}。")
        if not slot.is_visible():
            raise RuntimeError(f"Product Photos 图片槽 #{self.slot_id} 当前不可见。")

        with tempfile.TemporaryDirectory(prefix="makro-photo-") as temp_dir:
            upload_path, upload_meta = _normalize_for_makro_upload(source, Path(temp_dir))
            self.upload_meta = upload_meta

            slot.evaluate("el => el.scrollIntoView({block: 'nearest', inline: 'nearest'})")
            slot.click(timeout=1_500, force=True)

            current_path = self._current_path()
            upload_button = _wait_for_upload_photo_button(self.page, current_path)
            if upload_button is None:
                raise RuntimeError(
                    f"点击 #{self.slot_id} 图片框后 2000ms 内没有出现可见 Upload Photo 按钮。"
                )

            shared = _raw_file_input(self.page, current_path)
            try:
                with self.page.expect_file_chooser(timeout=1_500) as chooser_info:
                    upload_button.click(timeout=1_500, force=True)
                chooser_info.value.set_files(str(upload_path))
            except PlaywrightTimeoutError:
                if shared is None:
                    deadline = time.monotonic() + 1.5
                    while time.monotonic() < deadline:
                        current_path = self._current_path()
                        shared = _raw_file_input(self.page, current_path)
                        if shared is not None:
                            break
                        self.page.wait_for_timeout(50)
                if shared is None:
                    raise RuntimeError(
                        f"#{self.slot_id} 已点击 Upload Photo，但没有 file chooser，"
                        "也找不到共享 input[type=file]。"
                    )
                shared.set_input_files(str(upload_path))

            print(
                f"GUI_EXEC_PHOTO\tSUBMITTED\t{self.slot_id}\t{source.name}\t"
                f"{upload_meta['source_format']}->JPEG\t{upload_meta['upload_size']}",
                flush=True,
            )

            self.last_acceptance = _wait_for_target_slot_completion(
                self.page,
                current_path,
                self.slot_id,
                before_state=before_state,
            )

        self._selected = True
        print(
            f"GUI_EXEC_PHOTO\tACCEPTED\t{self.slot_id}\t{source.name}\t"
            f"{self.last_acceptance.get('acceptance_signal') or 'unknown'}",
            flush=True,
        )

    def evaluate(self, _expression: str) -> int:
        return 1 if self._selected else 0


def _select_file_input(page: Page, section_path: str):
    next_slot = _next_empty_photo_slot(page, section_path)
    if next_slot is None:
        return None
    slot_id, _slot = next_slot
    return _DynamicPhotoFileTarget(page, section_path, slot_id)


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
        page.wait_for_timeout(100)
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
            result.items.append(
                {"path": str(path), "status": "slot_missing", "detail": "没有下一个逻辑空的 #thumbnail_N 图片框。"}
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
                        "acceptance_signal": target.last_acceptance.get("acceptance_signal"),
                        "upload_meta": target.upload_meta,
                    }
                )
            else:
                result.items.append(
                    {
                        "path": str(path),
                        "status": "staging_unconfirmed",
                        "slot_id": target.slot_id,
                        "detail": "Makro 没有确认目标 thumbnail 图片框已接受新图片。",
                    }
                )
        except Exception as exc:
            print(
                f"GUI_EXEC_PHOTO\tERROR\t{target.slot_id}\t{path.name}\t{exc}",
                flush=True,
            )
            result.items.append(
                {"path": str(path), "status": "upload_error", "slot_id": target.slot_id, "detail": str(exc)}
            )

    section = find_section(page, PRODUCT_PHOTOS_SECTION) or section
    section_path = str(section.get("path") or section_path)
    final_state = _photo_state(page, section_path)
    result.final_count = final_state.get("completion_count")
    if result.staged == len(resolved_paths):
        result.status = "staged"
        result.detail = f"{result.staged}/{len(resolved_paths)} 个固定 thumbnail 图片框已依次接受，等待 Save。"
    elif result.staged > 0:
        result.status = "partial_staged"
        result.detail = f"仅 {result.staged}/{len(resolved_paths)} 个固定 thumbnail 图片框确认接受。"
    else:
        result.status = "staging_unconfirmed"
        result.detail = "没有任何固定 thumbnail 图片框确认接受。"
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
        page.wait_for_timeout(150)

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
