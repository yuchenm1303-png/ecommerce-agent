import pytest

from app.platforms.makro import MAKRO_HOST, parse_makro_listing_url


def test_parse_makro_listing_url() -> None:
    url = (
        "https://seller.makro.co.za/index.html#dashboard/addListings/single?"
        "brand=experimental&vertical=sports_action_camera&requestId=REQ123&vid=847"
    )
    target = parse_makro_listing_url(url)

    assert target.brand == "experimental"
    assert target.vertical == "sports_action_camera"
    assert target.request_id == "REQ123"
    assert target.vid == "847"


def test_reject_non_makro_host() -> None:
    with pytest.raises(ValueError, match=MAKRO_HOST):
        parse_makro_listing_url(
            "https://example.com/index.html#dashboard/addListings/single?vertical=x"
        )


def test_reject_wrong_makro_route() -> None:
    with pytest.raises(ValueError, match="Add a Single Listing"):
        parse_makro_listing_url("https://seller.makro.co.za/index.html#dashboard")
