from __future__ import annotations

import hashlib
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterable, Protocol

from PIL import Image, ImageOps, UnidentifiedImageError

from .providers.usage_telemetry import usage_request_context
from .semantic_grounding import GroundedSource, IMAGE_KIND, TEXT_KIND


IMAGE_EVIDENCE_CONTRACT_VERSION = 2
IMAGE_EVIDENCE_CACHE_VERSION = 2
_IMAGE_BATCH_MAX_ATTEMPTS = 3
_IMAGE_BATCH_BACKOFF_SECONDS = (0.35, 0.85)
_AI_IMAGE_INPUT_VERSION = 1
_AI_IMAGE_MAX_LONG_EDGE = 2048
_AI_IMAGE_MAX_PIXEL_AREA = 4_000_000
_AI_IMAGE_MAX_BYTES = 4 * 1024 * 1024
_AI_IMAGE_JPEG_QUALITY = 88


class ImageEvidenceError(ValueError):
    pass


class JSONTaskProvider(Protocol):
    name: str

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass(slots=True, frozen=True)
class ImageFactObservation:
    name: str
    scope: str
    value: str
    qualifier: str = ""
    evidence_text: str = ""

    @classmethod
    def from_mapping(cls, payload: dict[str, Any], *, where: str) -> "ImageFactObservation":
        name = str(payload.get("name") or "").strip()
        scope = str(payload.get("scope") or "unclear").strip() or "unclear"
        value = str(payload.get("value") or "").strip()
        evidence_text = str(payload.get("evidence_text") or "").strip()
        if not name or not value or not evidence_text:
            raise ImageEvidenceError(f"{where} requires name, value and evidence_text")
        return cls(
            name=name,
            scope=scope,
            value=value,
            qualifier=str(payload.get("qualifier") or "").strip(),
            evidence_text=evidence_text,
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "scope": self.scope,
            "value": self.value,
            "qualifier": self.qualifier,
            "evidence_text": self.evidence_text,
        }


@dataclass(slots=True, frozen=True)
class ImageObservation:
    image_id: str
    origin: str
    sha256: str
    visible_text: str
    facts: tuple[ImageFactObservation, ...]
    notes: str = ""

    @classmethod
    def from_mapping(
        cls,
        payload: dict[str, Any],
        *,
        source: GroundedSource,
    ) -> "ImageObservation":
        raw_facts = payload.get("facts") or []
        if not isinstance(raw_facts, list):
            raise ImageEvidenceError(f"{source.source_id}.facts must be an array")
        return cls(
            image_id=source.source_id,
            origin=source.origin,
            sha256=source.sha256,
            visible_text=str(payload.get("visible_text") or "").strip(),
            facts=tuple(
                ImageFactObservation.from_mapping(
                    item,
                    where=f"{source.source_id}.facts[{index}]",
                )
                for index, item in enumerate(raw_facts, start=1)
                if isinstance(item, dict)
            ),
            notes=str(payload.get("notes") or "").strip(),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "image_id": self.image_id,
            "origin": self.origin,
            "sha256": self.sha256,
            "visible_text": self.visible_text,
            "facts": [fact.as_dict() for fact in self.facts],
            "notes": self.notes,
        }


_FACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string", "minLength": 1},
        "scope": {"type": "string", "minLength": 1},
        "value": {"type": "string", "minLength": 1},
        "qualifier": {"type": "string"},
        "evidence_text": {"type": "string", "minLength": 1},
    },
    "required": ["name", "scope", "value", "qualifier", "evidence_text"],
}


def _batch_schema(images: list[GroundedSource]) -> dict[str, Any]:
    properties = {
        source.source_id: {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "visible_text": {"type": "string"},
                "facts": {"type": "array", "maxItems": 80, "items": _FACT_SCHEMA},
                "notes": {"type": "string"},
            },
            "required": ["visible_text", "facts", "notes"],
        }
        for source in images
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "images": {
                "type": "object",
                "additionalProperties": False,
                "properties": properties,
                "required": list(properties),
            },
            "summary": {"type": "string"},
        },
        "required": ["images", "summary"],
    }


