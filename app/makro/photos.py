from __future__ import annotations

import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageOps, UnidentifiedImageError
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from ..browser_visual_hud import browser_visual_hud_status, browser_visual_hud_target
from .sections import find_section, open_section_for_edit

PRODUCT_PHOTOS_SECTION = "Product Photos"
PHOTO_SURFACE_READY_TIMEOUT_MS = 8_000
PHOTO_SURFACE_STABLE_SAMPLES = 2
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


class PhotoUploadError(RuntimeError):
    """Base error for one logical Product Photos transaction."""


class PhotoPreSubmitError(PhotoUploadError):
    """The source/portal failed before any file bytes were submitted to Makro."""


class PhotoPostSubmitUncertainError(PhotoUploadError):
    """File bytes were submitted but exact slot ownership could not be proven."""


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


def _meaningful_slot_sources(slot: dict[str, Any] | None) -> set[str]:
    return {
        str(value).strip()
        for value in (slot or {}).get("image_sources") or []
        if _meaningful_image_source(value)
    }


def _slot_is_empty(slot: dict[str, Any]) -> bool:
    if bool(slot.get("has_check")):
        return False
    return bool(slot.get("has_plus"))


def _slot_from_state(state: dict[str, Any], slot_id: str) -> dict[str, Any]:
    return next(
        (
            dict(item)
            for item in state.get("slots") or []
            if str(item.get("id") or "") == slot_id
        ),
        {},
    )


def _slot_diagnostic_payload(state: dict[str, Any], slot_id: str) -> dict[str, Any]:
    slot = _slot_from_state(state, slot_id)
    return {
        "slot_id": slot_id,
        "has_plus": bool(slot.get("has_plus")),
        "has_check": bool(slot.get("has_check")),
        "image_sources": [str(value) for value in slot.get("image_sources") or []],
        "meaningful_image_sources": sorted(_meaningful_slot_sources(slot)),
        "completion_count": state.get("completion_count"),
        "capacity": state.get("capacity"),
        "uploading": bool(state.get("uploading")),
    }


