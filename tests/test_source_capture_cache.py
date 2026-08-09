from app.source_capture import _source_cache_key


def test_source_cache_key_ignores_query_tracking_noise():
    clean = "https://detail.1688.com/offer/850845635717.html"
    tracked = clean + "?spm=a2615.2177701.autotrace-offerGeneral.1&from=market"
    assert _source_cache_key(clean) == _source_cache_key(tracked)


def test_source_cache_key_changes_for_different_offer():
    first = "https://detail.1688.com/offer/850845635717.html"
    second = "https://detail.1688.com/offer/850845635718.html"
    assert _source_cache_key(first) != _source_cache_key(second)
