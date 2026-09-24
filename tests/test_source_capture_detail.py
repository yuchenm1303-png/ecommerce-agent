from __future__ import annotations

from app.source_capture import _detail_document_urls, _detail_image_urls_from_text
from app.source_snapshot import SourceSnapshot


def snapshot(*embedded: str) -> SourceSnapshot:
    return SourceSnapshot(
        requested_url="https://detail.1688.com/offer/850845635717.html",
        final_url="https://detail.1688.com/offer/850845635717.html",
        title="M8",
        captured_at="2026-08-09T00:00:00Z",
        embedded_data=list(embedded),
    )


def test_detail_document_urls_are_extracted_from_escaped_embedded_data():
    item = snapshot(
        r'{\"detailUrl\":\"https:\/\/itemcdn.tmall.com\/1688offer\/exact-detail\"}',
        r'{\"detailUrl\":\"https:\/\/itemcdn.tmall.com\/1688offer\/exact-detail\"}',
    )

    assert _detail_document_urls(item) == [
        "https://itemcdn.tmall.com/1688offer/exact-detail"
    ]


def test_detail_image_urls_support_absolute_and_protocol_relative_assets():
    payload = r'''
        <img src=\"https:\/\/cbu01.alicdn.com\/img\/one.jpg\">
        <img data-src=\"\/\/cbu01.alicdn.com\/img\/two.webp?x=1\">
        <img src=\"https:\/\/cbu01.alicdn.com\/img\/one.jpg\">
    '''

    assert _detail_image_urls_from_text(payload) == [
        "https://cbu01.alicdn.com/img/one.jpg",
        "https://cbu01.alicdn.com/img/two.webp?x=1",
    ]
