from __future__ import annotations

import os
import sys
from pathlib import Path

_DATA_ENV = "ECOMMERCE_AGENT_DATA_DIR"
_APP_DATA_DIRNAME = "EcommerceAgent"
_INSTALL_REGISTRY_KEY = r"Software\EcommerceAgent"
_INSTALL_REGISTRY_VALUE = "InstallDir"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def source_project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def installed_application_dir() -> Path | None:
    """Return the Inno-owned install directory for the current Windows user."""

    if os.name != "nt" or not is_frozen():
        return None
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            _INSTALL_REGISTRY_KEY,
            0,
            winreg.KEY_READ,
        ) as key:
            value, _ = winreg.QueryValueEx(key, _INSTALL_REGISTRY_VALUE)
    except (ImportError, OSError):
        return None

    text = os.path.expandvars(str(value or "").strip())
    if not text:
        return None
    try:
        return Path(text).expanduser().resolve()
    except OSError:
        return None


def is_installed_distribution() -> bool:
    """True only when this frozen executable is running from the Inno install tree.

    The portable archive contains the same frozen binaries, so ``sys.frozen`` is
    not sufficient to decide whether self-update is safe.  Inno records its
    exact install directory in HKCU and portable copies have no matching record.
    """

    install_dir = installed_application_dir()
    if install_dir is None:
        return False
    try:
        current_dir = Path(sys.executable).resolve().parent
    except OSError:
        return False
    return os.path.normcase(os.path.abspath(str(current_dir))) == os.path.normcase(
        os.path.abspath(str(install_dir))
    )


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


__all__ = [
    "installed_application_dir",
    "is_frozen",
    "is_installed_distribution",
    "runtime_root",
    "source_project_root",
]
