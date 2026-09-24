import json
from pathlib import Path
from types import SimpleNamespace

from app.public_resource_fetch import PublicResourceFetchError
from app.source_image_download import install_source_image_downloader


class _Page:
    url = "https://www.amazon.co.za/dp/ABC?session=secret-value"


class _Context:
    pages = [_Page()]


def _engine(fetch):
    media = SimpleNamespace(extension=".jpg")
    return SimpleNamespace(
        _PRODUCT_IMAGE_CANDIDATE_LIMIT=32,
        _PRODUCT_IMAGE_MAX_BYTES=1024 * 1024,
        _PRODUCT_IMAGES_TOTAL_MAX_BYTES=4 * 1024 * 1024,
        _context_cookies=lambda context, url: [{"name": "session", "value": "cookie-secret"}],
        fetch_public_resource=fetch,
        normalize_image_media=lambda body, source: (body, media),
        _source_image_downloader_hardened=False,
    )


def test_hardened_downloader_passes_supplier_referer_and_saves(tmp_path: Path):
    seen = {}

    def fetch(url, **kwargs):
        seen["url"] = url
        seen.update(kwargs)
        return SimpleNamespace(body=b"jpeg-bytes", final_url=url)

    engine = _engine(fetch)
    install_source_image_downloader(engine)
    paths = engine._download_page_images(
        _Context(),
        ["https://images.example.com/product.jpg"],
        tmp_path,
    )

    assert len(paths) == 1
    assert paths[0].is_file()
    assert seen["referer"] == _Page.url
    assert seen["cookies"] == [{"name": "session", "value": "cookie-secret"}]


def test_fetch_failure_diagnostic_does_not_log_query_or_cookie_secret(tmp_path: Path, capsys):
    def fetch(url, **kwargs):
        raise PublicResourceFetchError(
            "background resource returned HTTP 403 for https://images.example.com/product.jpg?token=do-not-log"
        )

    engine = _engine(fetch)
    install_source_image_downloader(engine)
    paths = engine._download_page_images(
        _Context(),
        ["https://images.example.com/product.jpg?token=do-not-log"],
        tmp_path,
    )

    assert paths == ()
    output = capsys.readouterr().out
    assert "http_403" in output
    assert "images.example.com" in output
    assert "do-not-log" not in output
    assert "secret-value" not in output
    assert "cookie-secret" not in output
    payload = json.loads(output.split("SOURCE_IMAGE_DOWNLOAD ", 1)[1])
    assert payload["rejected_candidates"] == [
        {"url": "https://images.example.com/product.jpg", "reason": "http_403"}
    ]
