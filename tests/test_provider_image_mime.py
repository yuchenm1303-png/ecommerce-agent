from __future__ import annotations

import base64
from io import BytesIO

from PIL import Image

from app.providers.openai_compatible import _image_data_uri


def _write_raster(path, *, format_name: str) -> None:
    Image.new("RGB", (32, 24), (80, 120, 160)).save(path, format=format_name)


def _decoded_transport(uri: str) -> Image.Image:
    prefix, encoded = uri.split(",", 1)
    assert prefix == "data:image/jpeg;base64"
    return Image.open(BytesIO(base64.b64decode(encoded)))


def test_provider_normalizes_opaque_img_without_magic_signature_whitelist(tmp_path):
    image = tmp_path / "source-image-02-deadbeef.img"
    _write_raster(image, format_name="BMP")

    uri = _image_data_uri(str(image))

    with _decoded_transport(uri) as decoded:
        assert decoded.format == "JPEG"
        assert decoded.size == (32, 24)


def test_provider_ignores_misleading_filename_suffix_and_normalizes_pixels(tmp_path):
    image = tmp_path / "supplier-photo.jpg"
    _write_raster(image, format_name="TIFF")

    uri = _image_data_uri(str(image))

    with _decoded_transport(uri) as decoded:
        assert decoded.format == "JPEG"
        assert decoded.size == (32, 24)