IMAGE_SYSTEM_INSTRUCTION = (
    "You extract evidence independently from each supplied product image. Read visible text and visual relationships, "
    "but do not map anything to marketplace fields and do not compare different images. Return one keyed observation "
    "for every image_id and JSON only."
)

IMAGE_RULES = [
    "Inspect every supplied image independently and return its image_id exactly once.",
    "Transcribe important visible product text faithfully; preserve numbers, units and language.",
    "Extract explicit product facts and the visual scope that the image establishes, such as packaging, product_body, mount, camera/lens, included_items, documentation or unclear.",
    "Use scope=unclear when the image does not establish what an observed value belongs to.",
    "Describe arrows, labels, nearby headings or depicted objects in evidence_text so a later text-only stage can determine scope.",
    "Do not infer negative facts from absence and do not use general product knowledge.",
    "Do not reconcile disagreements across images; the later Product Profile stage does that.",
    "Do not answer marketplace fields, browse the web or generate marketing copy.",
]


def build_image_evidence_request(images: Iterable[GroundedSource]) -> dict[str, Any]:
    sources = list(images)
    if not sources or any(source.kind != IMAGE_KIND for source in sources):
        raise ImageEvidenceError("image evidence request requires image sources only")
    return {
        "task": "extract_independent_product_image_evidence",
        "system_instruction": IMAGE_SYSTEM_INSTRUCTION,
        "prompt_instruction": (
            "For each image_id, return visible_text plus compact facts with scope and evidence_text. "
            "Treat each image independently and do not omit an image even when it has no readable fact."
        ),
        "target_fields": [],
        "image_ids": [source.source_id for source in sources],
        "rules": list(IMAGE_RULES),
        "grounded_sources": [source.as_request_dict() for source in sources],
        "json_contract": _batch_schema(sources),
        "strict_json_schema": True,
    }


