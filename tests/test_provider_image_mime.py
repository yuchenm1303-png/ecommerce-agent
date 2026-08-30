from __future__ import annotations

import base64

from app.providers.openai_compatible import _image_data_uri


def test_provider_derives_image_mime_from_bytes_for_opaque_supplier_file(tmp_path):
    image = tmp_path / "source-image-02-deadbeef.img"
    payload = b"\xff\xd8\xff\xe0" + b"supplier-image-bytes"
    image.write_bytes(payload)

    uri = _image_data_uri(str(image))

    assert uri == "data:image/jpeg;base64," + base64.b64encode(payload).decode("ascii")


def test_provider_ignores_filename_suffix_when_bytes_establish_webp(tmp_path):
    image = tmp_path / "supplier-photo.jpg"
    payload = b"RIFF" + (12).to_bytes(4, "little") + b"WEBP" + b"payload"
    image.write_bytes(payload)

    uri = _image_data_uri(str(image))

    assert uri.startswith("data:image/webp;base64,")
