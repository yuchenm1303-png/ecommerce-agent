from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import velopack

GITHUB_REPOSITORY_URL = "https://github.com/yuchenm1303-png/ecommerce-agent"
UPDATE_SOURCE_ENV = "ECOMMERCE_AGENT_UPDATE_SOURCE"


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
    "update_summary",
    "velopack_root",
]
