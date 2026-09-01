from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import velopack

GITHUB_REPOSITORY_URL = "https://github.com/yuchenm1303-png/ecommerce-agent"
UPDATE_SOURCE_ENV = "ECOMMERCE_AGENT_UPDATE_SOURCE"
_UPDATE_CHECK_SOURCE_ENV = "ECOMMERCE_AGENT_UPDATE_CHECK_SOURCE"
_UPDATE_CHECK_WORKER_ARG = "--internal-velopack-check"
_UPDATE_CHECK_RESULT_PREFIX = "LISTING_STUDIO_UPDATE_CHECK_RESULT:"
_UPDATE_CHECK_TIMEOUT_SECONDS = 12.0
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


class UpdateCheckTimeoutError(RuntimeError):
    """Raised after a Velopack discovery worker exceeds its hard deadline."""


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
    """Retry only transient transport failures while Velopack owns update semantics."""

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
    """Create the authoritative Stable manager with bounded transport recovery."""

    return _ResilientUpdateManager(_create_raw_update_manager(source))


def _asset_to_payload(asset: Any) -> dict[str, Any]:
    payload = {field: getattr(asset, field) for field in _ASSET_FIELDS}
    payload["Size"] = int(payload.get("Size") or 0)
    return payload


def _asset_from_payload(payload: object) -> Any:
    if not isinstance(payload, dict):
        raise RuntimeError("update check worker returned an invalid asset")
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
        raise RuntimeError("update check worker returned an invalid update payload")
    target = _asset_from_payload(payload.get("target"))
    raw_deltas = payload.get("deltas")
    if raw_deltas is None:
        raw_deltas = []
    if not isinstance(raw_deltas, list):
        raise RuntimeError("update check worker returned invalid delta metadata")
    deltas = [_asset_from_payload(item) for item in raw_deltas]
    raw_base = payload.get("base")
    base = _asset_from_payload(raw_base) if raw_base is not None else None
    return velopack.UpdateInfo(target, deltas, bool(payload.get("is_downgrade")), base)


def _update_check_command() -> list[str]:
    if bool(getattr(sys, "frozen", False)):
        return [sys.executable, _UPDATE_CHECK_WORKER_ARG]
    return [
        sys.executable,
        "-c",
        "from app.velopack_runtime import run_update_check_worker; "
        "raise SystemExit(run_update_check_worker())",
    ]


def run_update_check_worker() -> int:
    """Run one Velopack discovery in an isolated process and emit a framed result."""

    source = str(os.getenv(_UPDATE_CHECK_SOURCE_ENV, "") or "").strip() or None
    try:
        info = create_update_manager(source).check_for_updates()
        payload: dict[str, Any] = {"ok": True, "info": _update_info_to_payload(info)}
    except Exception as exc:
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print(
        _UPDATE_CHECK_RESULT_PREFIX + json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        flush=True,
    )
    return 0 if payload.get("ok") else 1


def bounded_check_for_updates(
    source: str | None = None,
    *,
    timeout_seconds: float = _UPDATE_CHECK_TIMEOUT_SECONDS,
) -> Any | None:
    """Discover an update in a killable child process with a true wall-clock deadline."""

    timeout = max(1.0, float(timeout_seconds))
    env = os.environ.copy()
    env[_UPDATE_CHECK_SOURCE_ENV] = str(source or "").strip()
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
    process = subprocess.Popen(
        _update_check_command(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        creationflags=creationflags,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate()
        raise UpdateCheckTimeoutError(
            f"Velopack update check timed out after {timeout:.0f}s"
        ) from exc

    result_text = ""
    for line in reversed(stdout.splitlines()):
        if line.startswith(_UPDATE_CHECK_RESULT_PREFIX):
            result_text = line[len(_UPDATE_CHECK_RESULT_PREFIX) :]
            break
    if not result_text:
        detail = stderr.strip() or stdout.strip() or f"worker exit code {process.returncode}"
        raise RuntimeError(f"Velopack update check worker failed: {detail}")
    try:
        payload = json.loads(result_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Velopack update check worker returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Velopack update check worker returned invalid data")
    if not payload.get("ok"):
        raise RuntimeError(str(payload.get("error") or "Velopack update check failed"))
    return _update_info_from_payload(payload.get("info"))


def resolve_stable_update_source() -> tuple[str, str]:
    """Return the Stable source without performing a separate network preflight.

    The empty advertised-version field is intentional: Velopack's own release
    feed is the sole version authority. This keeps startup/manual update checks
    independent from the website metadata service and avoids two release
    discovery paths disagreeing or one service blocking the other.
    """

    override = str(os.getenv(UPDATE_SOURCE_ENV, "") or "").strip()
    if override:
        return "", override
    return "", GITHUB_REPOSITORY_URL


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
    "UPDATE_SOURCE_ENV",
    "UpdateCheckTimeoutError",
    "bounded_check_for_updates",
    "create_update_manager",
    "embedded_application_version",
    "installed_application_version",
    "is_velopack_managed",
    "resolve_stable_update_source",
    "run_update_check_worker",
    "update_summary",
    "velopack_root",
]
