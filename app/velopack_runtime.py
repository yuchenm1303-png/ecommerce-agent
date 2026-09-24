from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import velopack

from app.chunked_update_transport import ChunkMirrorUnavailable, materialize_chunked_velopack_source

GITHUB_REPOSITORY_URL = "https://github.com/yuchenm1303-png/ecommerce-agent"
PORTAL_RELEASE_URL = "https://nfzkphjbelyltrzgkdwt.supabase.co/functions/v1/portal-release"
UPDATE_SOURCE_ENV = "ECOMMERCE_AGENT_UPDATE_SOURCE"
_UPDATE_WORKER_MODE_ENV = "ECOMMERCE_AGENT_UPDATE_WORKER_MODE"
_UPDATE_CHECK_SOURCE_ENV = "ECOMMERCE_AGENT_UPDATE_CHECK_SOURCE"
_UPDATE_CHECK_RESULT_ENV = "ECOMMERCE_AGENT_UPDATE_CHECK_RESULT_PATH"
_UPDATE_CHECK_PROXY_ENV = "ECOMMERCE_AGENT_UPDATE_CHECK_USE_SYSTEM_PROXY"
_UPDATE_CHECK_WORKER_ARG = "--internal-velopack-check"
_UPDATE_DOWNLOAD_TARGET_ENV = "ECOMMERCE_AGENT_UPDATE_DOWNLOAD_TARGET"
_UPDATE_DOWNLOAD_PROGRESS_ENV = "ECOMMERCE_AGENT_UPDATE_DOWNLOAD_PROGRESS_PATH"
_UPDATE_CHECK_TIMEOUT_SECONDS = 12.0
_UPDATE_SOURCE_SLICE_SECONDS = 6.0
_PORTAL_DISCOVERY_TIMEOUT_SECONDS = 3.0
_UPDATE_DOWNLOAD_CHECK_TIMEOUT_SECONDS = 12.0
_UPDATE_DOWNLOAD_IDLE_TIMEOUT_SECONDS = 45.0
_UPDATE_DOWNLOAD_POLL_SECONDS = 0.2
_DOWNLOAD_RETRY_DELAYS_SECONDS = (2.0, 5.0)
_TRANSIENT_DOWNLOAD_MARKERS = (
    "peer disconnected",
    "connection reset",
    "connection aborted",
    "connection closed",
    "connection refused",
    "network is unreachable",
    "temporarily unavailable",
    "timed out",
    "timeout",
    "http/2",
    "http2",
    "dns",
    "name resolution",
)
_ASSET_FIELDS = (
    "PackageId",
    "Version",
    "Type",
    "FileName",
    "SHA1",
    "SHA256",
    "Size",
    "NotesMarkdown",
    "NotesHtml",
)
_PROXY_ENV_NAMES = (
    "ALL_PROXY",
    "all_proxy",
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "NO_PROXY",
    "no_proxy",
)


class UpdateCheckTimeoutError(RuntimeError):
    """Raised after an isolated Velopack discovery exceeds its hard deadline."""


class UpdateDownloadTimeoutError(RuntimeError):
    """Raised when an isolated update download stops making progress."""


class UpdateSourceError(RuntimeError):
    """Raised only after every healthy update route has been exhausted."""


@dataclass(frozen=True)
class StableUpdateRoute:
    """Server-derived Stable version plus ordered Velopack transport sources.

    The version is still derived from the public GitHub Stable release by the
    portal service. Sources are merely transport routes for the same Velopack
    feed, so failover never creates a second release authority.
    """

    advertised_version: str
    sources: tuple[str, ...]
    prefer_system_proxy: bool = True
    control_plane_error: str = ""


