from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import velopack

GITHUB_REPOSITORY_URL = "https://github.com/yuchenm1303-png/ecommerce-agent"
PORTAL_RELEASE_URL = "https://nfzkphjbelyltrzgkdwt.supabase.co/functions/v1/portal-release"
UPDATE_SOURCE_ENV = "ECOMMERCE_AGENT_UPDATE_SOURCE"
_UPDATE_DISCOVERY_TIMEOUT_SECONDS = 8


class UpdateSourceError(RuntimeError):
    pass


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


def create_update_manager(source: str | None = None) -> velopack.UpdateManager:
    override = str(source or os.getenv(UPDATE_SOURCE_ENV, "") or "").strip()
    if override:
        return velopack.UpdateManager(override)
    return velopack.UpdateManager(velopack.GithubSource(GITHUB_REPOSITORY_URL, None, False))


def resolve_stable_update_source() -> tuple[str, str]:
    """Resolve the current Stable release through our own metadata service.

    The desktop client no longer spends an anonymous GitHub API request just to
    discover the latest release. The service returns the authoritative release
    version and a static Velopack base URL; Velopack then reads the release feed
    and package from that base URL.
    """

    override = str(os.getenv(UPDATE_SOURCE_ENV, "") or "").strip()
    if override:
        return "", override

    request = urllib.request.Request(
        PORTAL_RELEASE_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": f"ListingStudio/{embedded_application_version()}",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=_UPDATE_DISCOVERY_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise UpdateSourceError(f"update_service_http_{int(exc.code or 0)}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateSourceError("update_service_unreachable") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UpdateSourceError("update_service_invalid_response") from exc
    if not isinstance(payload, dict):
        raise UpdateSourceError("update_service_invalid_response")

    version = str(payload.get("version") or "").strip().lstrip("v")
    parts = version.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise UpdateSourceError("update_service_invalid_version")

    base_url = str(payload.get("updateBaseUrl") or "").strip().rstrip("/")
    if not base_url:
        base_url = f"{GITHUB_REPOSITORY_URL}/releases/download/v{version}"
    if not base_url.startswith("https://"):
        raise UpdateSourceError("update_service_invalid_source")
    return version, base_url


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
    "UpdateSourceError",
    "create_update_manager",
    "embedded_application_version",
    "installed_application_version",
    "is_velopack_managed",
    "resolve_stable_update_source",
    "update_summary",
    "velopack_root",
]
