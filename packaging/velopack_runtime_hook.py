"""PyInstaller runtime hook: Velopack must run before normal GUI startup."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import velopack

from app.update_browser_gate import prepare_for_velopack_transition

_E2E_SOURCE = "--velopack-e2e-source"
_E2E_TARGET = "--velopack-e2e-target"
_E2E_MARKER = "--velopack-e2e-marker"
_GUI_MARKER_ENV = "ECOMMERCE_AGENT_UPDATE_E2E_MARKER"
_TRANSITION_LOG = Path(tempfile.gettempdir()) / "listing-studio-velopack-transition.log"


def _arg(name: str) -> str:
    try:
        index = sys.argv.index(name)
    except ValueError:
        return ""
    if index + 1 >= len(sys.argv):
        return ""
    return str(sys.argv[index + 1] or "").strip()


def _log_transition(message: str) -> None:
    try:
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with _TRANSITION_LOG.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp}\t{message}\n")
    except OSError:
        pass


def _prepare_for_transition(*_args: object) -> None:
    """Release external browser ownership before Velopack mutates app files."""

    try:
        result = prepare_for_velopack_transition(log_path=_TRANSITION_LOG)
    except Exception as exc:
        # Fast lifecycle hooks must never become a new installation failure mode.
        # Velopack will still perform its own running-process/lock checks.
        _log_transition(f"transition cleanup raised {type(exc).__name__}: {exc}")
        return
    if result.ok:
        _log_transition("transition cleanup completed")
    else:
        _log_transition(f"transition cleanup incomplete: {result.detail}")


# Register fast hooks before Run(). This is the path used not only by in-app
# updates, but also when a user launches a newer Setup.exe over an installation
# that is still running. Without it, the GUI can exit while its dedicated Edge
# tree survives long enough to keep package files locked and Setup reports
# Windows access denied (os error 5).
_velopack_app = velopack.App()
_velopack_app.on_before_update_fast_callback(_prepare_for_transition)
_velopack_app.on_before_uninstall_fast_callback(_prepare_for_transition)
_velopack_app.run()

_source = _arg(_E2E_SOURCE)
_target = _arg(_E2E_TARGET).lstrip("v")
_marker = _arg(_E2E_MARKER)
if _source and _target and _marker:
    _manager = velopack.UpdateManager(_source)
    _current = str(_manager.get_current_version()).strip().lstrip("v")
    if _current != _target:
        _info = _manager.check_for_updates()
        if _info is None:
            raise RuntimeError(
                f"Velopack E2E expected update {_current} -> {_target}, but feed returned none"
            )
        _actual = str(_info.TargetFullRelease.Version).strip().lstrip("v")
        if _actual != _target:
            raise RuntimeError(
                f"Velopack E2E target mismatch: expected={_target} actual={_actual}"
            )
        _manager.download_updates(_info)
        _pending = _manager.get_update_pending_restart()
        if _pending is None:
            raise RuntimeError("Velopack E2E downloaded update but no pending asset was prepared")
        _manager.apply_updates_and_restart_with_args(_pending, sys.argv[1:])
        raise RuntimeError("Velopack E2E apply/restart unexpectedly returned")
    os.environ[_GUI_MARKER_ENV] = _marker