def image_evidence_contract_digest() -> str:
    raw = json.dumps(
        {
            "version": IMAGE_EVIDENCE_CONTRACT_VERSION,
            "system": IMAGE_SYSTEM_INSTRUCTION,
            "rules": IMAGE_RULES,
            "schema": "exact keyed per-image observations",
            "model_image_input": {
                "version": _AI_IMAGE_INPUT_VERSION,
                "max_long_edge": _AI_IMAGE_MAX_LONG_EDGE,
                "max_pixel_area": _AI_IMAGE_MAX_PIXEL_AREA,
                "max_bytes": _AI_IMAGE_MAX_BYTES,
                "jpeg_quality": _AI_IMAGE_JPEG_QUALITY,
            },
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_key(provider: JSONTaskProvider, namespace: str, source: GroundedSource) -> str:
    payload = {
        "cache_version": IMAGE_EVIDENCE_CACHE_VERSION,
        "contract_sha256": image_evidence_contract_digest(),
        "provider": provider.name,
        "namespace": namespace,
        "image_sha256": source.sha256,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_path(root: Path, provider: JSONTaskProvider, namespace: str, source: GroundedSource) -> Path:
    return root / f"image-observation-{_cache_key(provider, namespace, source)}.json"


def _model_input_source(source: GroundedSource, output_dir: Path) -> GroundedSource:
    """Return a bounded transport image while preserving the original evidence identity.

    Raw supplier images stay untouched and keep their original source_id/origin/sha256.
    Only the local byte payload sent to the multimodal provider is resized/re-encoded
    when it exceeds a conservative pixel or byte budget. This keeps large storefront
    assets from turning one batch into an unbounded model request without changing
    citation provenance or the later Listing Photos selection.
    """

    path = Path(source.image_path)
    if not path.is_file():
        return source

    try:
        byte_count = path.stat().st_size
        with Image.open(path) as opened:
            frame = ImageOps.exif_transpose(opened)
            width, height = int(frame.width), int(frame.height)
            if width <= 0 or height <= 0:
                return source
            needs_prepare = bool(
                max(width, height) > _AI_IMAGE_MAX_LONG_EDGE
                or width * height > _AI_IMAGE_MAX_PIXEL_AREA
                or byte_count > _AI_IMAGE_MAX_BYTES
            )
            if not needs_prepare:
                return source

            frame.load()
            scale = min(
                1.0,
                _AI_IMAGE_MAX_LONG_EDGE / max(width, height),
                math.sqrt(_AI_IMAGE_MAX_PIXEL_AREA / (width * height)),
            )
            target_size = (
                max(1, int(round(width * scale))),
                max(1, int(round(height * scale))),
            )
            if target_size != frame.size:
                resampling = getattr(Image, "Resampling", Image).LANCZOS
                frame = frame.resize(target_size, resampling)

            if "A" in frame.getbands():
                rgba = frame.convert("RGBA")
                flattened = Image.new("RGB", rgba.size, "white")
                flattened.paste(rgba, mask=rgba.getchannel("A"))
                frame = flattened
            else:
                frame = frame.convert("RGB")

            output_dir.mkdir(parents=True, exist_ok=True)
            target = output_dir / f"{source.sha256[:24] or hashlib.sha256(str(path).encode()).hexdigest()[:24]}.jpg"
            quality = _AI_IMAGE_JPEG_QUALITY
            frame.save(target, format="JPEG", quality=quality, optimize=True)

            while target.stat().st_size > _AI_IMAGE_MAX_BYTES and max(frame.size) > 768:
                next_size = (
                    max(1, int(round(frame.width * 0.82))),
                    max(1, int(round(frame.height * 0.82))),
                )
                resampling = getattr(Image, "Resampling", Image).LANCZOS
                frame = frame.resize(next_size, resampling)
                quality = max(68, quality - 5)
                frame.save(target, format="JPEG", quality=quality, optimize=True)
    except (UnidentifiedImageError, OSError, ValueError):
        return source

    return GroundedSource(
        source_id=source.source_id,
        source_type=source.source_type,
        kind=source.kind,
        origin=source.origin,
        content=source.content,
        image_path=str(target),
        sha256=source.sha256,
    )


@dataclass(slots=True)
class _BatchResult:
    index: int
    images: tuple[GroundedSource, ...]
    observations: list[ImageObservation]
    model_calls: int
    warning: str = ""
    error: BaseException | None = None


def _exception_text(exc: BaseException) -> str:
    parts: list[str] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts.append(str(current))
        cause = getattr(current, "__cause__", None)
        context = getattr(current, "__context__", None)
        if isinstance(cause, BaseException):
            current = cause
        elif isinstance(context, BaseException):
            current = context
        else:
            current = None
    return " ".join(parts).casefold()


def _is_retryable_image_batch_error(exc: BaseException) -> bool:
    """Retry only model-output/structured-response failures, never account/config errors."""

    if isinstance(exc, ImageEvidenceError):
        return True

    text = _exception_text(exc)
    if "openai-compatible api 未返回可解析的 json object" in text:
        return True
    if "openai-compatible api 返回空文本" in text:
        return True

    return (
        "response_format" in text
        and (
            "model output became abnormal" in text
            or "partial output may be incomplete or invalid json" in text
        )
    )


def _is_wall_clock_timeout(exc: BaseException) -> bool:
    text = _exception_text(exc)
    return "whole-request wall-clock deadline exceeded" in text


def _is_batch_local_degradable_error(exc: BaseException) -> bool:
    return bool(_is_wall_clock_timeout(exc) or _is_retryable_image_batch_error(exc))


def _run_batch(provider: JSONTaskProvider, index: int, images: list[GroundedSource]) -> _BatchResult:
    batch_images = tuple(images)
    try:
        request = build_image_evidence_request(images)
    except Exception as exc:
        return _BatchResult(
            index=index,
            images=batch_images,
            observations=[],
            model_calls=0,
            warning=f"image evidence batch {index} failed before model call: {exc}",
            error=exc,
        )

    model_calls = 0
    last_error: BaseException | None = None
    with usage_request_context(
        task=str(request.get("task") or ""),
        provider=str(getattr(provider, "name", "")),
        model=str(getattr(provider, "model", "")),
    ):
        for attempt in range(1, _IMAGE_BATCH_MAX_ATTEMPTS + 1):
            try:
                model_calls += 1
                raw = provider.extract_json(request)
                keyed = raw.get("images") if isinstance(raw, dict) else None
                if not isinstance(keyed, dict) or set(keyed) != {source.source_id for source in images}:
                    raise ImageEvidenceError("image observation response did not contain the exact image_id partition")
                observations = [
                    ImageObservation.from_mapping(keyed[source.source_id], source=source)
                    for source in images
                    if isinstance(keyed.get(source.source_id), dict)
                ]
                if len(observations) != len(images):
                    raise ImageEvidenceError("image observation response omitted an image")
                return _BatchResult(
                    index=index,
                    images=batch_images,
                    observations=observations,
                    model_calls=model_calls,
                )
            except Exception as exc:
                last_error = exc
                if attempt >= _IMAGE_BATCH_MAX_ATTEMPTS or not _is_retryable_image_batch_error(exc):
                    break
                delay = _IMAGE_BATCH_BACKOFF_SECONDS[min(attempt - 1, len(_IMAGE_BATCH_BACKOFF_SECONDS) - 1)]
                time.sleep(delay)

    attempts = model_calls
    return _BatchResult(
        index=index,
        images=batch_images,
        observations=[],
        model_calls=model_calls,
        warning=f"image evidence batch {index} failed after {attempts} model attempt(s): {last_error}",
        error=last_error,
    )


@dataclass(slots=True)
class ImageEvidenceRunResult:
    observations: list[ImageObservation]
    model_calls: int
    cache_hits: int
    batch_count: int
    failed_batches: int
    elapsed_seconds: float
    warnings: tuple[str, ...] = ()
    failed_images: tuple[str, ...] = ()


def _write_cached_observation(
    observation: ImageObservation,
    *,
    source: GroundedSource,
    cache_root: Path | None,
    provider: JSONTaskProvider,
    cache_namespace: str,
) -> None:
    if cache_root is None:
        return
    path = _cache_path(cache_root, provider, cache_namespace, source)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(observation.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(path)


def run_image_evidence(
    provider: JSONTaskProvider,
    sources: Iterable[GroundedSource],
    *,
    batch_size: int = 3,
    concurrency: int = 4,
    cache_dir: str | Path | None = None,
    cache_namespace: str = "",
) -> ImageEvidenceRunResult:
    """Resolve image evidence with bounded transport payloads and per-batch recovery.

    Successful observations become durable cache entries immediately, so another
    concurrent batch cannot invalidate already-paid work. A whole-request wall-clock
    timeout on a multi-image batch is recovered once by splitting that batch into
    independent single-image requests. Remaining timeout/structured-output failures
    are isolated to those images; the pipeline continues when any usable image or
    text evidence remains. Unknown provider/account/configuration failures still fail
    closed rather than being silently downgraded.
    """

    if not 1 <= int(batch_size) <= 8:
        raise ValueError("image batch_size must be in 1..8")
    if not 1 <= int(concurrency) <= 12:
        raise ValueError("image concurrency must be in 1..12")

    started = time.monotonic()
    all_sources = list(sources)
    images = [source for source in all_sources if source.kind == IMAGE_KIND]
    text_evidence_available = any(
        source.kind == TEXT_KIND and bool(source.content.strip())
        for source in all_sources
    )
    source_by_id = {source.source_id: source for source in images}
    cache_root = Path(cache_dir) if cache_dir is not None else None
    observations: dict[str, ImageObservation] = {}
    pending: list[GroundedSource] = []
    cache_hits = 0

    for source in images:
        path = _cache_path(cache_root, provider, cache_namespace, source) if cache_root is not None else None
        if path is not None and path.is_file():
            try:
                cached = ImageObservation.from_mapping(
                    json.loads(path.read_text(encoding="utf-8")),
                    source=source,
                )
                observations[source.source_id] = cached
                cache_hits += 1
                continue
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
        pending.append(source)

    if not pending:
        ordered_cached = [observations[source.source_id] for source in images if source.source_id in observations]
        return ImageEvidenceRunResult(
            observations=ordered_cached,
            model_calls=0,
            cache_hits=cache_hits,
            batch_count=0,
            failed_batches=0,
            elapsed_seconds=time.monotonic() - started,
        )

    all_runs: list[_BatchResult] = []
    final_failures: list[_BatchResult] = []
    temporary = TemporaryDirectory(prefix="ecommerce-image-evidence-")
    try:
        prepared_root = Path(temporary.name)
        prepared_pending = [_model_input_source(source, prepared_root) for source in pending]
        initial_batches = [
            prepared_pending[index : index + int(batch_size)]
            for index in range(0, len(prepared_pending), int(batch_size))
        ]

        def execute(batch_specs: list[tuple[int, list[GroundedSource]]]) -> list[_BatchResult]:
            completed: list[_BatchResult] = []
            if not batch_specs:
                return completed
            with ThreadPoolExecutor(
                max_workers=min(int(concurrency), len(batch_specs)),
                thread_name_prefix="image-evidence",
            ) as executor:
                futures = {
                    executor.submit(_run_batch, provider, index, batch): index
                    for index, batch in batch_specs
                }
                for future in as_completed(futures):
                    run = future.result()
                    completed.append(run)
                    all_runs.append(run)
                    if run.warning:
                        continue
                    for observation in run.observations:
                        observations[observation.image_id] = observation
                        original_source = source_by_id[observation.image_id]
                        _write_cached_observation(
                            observation,
                            source=original_source,
                            cache_root=cache_root,
                            provider=provider,
                            cache_namespace=cache_namespace,
                        )
            return completed

        initial_specs = [
            (index, batch)
            for index, batch in enumerate(initial_batches, start=1)
        ]
        initial_runs = execute(initial_specs)

        fatal_initial = [
            run
            for run in initial_runs
            if run.warning
            and run.error is not None
            and not _is_batch_local_degradable_error(run.error)
        ]
        if fatal_initial:
            raise ImageEvidenceError("; ".join(run.warning for run in fatal_initial))

        split_runs = [
            run
            for run in initial_runs
            if run.warning
            and run.error is not None
            and _is_wall_clock_timeout(run.error)
            and len(run.images) > 1
        ]
        final_failures.extend(
            run
            for run in initial_runs
            if run.warning and run not in split_runs
        )

        next_index = len(initial_specs) + 1
        recovery_specs: list[tuple[int, list[GroundedSource]]] = []
        for run in split_runs:
            for source in run.images:
                recovery_specs.append((next_index, [source]))
                next_index += 1

        recovery_runs = execute(recovery_specs)
        fatal_recovery = [
            run
            for run in recovery_runs
            if run.warning
            and run.error is not None
            and not _is_batch_local_degradable_error(run.error)
        ]
        if fatal_recovery:
            raise ImageEvidenceError("; ".join(run.warning for run in fatal_recovery))
        final_failures.extend(run for run in recovery_runs if run.warning)
    finally:
        temporary.cleanup()

    ordered = [
        observations[source.source_id]
        for source in images
        if source.source_id in observations
    ]
    warnings = tuple(run.warning for run in final_failures if run.warning)
    failed_images = tuple(
        source.source_id
        for run in final_failures
        for source in run.images
        if source.source_id not in observations
    )

    if images and not ordered and warnings and not text_evidence_available:
        raise ImageEvidenceError(
            "all image evidence failed and no text evidence is available: "
            + "; ".join(warnings)
        )

    return ImageEvidenceRunResult(
        observations=ordered,
        model_calls=sum(run.model_calls for run in all_runs),
        cache_hits=cache_hits,
        batch_count=len(all_runs),
        failed_batches=len(final_failures),
        elapsed_seconds=time.monotonic() - started,
        warnings=warnings,
        failed_images=failed_images,
    )


def write_image_observations(observations: Iterable[ImageObservation], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps([item.as_dict() for item in observations], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target
