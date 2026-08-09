"""Provider adapters and cross-platform media registration."""

from __future__ import annotations

import mimetypes

# Windows/Python installations do not consistently ship a WebP MIME mapping.
# Product-page capture legitimately stores supplier images as .webp, and the
# multimodal provider needs an image/* data-URI media type before sending them.
mimetypes.add_type("image/webp", ".webp", strict=True)
