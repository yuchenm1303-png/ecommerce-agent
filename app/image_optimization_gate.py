"""Process-level feature gate for the optional heavy listing-image AI pipeline.

The legacy Resolver image selection remains the default. GUI sessions may opt in
by setting this gate before spawning workflow subprocesses; child Resolver
processes inherit the setting mechanically. Product semantics remain owned by the
image AI pipeline itself, never by this gate.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, MutableMapping


IMAGE_OPTIMIZATION_ENV = "ECOMMERCE_AGENT_IMAGE_OPTIMIZATION"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def image_optimization_enabled(environ: Mapping[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    return str(source.get(IMAGE_OPTIMIZATION_ENV, "")).strip().casefold() in _TRUE_VALUES


def set_image_optimization_enabled(
    enabled: bool,
    environ: MutableMapping[str, str] | None = None,
) -> None:
    target = os.environ if environ is None else environ
    if enabled:
        target[IMAGE_OPTIMIZATION_ENV] = "1"
    else:
        target.pop(IMAGE_OPTIMIZATION_ENV, None)


__all__ = [
    "IMAGE_OPTIMIZATION_ENV",
    "image_optimization_enabled",
    "set_image_optimization_enabled",
]
