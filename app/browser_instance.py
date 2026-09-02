from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Mapping


MANAGED_MAKRO_CDP_PORT_ENV = "ECOMMERCE_AGENT_MAKRO_CDP_PORT"
_MANAGED_PORT_START = 12000
_MANAGED_PORT_COUNT = 20000


def browser_instance_namespace(project_root: str | Path) -> str:
    """Return a stable, non-secret namespace for one Listing Studio runtime root.

    Source worktrees use different runtime roots, so stable/dev copies naturally
    receive different browser namespaces. Installed builds keep one stable runtime
    root across application upgrades and therefore keep reusing the same managed
    browser namespace/profile.
    """

    root = Path(project_root).expanduser().resolve()
    normalized = os.path.normcase(str(root))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _validated_explicit_port(value: str) -> int:
    try:
        port = int(str(value or "").strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{MANAGED_MAKRO_CDP_PORT_ENV} must be an integer TCP port") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"{MANAGED_MAKRO_CDP_PORT_ENV} must be within 1..65535")
    return port


def managed_makro_cdp_port(
    project_root: str | Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Resolve the managed Makro CDP port for exactly one runtime root.

    The former global 9222 port made two simultaneously running Listing Studio
    copies observe and recover the same Edge generation. A deterministic runtime-
    root namespace gives each worktree/install its own CDP transport while keeping
    the port stable across restarts of that same copy. An explicit environment
    override remains available for controlled diagnostics.
    """

    env = os.environ if environ is None else environ
    explicit = str(env.get(MANAGED_MAKRO_CDP_PORT_ENV) or "").strip()
    if explicit:
        return _validated_explicit_port(explicit)

    namespace = browser_instance_namespace(project_root)
    bucket = int(namespace[:8], 16) % _MANAGED_PORT_COUNT
    return _MANAGED_PORT_START + bucket


__all__ = [
    "MANAGED_MAKRO_CDP_PORT_ENV",
    "browser_instance_namespace",
    "managed_makro_cdp_port",
]