def _photo_surface(page: Page, section_path: str):
    current = page.locator(section_path)
    if current.count() != 1:
        return current
    best = current
    for _ in range(4):
        if current.count() != 1:
            break
        slot_count = current.locator('[id^="thumbnail_"]').count()
        if slot_count > 0:
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
          for (const slot of Array.from(surface.querySelectorAll('[id^="thumbnail_"]'))) {
            const id = clean(slot.id);
            const match = /^thumbnail_(\d+)$/.exec(id);
            if (!match) continue;
            const index = Number(match[1]);
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
          slots.sort((left, right) => left.index - right.index);
          return slots;
        }"""
    )
    slots: list[dict[str, Any]] = []
    for raw in raw_slots or []:
        slot = dict(raw)
        slot["is_empty"] = _slot_is_empty(slot)
        slot["has_meaningful_image"] = bool(_meaningful_slot_sources(slot))
        slots.append(slot)
    return slots


def _uploading_visible(page: Page, section_path: str) -> bool:
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
        "capacity": counter[1] if counter else (len(slots) or None),
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


def _surface_slot_ids(state: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(slot.get("id") or "")
        for slot in sorted(
            (dict(slot) for slot in state.get("slots") or [] if str(slot.get("id") or "")),
            key=lambda slot: int(slot.get("index") or 0),
        )
    )


def _photo_surface_is_ready(state: dict[str, Any]) -> bool:
    if not bool(state.get("found")):
        return False
    slot_ids = _surface_slot_ids(state)
    if not slot_ids:
        return False
    raw_capacity = state.get("capacity")
    if raw_capacity is None:
        return True
    try:
        capacity = int(raw_capacity)
    except (TypeError, ValueError):
        return False
    return capacity > 0 and len(slot_ids) == capacity


def _wait_for_photo_surface_ready(
    page: Page,
    section_path: str,
    *,
    timeout_ms: int = PHOTO_SURFACE_READY_TIMEOUT_MS,
) -> tuple[str, dict[str, Any]]:
    timeout_ms = max(1, int(timeout_ms))
    deadline = time.monotonic() + timeout_ms / 1000.0
    stable_samples = 0
    stable_slot_ids: tuple[str, ...] | None = None
    latest: dict[str, Any] = {}
    live_path = section_path

    while True:
        section = find_section(page, PRODUCT_PHOTOS_SECTION)
        live_path = str((section or {}).get("path") or live_path)
        latest = _photo_state(page, live_path)
        current_slot_ids = _surface_slot_ids(latest)
        if _photo_surface_is_ready(latest):
            if current_slot_ids == stable_slot_ids:
                stable_samples += 1
            else:
                stable_slot_ids = current_slot_ids
                stable_samples = 1
            if stable_samples >= PHOTO_SURFACE_STABLE_SAMPLES:
                latest["surface_ready"] = True
                latest["surface_stable_samples"] = stable_samples
                latest["section_path"] = live_path
                return live_path, latest
        else:
            stable_samples = 0
            stable_slot_ids = None

        if time.monotonic() >= deadline:
            raise RuntimeError(
                "Product Photos 已展开，但实时图片槽结构未稳定就绪；"
                f"timeout_ms={timeout_ms}, declared_capacity={latest.get('capacity')}, "
                f"observed_slots={list(current_slot_ids)}, "
                f"completion={latest.get('completion_count')}/{latest.get('capacity')}."
            )
        page.wait_for_timeout(100)


def inspect_product_photos(page: Page) -> dict[str, Any]:
    section = find_section(page, PRODUCT_PHOTOS_SECTION)
    if section is None:
        return {"found": False, "detail": "当前页面找不到 Product Photos section。"}
    path = str(section.get("path") or "")
    if not path:
        return {"found": False, "detail": "Product Photos section 缺少稳定 DOM path。"}

    if bool(section.get("has_edit")):
        state = _photo_state(page, path)
    else:
        path, state = _wait_for_photo_surface_ready(
            page,
            path,
            timeout_ms=PHOTO_SURFACE_READY_TIMEOUT_MS,
        )
        section = find_section(page, PRODUCT_PHOTOS_SECTION) or section

    state["section_title"] = section.get("title")
    state["section_path"] = path
    state["expanded"] = not bool(section.get("has_edit"))
    return state


def _raw_file_input(page: Page, section_path: str):
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


def _next_empty_photo_slot(
    page: Page,
    section_path: str,
    *,
    consumed_slot_ids: set[str] | None = None,
) -> tuple[str, Any] | None:
    consumed = consumed_slot_ids or set()
    surface = _photo_surface(page, section_path)
    snapshots = sorted(
        _slot_snapshot(page, section_path),
        key=lambda slot: int(slot.get("index") or 0),
    )
    for snapshot in snapshots:
        slot_id = str(snapshot.get("id") or "")
        if not slot_id or slot_id in consumed:
            continue
        if not bool(snapshot.get("is_empty")):
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


def _open_photo_slot_upload_panel(
    page: Page,
    section_path: str,
    slot: Any,
    slot_id: str,
    *,
    click_timeout_ms: int = 1_500,
    panel_timeout_ms: int = 2_000,
) -> tuple[str, Any]:
    click_timeout_detail = ""
    try:
        slot.click(timeout=click_timeout_ms, force=True)
    except PlaywrightTimeoutError as exc:
        click_timeout_detail = str(exc)

    section = find_section(page, PRODUCT_PHOTOS_SECTION)
    live_path = str((section or {}).get("path") or section_path)
    upload_button = _wait_for_upload_photo_button(
        page,
        live_path,
        timeout_ms=panel_timeout_ms,
    )
    if upload_button is not None:
        if click_timeout_detail:
            print(
                f"GUI_EXEC_PHOTO\tSLOT_OPEN_RECOVERED\t{slot_id}\t"
                "Playwright click timeout occurred after dispatch; Upload Photo panel is visible.",
                flush=True,
            )
        return live_path, upload_button

    if click_timeout_detail:
        raise RuntimeError(
            f"点击 #{slot_id} 图片框时 Playwright 等待超时，且随后 {panel_timeout_ms}ms 内"
            "没有出现可见 Upload Photo 按钮；"
            f"click_timeout={click_timeout_detail}"
        )
    raise RuntimeError(
        f"点击 #{slot_id} 图片框后 {panel_timeout_ms}ms 内没有出现可见 Upload Photo 按钮。"
    )


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


def _target_slot_acceptance_signal(
    before_state: dict[str, Any],
    after_state: dict[str, Any],
    slot_id: str,
) -> tuple[str, set[str]]:
    before_slot = _slot_from_state(before_state, slot_id)
    after_slot = _slot_from_state(after_state, slot_id)
    before_sources = _meaningful_slot_sources(before_slot)
    after_sources = _meaningful_slot_sources(after_slot)
    new_sources = after_sources.difference(before_sources)

    if after_slot and bool(after_slot.get("has_check")) and not bool(before_slot.get("has_check")):
        return "target_slot_check", new_sources
    if (
        before_slot
        and bool(before_slot.get("has_plus"))
        and after_slot
        and not bool(after_slot.get("has_plus"))
    ):
        return "target_slot_consumed", new_sources
    if new_sources:
        return "target_slot_new_preview", new_sources

    before_completion_raw = before_state.get("completion_count")
    after_completion_raw = after_state.get("completion_count")
    if before_completion_raw is not None and after_completion_raw is not None:
        if int(after_completion_raw) > int(before_completion_raw):
            return "completion_counter_growth", new_sources

    return "", new_sources


def _state_diagnostic(state: dict[str, Any], slot_id: str) -> str:
    slot = _slot_from_state(state, slot_id)
    return (
        f"completion={state.get('completion_count')}/{state.get('capacity')}, "
        f"empty_slots={state.get('empty_slot_ids')}, uploading={bool(state.get('uploading'))}, "
        f"slot_plus={slot.get('has_plus')}, slot_check={slot.get('has_check')}, "
        f"slot_sources={sorted(_meaningful_slot_sources(slot))}"
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
    if not _slot_from_state(before_state, slot_id):
        raise RuntimeError(f"Product Photos 提交前状态缺少目标图片槽 #{slot_id}。")

    started = time.monotonic()
    soft_deadline = started + soft_timeout_ms / 1000.0
    uploading_deadline: float | None = None
    uploading_seen = False
    candidate_key: tuple[str, tuple[str, ...]] | None = None
    candidate_since: float | None = None
    latest: dict[str, Any] = {}
    latest_new_sources: set[str] = set()

    while True:
        section = find_section(page, PRODUCT_PHOTOS_SECTION)
        live_path = str((section or {}).get("path") or section_path)
        latest = _photo_state(page, live_path)
        now = time.monotonic()

        if bool(latest.get("uploading")):
            uploading_seen = True
            if uploading_deadline is None:
                uploading_deadline = now + uploading_timeout_ms / 1000.0

        signal, new_sources = _target_slot_acceptance_signal(before_state, latest, slot_id)
        latest_new_sources = set(new_sources)

        if signal:
            key = (signal, tuple(sorted(new_sources)))
            if key != candidate_key:
                candidate_key = key
                candidate_since = None

            if bool(latest.get("uploading")):
                candidate_since = None
            elif candidate_since is None:
                candidate_since = now

            stable_ms = 0.0 if candidate_since is None else (now - candidate_since) * 1000.0
            if stable_ms >= accepted_stability_ms:
                latest["uploading_seen"] = uploading_seen
                latest["acceptance_signal"] = signal
                latest["new_target_sources"] = sorted(new_sources)
                latest["target_slot_before"] = _slot_diagnostic_payload(before_state, slot_id)
                latest["target_slot_after"] = _slot_diagnostic_payload(latest, slot_id)
                latest["accepted_stability_ms"] = stable_ms
                return latest
        else:
            candidate_key = None
            candidate_since = None

        if uploading_deadline is not None:
            if now >= uploading_deadline:
                before_diag = _slot_diagnostic_payload(before_state, slot_id)
                after_diag = _slot_diagnostic_payload(latest, slot_id)
                raise RuntimeError(
                    f"#{slot_id} 已进入 Uploading，但 {uploading_timeout_ms}ms 内未形成稳定的目标槽接受证据；"
                    f"before={before_diag}; after={after_diag}; new_sources={sorted(latest_new_sources)}"
                )
        elif now >= soft_deadline:
            before_diag = _slot_diagnostic_payload(before_state, slot_id)
            after_diag = _slot_diagnostic_payload(latest, slot_id)
            raise RuntimeError(
                f"#{slot_id} 文件已提交，但 {soft_timeout_ms}ms 内未形成稳定的目标槽接受证据；"
                f"before={before_diag}; after={after_diag}; new_sources={sorted(latest_new_sources)}"
            )
        page.wait_for_timeout(100)


def _normalize_for_makro_upload(source: Path, destination_dir: Path) -> tuple[Path, dict[str, Any]]:
    source = source.expanduser().resolve()
    if not source.is_file():
        raise PhotoPreSubmitError(f"上传图片不存在或不是文件：{source}")
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
        raise PhotoPreSubmitError(f"图片无法标准化为 Makro JPEG：{source} ({exc})") from exc

    return target, {
        "source_format": source_format,
        "upload_format": "JPEG",
        "original_size": original_size,
        "upload_size": final_size,
        "quality": JPEG_QUALITY,
    }


class _DynamicPhotoFileTarget:
    """Upload one file exactly once into one concrete Makro thumbnail role."""

    def __init__(
        self,
        page: Page,
        section_path: str,
        slot_id: str,
        *,
        timeout_ms: int,
    ) -> None:
        self.page = page
        self.section_path = section_path
        self.slot_id = slot_id
        self.timeout_ms = max(1, int(timeout_ms))
        self._selected = False
        self.last_acceptance: dict[str, Any] = {}
        self.upload_meta: dict[str, Any] = {}
        self.submitted = False

    def _current_path(self) -> str:
        section = find_section(self.page, PRODUCT_PHOTOS_SECTION)
        path = str((section or {}).get("path") or "")
        return path or self.section_path

    def set_input_files(self, files: str | Path) -> None:
        source = Path(files).expanduser().resolve()
        self.submitted = False
        try:
            current_path = self._current_path()
            current_path, before_state = _wait_for_photo_surface_ready(
                self.page,
                current_path,
                timeout_ms=self.timeout_ms,
            )
            if self.slot_id not in {str(value) for value in before_state.get("empty_slot_ids") or []}:
                raise RuntimeError(f"Product Photos 目标图片槽 #{self.slot_id} 已不是空槽，拒绝重复提交。")

            surface = _photo_surface(self.page, current_path)
            slot = surface.locator(f"#{self.slot_id}")
            if slot.count() != 1:
                raise RuntimeError(f"Product Photos 找不到目标图片槽 #{self.slot_id}。")
            if not slot.is_visible():
                raise RuntimeError(f"Product Photos 图片槽 #{self.slot_id} 当前不可见。")

            browser_visual_hud_target(
                slot,
                "准备上传图片",
                f"正在定位 Product Photos 图片槽 {self.slot_id}，下一步会打开真实上传控件。",
                phase=2,
            )

            with tempfile.TemporaryDirectory(prefix="makro-photo-") as temp_dir:
                upload_path, upload_meta = _normalize_for_makro_upload(source, Path(temp_dir))
                self.upload_meta = upload_meta

                slot.evaluate("el => el.scrollIntoView({block: 'nearest', inline: 'nearest'})")
                current_path, upload_button = _open_photo_slot_upload_panel(
                    self.page,
                    current_path,
                    slot,
                    self.slot_id,
                )

                browser_visual_hud_target(
                    upload_button,
                    "正在打开图片选择",
                    f"已进入 {self.slot_id} 上传面板，准备点击真实 Upload Photo 按钮。",
                    phase=3,
                )

                shared = _raw_file_input(self.page, current_path)
                try:
                    with self.page.expect_file_chooser(timeout=1_500) as chooser_info:
                        upload_button.click(timeout=1_500, force=True)
                    browser_visual_hud_status(
                        self.page,
                        "正在提交商品图片",
                        f"正在把 {source.name} 提交到 {self.slot_id}，等待 Makro 接收。",
                        phase=3,
                    )
                    chooser_info.value.set_files(str(upload_path))
                    self.submitted = True
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
                    browser_visual_hud_status(
                        self.page,
                        "正在提交商品图片",
                        f"正在通过页面文件输入把 {source.name} 提交到 {self.slot_id}。",
                        phase=3,
                    )
                    shared.set_input_files(str(upload_path))
                    self.submitted = True

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
                    soft_timeout_ms=self.timeout_ms,
                    uploading_timeout_ms=max(self.timeout_ms, 60_000),
                )
        except PhotoPreSubmitError:
            raise
        except Exception as exc:
            if self.submitted:
                raise PhotoPostSubmitUncertainError(str(exc)) from exc
            raise PhotoPreSubmitError(str(exc)) from exc

        self._selected = True
        browser_visual_hud_status(
            self.page,
            "商品图片已接受",
            f"Makro 已确认 {self.slot_id} 接受 {source.name}，准备处理下一张图片。",
            phase=4,
        )
        print(
            f"GUI_EXEC_PHOTO\tACCEPTED\t{self.slot_id}\t{source.name}\t"
            f"{self.last_acceptance.get('acceptance_signal') or 'unknown'}",
            flush=True,
        )

    def evaluate(self, _expression: str) -> int:
        return 1 if self._selected else 0


def _select_file_input(
    page: Page,
    section_path: str,
    *,
    consumed_slot_ids: set[str] | None = None,
    timeout_ms: int = 8_000,
):
    next_slot = _next_empty_photo_slot(
        page,
        section_path,
        consumed_slot_ids=consumed_slot_ids,
    )
    if next_slot is None:
        return None
    slot_id, _slot = next_slot
    return _DynamicPhotoFileTarget(
        page,
        section_path,
        slot_id,
        timeout_ms=timeout_ms,
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
        page.wait_for_timeout(100)
    return latest


def upload_product_photos(
    page: Page,
    image_paths: Iterable[str | Path],
    *,
    timeout_ms: int = 8_000,
) -> PhotoUploadResult:
    """Stage independent source files while preserving slot-ownership safety.

    Invalid/missing/undecodable files fail before browser submission and are
    rejected individually. Once file bytes have been submitted to a concrete
    thumbnail role, inability to prove that role's acceptance stops the sequence;
    continuing after that boundary could assign the next product image to the wrong
    Makro slot.
    """

    timeout_ms = max(1, int(timeout_ms))
    resolved_paths: list[Path] = []
    input_rejections: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in image_paths:
        path = Path(raw).expanduser().resolve()
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        if not path.is_file():
            input_rejections.append(
                {
                    "path": str(path),
                    "status": "rejected_pre_submit",
                    "detail": f"上传图片不存在或不是文件：{path}",
                    "failure_scope": "image",
                }
            )
            continue
        resolved_paths.append(path)

    if not resolved_paths:
        if input_rejections:
            return PhotoUploadResult(
                status="no_usable_input",
                attempted=0,
                staged=0,
                items=input_rejections,
                detail="所有请求图片都在提交 Makro 前被机械校验拒绝；没有改变任何图片槽。",
            )
        return PhotoUploadResult(status="skipped", detail="没有传入 --upload-image。")

    browser_visual_hud_status(
        page,
        "正在上传商品图片",
        f"Product Photos 将依次处理 {len(resolved_paths)} 张可用候选图片。",
        phase=1,
    )

    section = find_section(page, PRODUCT_PHOTOS_SECTION)
    if section is None:
        return PhotoUploadResult(status="not_found", items=input_rejections, detail="当前页面找不到 Product Photos section。")
    open_section_for_edit(page, section)
    section = find_section(page, PRODUCT_PHOTOS_SECTION) or section
    section_path = str(section.get("path") or "")
    if not section_path:
        return PhotoUploadResult(status="not_found", items=input_rejections, detail="Product Photos section 缺少稳定 DOM path。")

    section_path, state = _wait_for_photo_surface_ready(
        page,
        section_path,
        timeout_ms=timeout_ms,
    )
    initial_count = state.get("completion_count")
    capacity = state.get("capacity")
    result = PhotoUploadResult(
        status="running",
        initial_count=initial_count,
        final_count=initial_count,
        capacity=capacity,
        accept=str(((state.get("file_inputs") or [{}])[0]).get("accept") or ""),
        multiple=False,
        items=list(input_rejections),
    )

    consumed_slots: set[str] = set()
    pre_submit_rejected = len(input_rejections)
    post_submit_uncertain = False
    operational_stop = False
    for path in resolved_paths:
        section = find_section(page, PRODUCT_PHOTOS_SECTION) or section
        section_path = str(section.get("path") or section_path)
        try:
            section_path, _ready_state = _wait_for_photo_surface_ready(
                page,
                section_path,
                timeout_ms=timeout_ms,
            )
            target = _select_file_input(
                page,
                section_path,
                consumed_slot_ids=consumed_slots,
                timeout_ms=timeout_ms,
            )
        except Exception as exc:
            result.items.append(
                {
                    "path": str(path),
                    "status": "gallery_unavailable",
                    "detail": str(exc),
                    "failure_scope": "photo_section",
                }
            )
            operational_stop = True
            break

        if target is None:
            result.items.append(
                {
                    "path": str(path),
                    "status": "slot_missing",
                    "detail": "当前实时图片槽中没有下一个未消费的空 thumbnail 图片框。",
                    "consumed_slots": sorted(consumed_slots),
                    "failure_scope": "photo_section",
                }
            )
            operational_stop = True
            break

        result.attempted += 1
        try:
            target.set_input_files(str(path))
        except PhotoPreSubmitError as exc:
            pre_submit_rejected += 1
            print(
                f"GUI_EXEC_PHOTO\tREJECTED_PRE_SUBMIT\t{target.slot_id}\t{path.name}\t{exc}",
                flush=True,
            )
            result.items.append(
                {
                    "path": str(path),
                    "status": "rejected_pre_submit",
                    "slot_id": target.slot_id,
                    "detail": str(exc),
                    "consumed_slots": sorted(consumed_slots),
                    "timeout_ms": timeout_ms,
                    "failure_scope": "image",
                }
            )
            continue
        except PhotoPostSubmitUncertainError as exc:
            post_submit_uncertain = True
            try:
                live_state = _photo_state(page, section_path)
                after_error = _slot_diagnostic_payload(live_state, target.slot_id)
            except Exception:
                after_error = {}
            print(
                f"GUI_EXEC_PHOTO\tPOST_SUBMIT_UNCERTAIN\t{target.slot_id}\t{path.name}\t{exc}",
                flush=True,
            )
            result.items.append(
                {
                    "path": str(path),
                    "status": "post_submit_uncertain",
                    "slot_id": target.slot_id,
                    "detail": str(exc),
                    "target_slot_after_error": after_error,
                    "consumed_slots": sorted(consumed_slots),
                    "timeout_ms": timeout_ms,
                    "failure_scope": "slot_transaction",
                }
            )
            break

        settled = dict(target.last_acceptance)
        consumed_slots.add(target.slot_id)
        result.staged += 1
        result.items.append(
            {
                "path": str(path),
                "status": "staged",
                "slot_id": target.slot_id,
                "acceptance_signal": settled.get("acceptance_signal"),
                "new_target_sources": settled.get("new_target_sources") or [],
                "target_slot_before": settled.get("target_slot_before") or {},
                "target_slot_after": settled.get("target_slot_after") or {},
                "uploading_seen": bool(settled.get("uploading_seen")),
                "consumed_slots": sorted(consumed_slots),
                "upload_meta": target.upload_meta,
                "timeout_ms": timeout_ms,
            }
        )

    section = find_section(page, PRODUCT_PHOTOS_SECTION) or section
    section_path = str(section.get("path") or section_path)
    try:
        final_state = _photo_state(page, section_path)
        result.final_count = final_state.get("completion_count")
    except Exception:
        result.final_count = result.initial_count

    eligible_after_rejection = max(0, len(resolved_paths) - (pre_submit_rejected - len(input_rejections)))
    if post_submit_uncertain:
        result.status = "post_submit_uncertain"
        result.detail = (
            f"已确认 staged={result.staged}，但后续文件已提交而目标槽状态无法证明；"
            "已停止，禁止继续分配下一张图片。"
        )
    elif operational_stop:
        result.status = "partial_staged" if result.staged else "staging_unconfirmed"
        result.detail = (
            f"已确认 staged={result.staged}；Product Photos 运行表面随后不可安全操作，已停止。"
        )
    elif result.staged == eligible_after_rejection:
        result.status = "staged"
        result.detail = (
            f"{result.staged}/{eligible_after_rejection} 张可提交图片已事务确认；"
            f"另有 {pre_submit_rejected} 张在提交前被隔离，等待 Save。"
        )
    elif result.staged > 0:
        result.status = "partial_staged"
        result.detail = (
            f"仅 {result.staged}/{eligible_after_rejection} 个可提交图片槽事务确认；"
            "未继续冒险上传。"
        )
    else:
        result.status = "staging_unconfirmed"
        result.detail = "没有任何实时 thumbnail 图片槽形成可证明的本次上传接受证据。"
    return result


def verify_persisted_photo_count(
    page: Page,
    *,
    initial_count: int | None,
    expected_added: int,
    timeout_ms: int = 10_000,
) -> dict[str, Any]:
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


__all__ = [
    "PRODUCT_PHOTOS_SECTION",
    "PhotoPostSubmitUncertainError",
    "PhotoPreSubmitError",
    "PhotoUploadError",
    "PhotoUploadResult",
    "inspect_product_photos",
    "parse_completion_counter",
    "upload_product_photos",
    "verify_persisted_photo_count",
]
