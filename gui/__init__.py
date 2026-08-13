"""Windows GUI package bootstrap."""

from __future__ import annotations

import os
import sys


_PACKAGE_IMPORT_PROBE = "ECOMMERCE_AGENT_PACKAGE_IMPORT_PROBE"


def _install_account_controls_hook() -> None:
    from . import app_access as _app_access

    if getattr(_app_access, "_account_controls_hooked", False):
        return

    original_install = _app_access.install_application_access

    def install_with_account_controls(window, session):
        controller = original_install(window, session)
        try:
            from .account_controls import install_application_account_controls

            install_application_account_controls(window, controller)
        except Exception as exc:  # Account UI must never block the core workspace.
            print(f"[account-controls] install skipped: {exc}", file=sys.stderr)
        return controller

    _app_access.install_application_access = install_with_account_controls
    _app_access._account_controls_hooked = True


_package_probe = os.environ.get(_PACKAGE_IMPORT_PROBE) == "1"
try:
    _install_account_controls_hook()
except Exception:
    if _package_probe:
        os._exit(92)
    raise

# Packaging CI launches the real windowed executable with this flag. Reaching
# this point proves gui.app_access and its top-level runtime dependencies loaded
# successfully from the built onedir package; normal application runs are unchanged.
if _package_probe:
    os._exit(0)
