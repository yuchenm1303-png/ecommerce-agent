"""Windows GUI package bootstrap."""

from __future__ import annotations

import os
import sys


_PACKAGE_IMPORT_PROBE = "ECOMMERCE_AGENT_PACKAGE_IMPORT_PROBE"


def _install_application_access_extensions_hook() -> None:
    from . import app_access as _app_access

    if getattr(_app_access, "_application_access_extensions_hooked", False):
        return

    original_install = _app_access.install_application_access

    def install_with_extensions(window, session):
        controller = original_install(window, session)
        try:
            from .static_label_stability import install_static_label_stability

            install_static_label_stability()
        except Exception as exc:  # Presentation stability must never block the core workspace.
            print(f"[static-label-stability] install skipped: {exc}", file=sys.stderr)
        try:
            from .account_controls import install_application_account_controls

            install_application_account_controls(window, controller)
        except Exception as exc:  # Account UI must never block the core workspace.
            print(f"[account-controls] install skipped: {exc}", file=sys.stderr)
        try:
            from .batch_link_telemetry import install_batch_link_telemetry

            install_batch_link_telemetry(window, controller)
        except Exception as exc:  # Telemetry must never block the core workspace.
            print(f"[batch-link-telemetry] install skipped: {exc}", file=sys.stderr)

        # Agent is a detached workspace extension. Defer installation until the
        # first Qt event-loop turn so run_local_gui.py can finish constructing the
        # existing Single/Batch workspace and its authoritative QQuickWindow first.
        # No Agent model/provider is initialized until the user actually opens it.
        try:
            from PySide6.QtCore import QTimer

            def install_agent_workspace_extension() -> None:
                try:
                    from .agent_workspace import install_agent_workspace

                    install_agent_workspace(window)
                except Exception as exc:  # Agent must never block Listing startup.
                    print(f"[agent-workspace] install skipped: {exc}", file=sys.stderr)

            QTimer.singleShot(0, install_agent_workspace_extension)
        except Exception as exc:  # Keep Listing fully independent from Agent UI.
            print(f"[agent-workspace] deferred install unavailable: {exc}", file=sys.stderr)
        return controller

    _app_access.install_application_access = install_with_extensions
    _app_access._application_access_extensions_hooked = True


_package_probe = os.environ.get(_PACKAGE_IMPORT_PROBE) == "1"
try:
    _install_application_access_extensions_hook()
except Exception:
    if _package_probe:
        os._exit(92)
    raise

# Packaging CI launches the real windowed executable with this flag. Reaching
# this point proves gui.app_access and its top-level runtime dependencies loaded
# successfully from the built onedir package; normal application runs are unchanged.
if _package_probe:
    os._exit(0)