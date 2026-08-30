from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

import velopack

GITHUB_REPOSITORY_URL = "https://github.com/yuchenm1303-png/ecommerce-agent"
UPDATE_SOURCE_ENV = "ECOMMERCE_AGENT_UPDATE_SOURCE"
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
    "create_update_manager",
    "embedded_application_version",
    "installed_application_version",
    "is_velopack_managed",
    "resolve_stable_update_source",
    "update_summary",
    "velopack_root",
]
