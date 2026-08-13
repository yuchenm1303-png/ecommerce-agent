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


def runtime_root() -> Path:
    """Return the writable application root.

    Source/development runs keep the historical repository-local layout.
    Frozen Windows builds move mutable state out of the install directory into
    LOCALAPPDATA so upgrades can replace program files without touching login
    profiles, run history, caches, or batch state.
    """

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


__all__ = ["is_frozen", "runtime_root", "source_project_root"]
