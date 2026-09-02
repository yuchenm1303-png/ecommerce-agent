from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

VELOPACK_FEED_NAME = "releases.win-x64-stable.json"
_HTTP_TIMEOUT_SECONDS = 15.0
_STREAM_READ_BYTES = 1024 * 1024
_MAX_MANIFEST_BYTES = 1024 * 1024
_MANIFEST_SCHEMA = 1
_RETRY_DELAYS_SECONDS = (1.0, 3.0, 7.0)


class ChunkMirrorUnavailable(RuntimeError):
    """Raised when an update source has no completed chunk mirror for the target."""


class ChunkMirrorIntegrityError(RuntimeError):
    """Raised when mirrored bytes disagree with signed Velopack metadata."""


def _asset_name(asset: Any) -> str:
    return str(getattr(asset, "FileName", "") or "").strip()


def _asset_size(asset: Any) -> int:
    return int(getattr(asset, "Size", 0) or 0)


def _asset_sha256(asset: Any) -> str:
    return str(getattr(asset, "SHA256", "") or "").strip().lower()


def _source_url(source: str, *parts: str) -> str:
    base = str(source or "").strip().rstrip("/")
    if not base.startswith("https://"):
        raise ChunkMirrorUnavailable("chunk mirror requires an HTTPS source")
    encoded = "/".join(urllib.parse.quote(part, safe="") for part in parts)
    return f"{base}/{encoded}"


def _configured_proxy_handler() -> urllib.request.ProxyHandler:
    """Use only proxy state explicitly inherited by the isolated update worker."""

    proxies: dict[str, str] = {}
    all_proxy = str(os.getenv("ALL_PROXY") or os.getenv("all_proxy") or "").strip()
    https_proxy = str(os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or all_proxy).strip()
    http_proxy = str(os.getenv("HTTP_PROXY") or os.getenv("http_proxy") or all_proxy).strip()
    if https_proxy:
        proxies["https"] = https_proxy
    if http_proxy:
        proxies["http"] = http_proxy
    # An explicit empty ProxyHandler is intentional. It prevents urllib from
    # silently re-reading the Windows registry during a direct worker attempt.
    return urllib.request.ProxyHandler(proxies)


def _open(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "ListingStudio-Chunked-Update/1"},
        method="GET",
    )
    opener = urllib.request.build_opener(_configured_proxy_handler())
    return opener.open(request, timeout=_HTTP_TIMEOUT_SECONDS)


def _retry_delay(attempt: int) -> None:
    if attempt < len(_RETRY_DELAYS_SECONDS):
        time.sleep(_RETRY_DELAYS_SECONDS[attempt])


