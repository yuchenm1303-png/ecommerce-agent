"""Compatibility import for the canonical resilient application updater.

Historically this module subclassed ``gui.app_updater`` and duplicated most of
the update state machine.  The production updater now has one implementation in
``gui.app_updater``; keeping this shim preserves the existing formal GUI import
without allowing the two paths to drift again.
"""

from __future__ import annotations

from gui.app_updater import ApplicationUpdater, install_application_updater

__all__ = ["ApplicationUpdater", "install_application_updater"]
