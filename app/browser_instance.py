from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Iterator, Mapping


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
    """Resolve the legacy/default managed Makro CDP port for one runtime root.

    The first/default Makro account keeps using this value for backwards
    compatibility with already-running installations. Additional marketplace
    accounts receive their own stable ports via
    :func:`managed_makro_account_cdp_port_candidates`.
    """

    env = os.environ if environ is None else environ
    explicit = str(env.get(MANAGED_MAKRO_CDP_PORT_ENV) or "").strip()
    if explicit:
        return _validated_explicit_port(explicit)

    namespace = browser_instance_namespace(project_root)
    bucket = int(namespace[:8], 16) % _MANAGED_PORT_COUNT
    return _MANAGED_PORT_START + bucket


def managed_makro_account_cdp_port_candidates(
    project_root: str | Path,
    account_id: str,
) -> Iterator[int]:
    """Yield deterministic candidate ports for one Makro account.

    Allocation/collision ownership is intentionally kept outside this pure helper:
    ``ChannelAccountStore`` persists the first free candidate into its account
    metadata so later accounts can never silently steal an existing lane. Linear
    probing is deterministic and covers the complete managed range before
    exhaustion.
    """

    account = str(account_id or "").strip()
    if not account:
        raise ValueError("Makro account_id must not be empty")

    namespace = browser_instance_namespace(project_root)
    digest = hashlib.sha256(f"{namespace}:makro:{account}".encode("utf-8")).hexdigest()
    start_bucket = int(digest[:8], 16) % _MANAGED_PORT_COUNT
    for attempt in range(_MANAGED_PORT_COUNT):
        yield _MANAGED_PORT_START + ((start_bucket + attempt) % _MANAGED_PORT_COUNT)


__all__ = [
    "MANAGED_MAKRO_CDP_PORT_ENV",
    "browser_instance_namespace",
    "managed_makro_account_cdp_port_candidates",
    "managed_makro_cdp_port",
]
