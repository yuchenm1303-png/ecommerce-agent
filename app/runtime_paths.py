from __future__ import annotations

import os
import sys
from pathlib import Path

_DATA_ENV = "ECOMMERCE_AGENT_DATA_DIR"
_APP_DATA_DIRNAME = "EcommerceAgent"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def source_project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def installed_application_dir() -> Path | None:
    """Return the Velopack installation root for the current process."""

    if os.name != "nt" or not is_frozen():
        return None
    try:
        from app.velopack_runtime import velopack_root

        return velopack_root()
    except Exception:
        return None


def is_installed_distribution() -> bool:
    """True only for the Velopack-managed installed application.

    A raw PyInstaller directory and the Velopack portable archive are not treated
    as installed self-updating distributions. This keeps update ownership inside
    Velopack instead of reconstructing install identity from our own registry
    marker or directory convention.
    """

    return installed_application_dir() is not None


def runtime_root() -> Path:
    """Return the writable application-data root outside the versioned app tree."""

    override = str(os.getenv(_DATA_ENV, "") or "").strip()
    if override:
        root = Path(override).expanduser()
    elif is_frozen():
        local_app_data = str(os.getenv("LOCALAPPDATA", "") or "").strip()
        if local_app_data:
            root = Path(local_app_data) / _APP_DATA_DIRNAME
        else:
            root = Path.home() / "AppData" / "Local" / _APP_DATA_DIRNAME
    else:
        root = source_project_root()

    root = root.resolve()
    for relative in ("logs", "browser_profiles"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    return root


__all__ = [
    "installed_application_dir",
    "is_frozen",
    "is_installed_distribution",
    "runtime_root",
    "source_project_root",
]
