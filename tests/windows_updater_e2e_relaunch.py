"""Independent frozen executable used to prove updater relaunch reaches Python."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    marker = str(os.getenv("ECOMMERCE_AGENT_E2E_RELAUNCH_MARKER") or "").strip()
    if not marker:
        return 2
    path = Path(marker)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "started": True,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "executable": str(Path(sys.executable).resolve()),
        "frozen": bool(getattr(sys, "frozen", False)),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
