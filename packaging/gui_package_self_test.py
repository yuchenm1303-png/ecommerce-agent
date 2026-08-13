from __future__ import annotations

import importlib
import os
from pathlib import Path


_ENV_FLAG = "ECOMMERCE_AGENT_PACKAGE_SELF_TEST"


if os.environ.get(_ENV_FLAG) == "1":
    try:
        access = importlib.import_module("gui.app_access")
        module_path = Path(str(access.__file__ or "")).resolve()
        if module_path.suffix.lower() != ".py" or not module_path.is_file():
            raise RuntimeError(f"app_access is not loaded from packaged source: {module_path}")

        probe = b"listing-studio-package-self-test"
        protected = access._dpapi_protect(probe)
        if access._dpapi_unprotect(protected) != probe:
            raise RuntimeError("DPAPI round-trip failed")

        device_id, device_name = access.device_identity()
        if not device_id or not device_name:
            raise RuntimeError("device identity self-test failed")
    except Exception:
        os._exit(91)
    os._exit(0)
