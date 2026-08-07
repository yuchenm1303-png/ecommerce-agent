from app.matcher import match_answer


def test_exact_match():
    result = match_answer("品牌", {"品牌": "LumaTech"})
    assert result is not None
    assert result.answer == "LumaTech"
    assert result.strategy == "exact"


def test_alias_match():
    result = match_answer("Brand", {"品牌": "LumaTech"})
    assert result is not None
    assert result.answer == "LumaTech"
    assert result.strategy == "alias"


def test_unknown_field_is_not_guessed():
    result = match_answer("功率因数", {"额定功率": "60W"})
    assert result is None