def _read_manifest(source: str, file_name: str) -> dict[str, Any]:
    url = _source_url(source, "chunks", file_name, "manifest.json")
    last_error: BaseException | None = None
    for attempt in range(len(_RETRY_DELAYS_SECONDS) + 1):
        try:
            with _open(url) as response:
                raw = response.read(_MAX_MANIFEST_BYTES + 1)
            break
        except urllib.error.HTTPError as exc:
            if int(exc.code or 0) == 404:
                raise ChunkMirrorUnavailable("chunk mirror is not ready for this package") from exc
            last_error = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
        _retry_delay(attempt)
    else:
        raise ChunkMirrorUnavailable(f"chunk mirror manifest unavailable: {last_error}") from last_error

    if len(raw) > _MAX_MANIFEST_BYTES:
        raise ChunkMirrorIntegrityError("chunk mirror manifest is unexpectedly large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ChunkMirrorIntegrityError("chunk mirror manifest is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ChunkMirrorIntegrityError("chunk mirror manifest is not an object")
    return payload


def _validated_parts(manifest: dict[str, Any], target: Any) -> list[dict[str, int | str]]:
    file_name = _asset_name(target)
    total_size = _asset_size(target)
    if int(manifest.get("schema_version") or 0) != _MANIFEST_SCHEMA:
        raise ChunkMirrorIntegrityError("unsupported chunk mirror manifest schema")
    if str(manifest.get("file_name") or "") != file_name:
        raise ChunkMirrorIntegrityError("chunk mirror file name does not match Velopack")
    if int(manifest.get("size") or 0) != total_size or total_size <= 0:
        raise ChunkMirrorIntegrityError("chunk mirror size does not match Velopack")
    manifest_sha = str(manifest.get("sha256") or "").strip().lower()
    target_sha = _asset_sha256(target)
    if manifest_sha and target_sha and manifest_sha != target_sha:
        raise ChunkMirrorIntegrityError("chunk mirror SHA256 metadata does not match Velopack")

    raw_parts = manifest.get("chunks")
    if not isinstance(raw_parts, list) or not raw_parts:
        raise ChunkMirrorIntegrityError("chunk mirror manifest has no chunks")
    parts: list[dict[str, int | str]] = []
    expected_offset = 0
    seen: set[str] = set()
    for raw in raw_parts:
        if not isinstance(raw, dict):
            raise ChunkMirrorIntegrityError("chunk mirror contains an invalid chunk")
        name = str(raw.get("name") or "").strip()
        offset = int(raw.get("offset") or 0)
        size = int(raw.get("size") or 0)
        if not name or "/" in name or "\\" in name or name in seen:
            raise ChunkMirrorIntegrityError("chunk mirror contains an unsafe chunk name")
        if offset != expected_offset or size <= 0:
            raise ChunkMirrorIntegrityError("chunk mirror offsets are not contiguous")
        seen.add(name)
        parts.append({"name": name, "offset": offset, "size": size})
        expected_offset += size
    if expected_offset != total_size:
        raise ChunkMirrorIntegrityError("chunk mirror chunks do not cover the full package")
    return parts


def _copy_response(
    response: Any,
    destination: Any,
    *,
    expected_size: int,
    digest: Any = None,
) -> int:
    written = 0
    while True:
        block = response.read(_STREAM_READ_BYTES)
        if not block:
            break
        destination.write(block)
        written += len(block)
        if written > expected_size:
            raise ChunkMirrorIntegrityError("mirrored object exceeded its declared size")
        if digest is not None:
            digest.update(block)
    if written != expected_size:
        raise ChunkMirrorIntegrityError(
            f"mirrored object size mismatch: expected={expected_size} actual={written}"
        )
    return written


def _download_exact_object(
    url: str,
    destination: Path,
    *,
    expected_size: int,
    expected_sha256: str = "",
) -> None:
    last_error: BaseException | None = None
    attempts = len(_RETRY_DELAYS_SECONDS) + 1
    for attempt in range(attempts):
        destination.unlink(missing_ok=True)
        digest = hashlib.sha256()
        try:
            with _open(url) as response, destination.open("wb") as output:
                _copy_response(
                    response,
                    output,
                    expected_size=expected_size,
                    digest=digest,
                )
            if expected_sha256 and digest.hexdigest().lower() != expected_sha256:
                raise ChunkMirrorIntegrityError("mirrored object SHA256 mismatch")
            return
        except (
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            OSError,
            ChunkMirrorIntegrityError,
        ) as exc:
            last_error = exc
            destination.unlink(missing_ok=True)
            _retry_delay(attempt)
    if isinstance(last_error, ChunkMirrorIntegrityError):
        raise last_error
    raise ChunkMirrorUnavailable(f"mirrored object unavailable after {attempts} attempts: {last_error}") from last_error


def _download_direct_asset(source: str, asset: Any, destination: Path) -> None:
    file_name = _asset_name(asset)
    expected_size = _asset_size(asset)
    if not file_name or expected_size <= 0:
        raise ChunkMirrorIntegrityError("Velopack asset metadata is incomplete")
    try:
        _download_exact_object(
            _source_url(source, file_name),
            destination,
            expected_size=expected_size,
            expected_sha256=_asset_sha256(asset),
        )
    except ChunkMirrorIntegrityError as exc:
        raise ChunkMirrorIntegrityError(f"mirrored asset integrity failed: {file_name}: {exc}") from exc


def _download_feed(source: str, destination: Path) -> None:
    url = _source_url(source, VELOPACK_FEED_NAME)
    last_error: BaseException | None = None
    for attempt in range(len(_RETRY_DELAYS_SECONDS) + 1):
        destination.unlink(missing_ok=True)
        try:
            with _open(url) as response, destination.open("wb") as output:
                shutil.copyfileobj(response, output, length=_STREAM_READ_BYTES)
            if destination.stat().st_size <= 0:
                raise ChunkMirrorIntegrityError("mirrored Velopack feed is empty")
            return
        except (
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            OSError,
            ChunkMirrorIntegrityError,
        ) as exc:
            last_error = exc
            destination.unlink(missing_ok=True)
            _retry_delay(attempt)
    raise ChunkMirrorUnavailable(f"mirrored Velopack feed unavailable: {last_error}") from last_error


def _append_verified_chunk(
    source: str,
    file_name: str,
    part: dict[str, int | str],
    output: Any,
    digest: Any,
    scratch_dir: Path,
) -> int:
    name = str(part["name"])
    expected_size = int(part["size"])
    scratch = scratch_dir / f".{name}.download"
    try:
        _download_exact_object(
            _source_url(source, "chunks", file_name, name),
            scratch,
            expected_size=expected_size,
        )
        with scratch.open("rb") as chunk:
            while True:
                block = chunk.read(_STREAM_READ_BYTES)
                if not block:
                    break
                output.write(block)
                digest.update(block)
        return expected_size
    finally:
        scratch.unlink(missing_ok=True)


def _reassemble_full_package(
    source: str,
    target: Any,
    manifest: dict[str, Any],
    destination: Path,
    progress: Callable[[int], None] | None,
) -> None:
    parts = _validated_parts(manifest, target)
    total_size = _asset_size(target)
    copied = 0
    digest = hashlib.sha256()
    file_name = _asset_name(target)
    destination.unlink(missing_ok=True)

    with tempfile.TemporaryDirectory(prefix="listing-studio-chunks-") as scratch_text:
        scratch_dir = Path(scratch_text)
        try:
            with destination.open("wb") as output:
                for part in parts:
                    copied += _append_verified_chunk(
                        source,
                        file_name,
                        part,
                        output,
                        digest,
                        scratch_dir,
                    )
                    if progress is not None:
                        progress(max(0, min(100, int(copied * 100 / total_size))))
        except Exception:
            destination.unlink(missing_ok=True)
            raise

    if destination.stat().st_size != total_size:
        destination.unlink(missing_ok=True)
        raise ChunkMirrorIntegrityError("reassembled package size does not match Velopack")
    expected_sha = _asset_sha256(target)
    if expected_sha and digest.hexdigest().lower() != expected_sha:
        destination.unlink(missing_ok=True)
        raise ChunkMirrorIntegrityError("reassembled package SHA256 does not match Velopack")
    if progress is not None:
        progress(100)


@contextmanager
def materialize_chunked_velopack_source(
    source: str,
    info: Any,
    *,
    progress: Callable[[int], None] | None = None,
) -> Iterator[Path]:
    """Materialize one mirrored release locally without owning update semantics.

    Python only performs byte transport/reassembly and strict size/SHA validation.
    The yielded directory is then consumed by Velopack's normal local-directory
    source, which remains responsible for release selection, delta/full semantics,
    staging and install verification.
    """

    target = getattr(info, "TargetFullRelease", None)
    if target is None:
        raise ChunkMirrorIntegrityError("Velopack update has no target full release")
    file_name = _asset_name(target)
    if not file_name:
        raise ChunkMirrorIntegrityError("Velopack target package has no file name")
    manifest = _read_manifest(source, file_name)

    with tempfile.TemporaryDirectory(prefix="listing-studio-chunked-source-") as temp_dir:
        root = Path(temp_dir)
        _download_feed(source, root / VELOPACK_FEED_NAME)
        for delta in list(getattr(info, "DeltasToTarget", None) or []):
            _download_direct_asset(source, delta, root / _asset_name(delta))
        _reassemble_full_package(source, target, manifest, root / file_name, progress)
        yield root


__all__ = [
    "ChunkMirrorIntegrityError",
    "ChunkMirrorUnavailable",
    "VELOPACK_FEED_NAME",
    "materialize_chunked_velopack_source",
]
