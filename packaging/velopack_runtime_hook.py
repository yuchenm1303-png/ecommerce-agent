"""PyInstaller runtime hook: Velopack must run before normal GUI startup."""
from __future__ import annotations

import os
import sys

import velopack

_E2E_SOURCE = "--velopack-e2e-source"
_E2E_TARGET = "--velopack-e2e-target"
_E2E_MARKER = "--velopack-e2e-marker"
_GUI_MARKER_ENV = "ECOMMERCE_AGENT_UPDATE_E2E_MARKER"


def _arg(name: str) -> str:
    try:
        index = sys.argv.index(name)
    except ValueError:
        return ""
    if index + 1 >= len(sys.argv):
        return ""
    return str(sys.argv[index + 1] or "").strip()


velopack.App().run()

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
        _manager.wait_exit_then_apply_updates(
            _info,
            silent=True,
            restart=True,
            restart_args=sys.argv[1:],
        )
        raise SystemExit(0)
    os.environ[_GUI_MARKER_ENV] = _marker