def embedded_application_version() -> str:
    candidates: list[Path] = []
    if bool(getattr(sys, "frozen", False)):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "packaging" / "VERSION")
        candidates.append(Path(sys.executable).resolve().parent / "_internal" / "packaging" / "VERSION")
    else:
        candidates.append(Path(__file__).resolve().parents[1] / "packaging" / "VERSION")
    for path in candidates:
        try:
            value = path.read_text(encoding="utf-8").strip().lstrip("v")
        except OSError:
            continue
        if value:
            return value
    return "0.0.0"


def velopack_root() -> Path | None:
    if os.name != "nt" or not bool(getattr(sys, "frozen", False)):
        return None
    try:
        current = Path(sys.executable).resolve().parent
    except OSError:
        return None
    root = current.parent
    if current.name.casefold() != "current":
        return None
    if not (root / "Update.exe").is_file():
        return None
    return root


def is_velopack_managed() -> bool:
    return velopack_root() is not None


def _create_raw_update_manager(source: str | None = None) -> velopack.UpdateManager:
    override = str(source or os.getenv(UPDATE_SOURCE_ENV, "") or "").strip()
    if override and override != GITHUB_REPOSITORY_URL:
        return velopack.UpdateManager(override)
    return velopack.UpdateManager(velopack.GithubSource(GITHUB_REPOSITORY_URL, None, False))


def _is_transient_download_error(exc: BaseException) -> bool:
    message = f"{type(exc).__name__}: {exc}".casefold()
    return any(marker in message for marker in _TRANSIENT_DOWNLOAD_MARKERS)


class _ResilientUpdateManager:
    """Retry transient transport failures while Velopack owns package semantics."""

    def __init__(self, manager: velopack.UpdateManager) -> None:
        self._manager = manager

    def __getattr__(self, name: str) -> Any:
        return getattr(self._manager, name)

    def download_updates(self, info: Any, progress: Any = None) -> Any:
        attempts = 1 + len(_DOWNLOAD_RETRY_DELAYS_SECONDS)
        for attempt in range(1, attempts + 1):
            try:
                if progress is None:
                    return self._manager.download_updates(info)
                return self._manager.download_updates(info, progress)
            except Exception as exc:
                transient = _is_transient_download_error(exc)
                if attempt >= attempts or not transient:
                    if transient:
                        raise RuntimeError(
                            f"update network transport failed after {attempt} attempts: "
                            f"{type(exc).__name__}: {exc}"
                        ) from exc
                    raise
                time.sleep(_DOWNLOAD_RETRY_DELAYS_SECONDS[attempt - 1])
        raise RuntimeError("unreachable Velopack download retry state")


def create_update_manager(source: str | None = None) -> Any:
    return _ResilientUpdateManager(_create_raw_update_manager(source))


def _version_key(value: str) -> tuple[int, int, int] | None:
    parts = str(value or "").strip().lstrip("v").split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        return None
    return int(parts[0]), int(parts[1]), int(parts[2])


def _safe_https_source(value: object) -> str:
    source = str(value or "").strip().rstrip("/")
    return source if source.startswith("https://") else ""


def _system_proxy_available() -> bool:
    try:
        proxies = urllib.request.getproxies()
    except Exception:
        return any(os.getenv(name) for name in _PROXY_ENV_NAMES)
    return bool(proxies.get("https") or proxies.get("http") or os.getenv("ALL_PROXY") or os.getenv("all_proxy"))


