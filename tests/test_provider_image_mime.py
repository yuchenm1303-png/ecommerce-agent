from __future__ import annotations

import importlib
import mimetypes

import app.providers as providers


def test_webp_mime_is_registered_even_when_platform_database_lacks_it(monkeypatch):
    monkeypatch.delitem(mimetypes.types_map, ".webp", raising=False)
    monkeypatch.delitem(mimetypes.common_types, ".webp", raising=False)

    importlib.reload(providers)

    mime, _ = mimetypes.guess_type("source-image-01-a145ae8d13.webp")
    assert mime == "image/webp"
