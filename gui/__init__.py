"""Windows GUI package bootstrap."""

from __future__ import annotations

import sys


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


_install_account_controls_hook()