def _portal_request(*, use_system_proxy: bool) -> dict[str, Any]:
    request = urllib.request.Request(
        PORTAL_RELEASE_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": f"ListingStudio/{embedded_application_version()}",
        },
        method="GET",
    )
    if use_system_proxy:
        opener = urllib.request.build_opener()
    else:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=_PORTAL_DISCOVERY_TIMEOUT_SECONDS) as response:
        raw = response.read().decode("utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("portal release payload is not an object")
    return payload


def resolve_stable_update_route() -> StableUpdateRoute:
    """Resolve Stable through the resilient control plane, with GitHub fallback.

    The portal is a validated/cached projection of the GitHub Stable release and
    may advertise a CDN mirror of the exact same Velopack feed. If the control
    plane itself is unavailable, direct GitHub remains a final transport route.
    """

    override = str(os.getenv(UPDATE_SOURCE_ENV, "") or "").strip().rstrip("/")
    if override:
        return StableUpdateRoute("", (override,), _system_proxy_available())

    attempts: list[tuple[bool, str]] = []
    if _system_proxy_available():
        attempts.append((True, "system-proxy"))
    attempts.append((False, "direct"))
    errors: list[str] = []

    for use_proxy, label in attempts:
        try:
            payload = _portal_request(use_system_proxy=use_proxy)
            if str(payload.get("channel") or "").strip().casefold() != "stable":
                raise ValueError("portal release channel is not stable")
            version = str(payload.get("version") or "").strip().lstrip("v")
            if _version_key(version) is None:
                raise ValueError("portal release version is invalid")

            sources: list[str] = []
            raw_sources = payload.get("updateSources")
            if isinstance(raw_sources, list):
                for value in raw_sources:
                    source = _safe_https_source(value)
                    if source and source not in sources:
                        sources.append(source)
            legacy_source = _safe_https_source(payload.get("updateBaseUrl"))
            if legacy_source and legacy_source not in sources:
                sources.append(legacy_source)
            if GITHUB_REPOSITORY_URL not in sources:
                sources.append(GITHUB_REPOSITORY_URL)
            return StableUpdateRoute(version, tuple(sources), use_proxy)
        except Exception as exc:
            errors.append(f"{label}:{type(exc).__name__}:{exc}")

    return StableUpdateRoute(
        "",
        (GITHUB_REPOSITORY_URL,),
        _system_proxy_available(),
        "; ".join(errors),
    )


def resolve_stable_update_source() -> tuple[str, str]:
    """Compatibility view of the first route for older callers/tests."""

    route = resolve_stable_update_route()
    return route.advertised_version, route.sources[0]


def _asset_to_payload(asset: Any) -> dict[str, Any]:
    payload = {field: getattr(asset, field) for field in _ASSET_FIELDS}
    payload["Size"] = int(payload.get("Size") or 0)
    return payload


def _asset_from_payload(payload: object) -> Any:
    if not isinstance(payload, dict):
        raise RuntimeError("update worker returned an invalid asset")
    return velopack.VelopackAsset(
        str(payload.get("PackageId") or ""),
        str(payload.get("Version") or ""),
        str(payload.get("Type") or ""),
        str(payload.get("FileName") or ""),
        str(payload.get("SHA1") or ""),
        str(payload.get("SHA256") or ""),
        int(payload.get("Size") or 0),
        str(payload.get("NotesMarkdown") or ""),
        str(payload.get("NotesHtml") or ""),
    )


def _update_info_to_payload(info: Any | None) -> dict[str, Any] | None:
    if info is None:
        return None
    base = getattr(info, "BaseRelease", None)
    return {
        "target": _asset_to_payload(info.TargetFullRelease),
        "deltas": [_asset_to_payload(asset) for asset in list(info.DeltasToTarget or [])],
        "is_downgrade": bool(info.IsDowngrade),
        "base": _asset_to_payload(base) if base is not None else None,
    }


def _update_info_from_payload(payload: object) -> Any | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise RuntimeError("update worker returned an invalid update payload")
    target = _asset_from_payload(payload.get("target"))
    raw_deltas = payload.get("deltas")
    if raw_deltas is None:
        raw_deltas = []
    if not isinstance(raw_deltas, list):
        raise RuntimeError("update worker returned invalid delta metadata")
    deltas = [_asset_from_payload(item) for item in raw_deltas]
    raw_base = payload.get("base")
    base = _asset_from_payload(raw_base) if raw_base is not None else None
    return velopack.UpdateInfo(target, deltas, bool(payload.get("is_downgrade")), base)


def _update_worker_command() -> list[str]:
    if bool(getattr(sys, "frozen", False)):
        return [sys.executable, _UPDATE_CHECK_WORKER_ARG]
    return [
        sys.executable,
        "-c",
        "from app.velopack_runtime import run_update_check_worker; "
        "raise SystemExit(run_update_check_worker())",
    ]


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _write_update_check_result(result_path: Path, payload: dict[str, Any]) -> None:
    _write_json_atomic(result_path, payload)


def _update_child_environment(*, use_system_proxy: bool) -> dict[str, str]:
    env = os.environ.copy()
    if use_system_proxy:
        try:
            proxies = urllib.request.getproxies()
        except Exception:
            proxies = {}
        if not env.get("ALL_PROXY") and not env.get("all_proxy"):
            https_proxy = str(proxies.get("https") or "").strip()
            http_proxy = str(proxies.get("http") or "").strip()
            if https_proxy:
                env.setdefault("HTTPS_PROXY", https_proxy)
                env.setdefault("https_proxy", https_proxy)
            if http_proxy:
                env.setdefault("HTTP_PROXY", http_proxy)
                env.setdefault("http_proxy", http_proxy)
    else:
        for name in _PROXY_ENV_NAMES:
            env.pop(name, None)
    return env


def _run_check_worker() -> int:
    result_text = str(os.getenv(_UPDATE_CHECK_RESULT_ENV, "") or "").strip()
    if not result_text:
        return 2
    result_path = Path(result_text)
    source = str(os.getenv(_UPDATE_CHECK_SOURCE_ENV, "") or "").strip() or None
    try:
        info = create_update_manager(source).check_for_updates()
        payload: dict[str, Any] = {"ok": True, "info": _update_info_to_payload(info)}
    except Exception as exc:
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    try:
        _write_update_check_result(result_path, payload)
    except OSError:
        return 3
    return 0 if payload.get("ok") else 1


def _download_with_chunked_fallback(
    manager: Any,
    info: Any,
    source: str | None,
    progress: Callable[[int], None],
) -> Any:
    """Keep normal Velopack transport first, then recover from a mirrored full package.

    The chunk path is transport-only: bytes are reassembled and strictly checked,
    then handed back to an ordinary Velopack local-directory source. Velopack still
    owns release selection, delta/full semantics, staging and install validation.
    """

    try:
        manager.download_updates(info, progress)
        return manager
    except Exception as primary_error:
        candidate = str(source or "").strip().rstrip("/")
        if not candidate.startswith("https://") or candidate == GITHUB_REPOSITORY_URL:
            raise
        try:
            with materialize_chunked_velopack_source(
                candidate,
                info,
                progress=lambda value: progress(max(0, min(85, int(value) * 85 // 100))),
            ) as local_source:
                local_manager = create_update_manager(str(local_source))
                local_info = local_manager.check_for_updates()
                if local_info is None:
                    raise RuntimeError("chunked local source did not expose the expected update")
                expected = str(info.TargetFullRelease.Version or "").strip().lstrip("v")
                actual = str(local_info.TargetFullRelease.Version or "").strip().lstrip("v")
                if actual != expected:
                    raise RuntimeError(
                        f"chunked local source target mismatch: expected={expected} actual={actual}"
                    )
                local_manager.download_updates(
                    local_info,
                    lambda value: progress(85 + max(0, min(15, int(value) * 15 // 100))),
                )
                if local_manager.get_update_pending_restart() is None:
                    raise RuntimeError("chunked Velopack download completed without a pending update")
                return local_manager
        except ChunkMirrorUnavailable:
            raise primary_error


def _run_download_worker() -> int:
    result_text = str(os.getenv(_UPDATE_CHECK_RESULT_ENV, "") or "").strip()
    progress_text = str(os.getenv(_UPDATE_DOWNLOAD_PROGRESS_ENV, "") or "").strip()
    target_version = str(os.getenv(_UPDATE_DOWNLOAD_TARGET_ENV, "") or "").strip().lstrip("v")
    source = str(os.getenv(_UPDATE_CHECK_SOURCE_ENV, "") or "").strip() or None
    if not result_text or not progress_text or _version_key(target_version) is None:
        return 2

    result_path = Path(result_text)
    progress_path = Path(progress_text)
    try:
        _write_json_atomic(progress_path, {"stage": "checking", "progress": 0})
        manager = create_update_manager(source)
        info = manager.check_for_updates()
        if info is None:
            raise RuntimeError(f"update source no longer offers v{target_version}")
        actual = str(info.TargetFullRelease.Version or "").strip().lstrip("v")
        if actual != target_version:
            raise RuntimeError(
                f"update target changed while downloading: expected={target_version} actual={actual}"
            )
        _write_json_atomic(progress_path, {"stage": "downloading", "progress": 0})

        def _progress(value: Any) -> None:
            try:
                percent = max(0, min(100, int(value)))
            except (TypeError, ValueError):
                percent = 0
            _write_json_atomic(progress_path, {"stage": "downloading", "progress": percent})

        manager = _download_with_chunked_fallback(manager, info, source, _progress)
        if manager.get_update_pending_restart() is None:
            raise RuntimeError("Velopack download completed without a pending update")
        payload: dict[str, Any] = {"ok": True, "source": str(source or "")}
    except Exception as exc:
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    try:
        _write_json_atomic(result_path, payload)
    except OSError:
        return 3
    return 0 if payload.get("ok") else 1


def run_update_check_worker() -> int:
    """Frozen worker entry point shared by isolated check/download operations."""

    mode = str(os.getenv(_UPDATE_WORKER_MODE_ENV, "check") or "check").strip().casefold()
    if mode == "download":
        return _run_download_worker()
    return _run_check_worker()


def bounded_check_for_updates(
    source: str | None = None,
    *,
    timeout_seconds: float = _UPDATE_CHECK_TIMEOUT_SECONDS,
    use_system_proxy: bool = True,
) -> Any | None:
    """Discover one source in a killable process with a true wall-clock deadline."""

    timeout = max(1.0, float(timeout_seconds))
    with tempfile.TemporaryDirectory(prefix="listing-studio-update-check-") as temp_dir:
        result_path = Path(temp_dir) / "result.json"
        env = _update_child_environment(use_system_proxy=use_system_proxy)
        env[_UPDATE_WORKER_MODE_ENV] = "check"
        env[_UPDATE_CHECK_SOURCE_ENV] = str(source or "").strip()
        env[_UPDATE_CHECK_RESULT_ENV] = str(result_path)
        env[_UPDATE_CHECK_PROXY_ENV] = "1" if use_system_proxy else "0"
        creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
        process = subprocess.Popen(
            _update_worker_command(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            creationflags=creationflags,
        )
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait()
            raise UpdateCheckTimeoutError(
                f"Velopack update check timed out after {timeout:.0f}s"
            ) from exc

        if not result_path.is_file():
            raise RuntimeError(
                f"Velopack update check worker exited without a result: code={process.returncode}"
            )
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Velopack update check worker returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Velopack update check worker returned invalid data")
        if not payload.get("ok"):
            raise RuntimeError(str(payload.get("error") or "Velopack update check failed"))
        return _update_info_from_payload(payload.get("info"))


def check_update_route(
    route: StableUpdateRoute,
    current_version: str,
    *,
    timeout_seconds: float = _UPDATE_CHECK_TIMEOUT_SECONDS,
) -> tuple[Any | None, str]:
    """Check ordered CDN/GitHub routes within one shared deadline."""

    advertised_key = _version_key(route.advertised_version)
    current_key = _version_key(current_version)
    if advertised_key is not None and current_key is not None and advertised_key <= current_key:
        return None, route.sources[0]

    sources = tuple(dict.fromkeys(source for source in route.sources if source))
    if not sources:
        raise UpdateSourceError("no Stable update sources are available")

    deadline = time.monotonic() + max(2.0, float(timeout_seconds))
    errors: list[str] = []
    proxy_available = _system_proxy_available()
    modes = (route.prefer_system_proxy, not route.prefer_system_proxy) if proxy_available else (False,)
    attempts: list[tuple[str, bool]] = []
    for source in sources:
        attempts.append((source, modes[0]))
    for source in sources:
        if len(modes) < 2:
            break
        candidate = (source, modes[1])
        if candidate not in attempts:
            attempts.append(candidate)

    for index, (source, use_proxy) in enumerate(attempts):
        remaining = deadline - time.monotonic()
        if remaining < 1.0:
            break
        primary_attempt = index < len(sources)
        primary_left = max(0, len(sources) - index - 1) if primary_attempt else 0
        reserve = 4.0 if primary_left > 0 else 0.0
        if primary_attempt:
            slice_timeout = max(1.0, min(_UPDATE_SOURCE_SLICE_SECONDS, remaining - reserve))
        else:
            slice_timeout = max(1.0, min(3.0, remaining))
        try:
            info = bounded_check_for_updates(
                source,
                timeout_seconds=slice_timeout,
                use_system_proxy=use_proxy,
            )
            if info is None:
                if advertised_key is None:
                    return None, source
                errors.append(f"{source}:stale_feed")
                continue
            actual = str(info.TargetFullRelease.Version or "").strip().lstrip("v")
            if advertised_key is not None and actual != route.advertised_version:
                errors.append(
                    f"{source}:target_mismatch expected={route.advertised_version} actual={actual}"
                )
                continue
            return info, source
        except Exception as exc:
            mode = "proxy" if use_proxy else "direct"
            errors.append(f"{source}[{mode}]:{type(exc).__name__}:{exc}")

    detail = "; ".join(errors[-6:]) or route.control_plane_error or "no route completed"
    raise UpdateSourceError(f"all Stable update routes failed: {detail}")


def _read_progress_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _download_from_source(
    source: str,
    target_version: str,
    *,
    progress: Callable[[int], None] | None,
    use_system_proxy: bool,
) -> None:
    with tempfile.TemporaryDirectory(prefix="listing-studio-update-download-") as temp_dir:
        result_path = Path(temp_dir) / "result.json"
        progress_path = Path(temp_dir) / "progress.json"
        env = _update_child_environment(use_system_proxy=use_system_proxy)
        env[_UPDATE_WORKER_MODE_ENV] = "download"
        env[_UPDATE_CHECK_SOURCE_ENV] = source
        env[_UPDATE_CHECK_RESULT_ENV] = str(result_path)
        env[_UPDATE_DOWNLOAD_PROGRESS_ENV] = str(progress_path)
        env[_UPDATE_DOWNLOAD_TARGET_ENV] = target_version
        creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
        process = subprocess.Popen(
            _update_worker_command(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            creationflags=creationflags,
        )

        started = time.monotonic()
        last_activity = started
        last_signature: tuple[int, int] | None = None
        stage = "checking"
        try:
            while process.poll() is None:
                try:
                    stat = progress_path.stat()
                    signature = (int(stat.st_mtime_ns), int(stat.st_size))
                except OSError:
                    signature = None
                if signature is not None and signature != last_signature:
                    last_signature = signature
                    payload = _read_progress_payload(progress_path)
                    next_stage = str(payload.get("stage") or stage)
                    stage = next_stage if next_stage in {"checking", "downloading"} else stage
                    last_activity = time.monotonic()
                    if progress is not None and stage == "downloading":
                        try:
                            progress(max(0, min(100, int(payload.get("progress") or 0))))
                        except (TypeError, ValueError):
                            pass

                now = time.monotonic()
                if stage == "checking" and now - started > _UPDATE_DOWNLOAD_CHECK_TIMEOUT_SECONDS:
                    raise UpdateCheckTimeoutError(
                        f"download source check timed out after {_UPDATE_DOWNLOAD_CHECK_TIMEOUT_SECONDS:.0f}s"
                    )
                if stage == "downloading" and now - last_activity > _UPDATE_DOWNLOAD_IDLE_TIMEOUT_SECONDS:
                    raise UpdateDownloadTimeoutError(
                        f"update download made no progress for {_UPDATE_DOWNLOAD_IDLE_TIMEOUT_SECONDS:.0f}s"
                    )
                time.sleep(_UPDATE_DOWNLOAD_POLL_SECONDS)
        except Exception:
            process.kill()
            process.wait()
            raise

        if not result_path.is_file():
            raise RuntimeError(f"update download worker exited without a result: code={process.returncode}")
        payload = _read_progress_payload(result_path)
        if not payload.get("ok"):
            raise RuntimeError(str(payload.get("error") or "Velopack update download failed"))
        if progress is not None:
            progress(100)


def download_update_with_failover(
    target_version: str,
    sources: tuple[str, ...] | list[str],
    *,
    preferred_source: str = "",
    prefer_system_proxy: bool = True,
    progress: Callable[[int], None] | None = None,
    status: Callable[[str], None] | None = None,
) -> str:
    """Download the exact target through isolated CDN/GitHub failover workers."""

    target = str(target_version or "").strip().lstrip("v")
    if _version_key(target) is None:
        raise ValueError(f"invalid target version: {target_version}")
    ordered: list[str] = []
    first = str(preferred_source or "").strip().rstrip("/")
    if first:
        ordered.append(first)
    for source in sources:
        value = str(source or "").strip().rstrip("/")
        if value and value not in ordered:
            ordered.append(value)
    if not ordered:
        raise UpdateSourceError("no update download sources are available")

    errors: list[str] = []
    proxy_available = _system_proxy_available()
    modes = (prefer_system_proxy, not prefer_system_proxy) if proxy_available else (False,)
    attempts = [(source, modes[0]) for source in ordered]
    if len(modes) > 1:
        attempts.extend((source, modes[1]) for source in ordered if (source, modes[1]) not in attempts)

    for index, (source, use_proxy) in enumerate(attempts, start=1):
        if status is not None:
            mode_text = "系统代理" if use_proxy else "直连"
            status(f"正在连接更新源 {index}/{len(attempts)} · {mode_text}…")
        try:
            _download_from_source(
                source,
                target,
                progress=progress,
                use_system_proxy=use_proxy,
            )
            return source
        except Exception as exc:
            mode = "proxy" if use_proxy else "direct"
            errors.append(f"{source}[{mode}]:{type(exc).__name__}:{exc}")

    raise UpdateSourceError("all update download routes failed: " + "; ".join(errors[-6:]))


def installed_application_version() -> str:
    if is_velopack_managed():
        try:
            return str(create_update_manager().get_current_version()).strip().lstrip("v")
        except Exception:
            pass
    return embedded_application_version()


def update_summary(info: Any) -> dict[str, Any]:
    release = info.TargetFullRelease
    return {
        "version": str(release.Version).strip().lstrip("v"),
        "size": int(release.Size or 0),
        "notes": str(release.NotesMarkdown or "").strip(),
        "file_name": str(release.FileName or "").strip(),
    }


__all__ = [
    "GITHUB_REPOSITORY_URL",
    "PORTAL_RELEASE_URL",
    "UPDATE_SOURCE_ENV",
    "StableUpdateRoute",
    "UpdateCheckTimeoutError",
    "UpdateDownloadTimeoutError",
    "UpdateSourceError",
    "bounded_check_for_updates",
    "check_update_route",
    "create_update_manager",
    "download_update_with_failover",
    "embedded_application_version",
    "installed_application_version",
    "is_velopack_managed",
    "resolve_stable_update_route",
    "resolve_stable_update_source",
    "run_update_check_worker",
    "update_summary",
    "velopack_root",
]
