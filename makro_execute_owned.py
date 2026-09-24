"""Canonical GUI host for one real Makro execution.

The business executor stays in ``makro_execute_listing``. This host owns the
cross-process CDP transport lane in the *same process* as that executor, so both
source-Python runs and the installed ``EcommerceAgentWorker.exe`` follow exactly
the same ownership path. It never spawns another Python/executable child.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from app.browser_session import DEFAULT_CDP_PORT
from app.cdp_automation_health import poison_matches_current_generation
from app.cdp_transport_lane import exclusive_cdp_transport_lane
from app.makro.photo_acceptance import install_executor_photo_guards
import makro_execute_listing as _executor


# Makro Product Photos is mandatory for a persistable listing. Install the
# canonical gate before any execution entrypoint can classify a zero-photo draft
# as strict success. The guard is idempotent and preserves already-persisted
# gallery images when no new upload is required.
install_executor_photo_guards(_executor)


def _cdp_port(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--cdp-port", type=int, default=DEFAULT_CDP_PORT)
    parsed, _unknown = parser.parse_known_args([str(value) for value in argv])
    return int(parsed.cdp_port)


def main() -> int:
    port = _cdp_port(sys.argv[1:])
    with exclusive_cdp_transport_lane(port):
        if poison_matches_current_generation(port):
            raise RuntimeError(
                "Makro Browser automation generation 已标记失效；"
                "真实执行不会继续 attach，等待 GUI 在空闲边界安全恢复。"
            )
        return int(_executor.main())


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
