from gui.batch_source_outcome import source_media_review


def test_exhausted_source_media_routes_to_review():
    review = source_media_review(
        {
            "outcome": "review",
            "failure_kind": "LISTING_MEDIA_UNAVAILABLE",
            "failure_reason": "all candidates rejected",
        }
    )
    assert review == ("Source 图片验收", "all candidates rejected", "缺少可提交商品图片")


def test_unrelated_source_failure_remains_generic_failure():
    assert source_media_review({"failure_kind": "SOURCE_CAPTURE_FAILED"}) is None
