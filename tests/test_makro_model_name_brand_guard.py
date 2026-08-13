from __future__ import annotations

import pytest

from app.makro.domain import MakroDomainAdapter
from app.resolution_types import RESOLVED, ResolvedAnswer


class _Page:
    url = (
        "https://seller.makro.co.za/index.html#dashboard/addListings/single?"
        "brand=Dexmary&vertical=air_purifier&requestId=REQ1&context=CPUI&firstDraft=1&vid=2258"
    )


def _answer(value: str) -> ResolvedAnswer:
    return ResolvedAnswer(
        attribute_key="model_name",
        label="Model Name",
        status=RESOLVED,
        answer=value,
        answer_values=[value],
    )


def test_model_name_uses_committed_listing_brand_when_packet_brand_is_missing() -> None:
    adapter = MakroDomainAdapter(_Page())
    original = _answer("Dexmary Air Purifier")
    field = {"attribute_key": "model_name", "label": "Model Name"}

    constrained = adapter._constrained_execution_answer(field, original)

    assert constrained.answer == "Air Purifier"
    assert constrained.answer_values == ["Air Purifier"]
    assert original.answer == "Dexmary Air Purifier"
    assert original.answer_values == ["Dexmary Air Purifier"]


def test_model_name_brand_only_fails_closed() -> None:
    adapter = MakroDomainAdapter(_Page())
    field = {"attribute_key": "model_name", "label": "Model Name"}

    with pytest.raises(RuntimeError, match="去除当前 Brand 后为空"):
        adapter._constrained_execution_answer(field, _answer("Dexmary"))


def test_non_model_field_is_untouched() -> None:
    adapter = MakroDomainAdapter(_Page())
    original = _answer("Dexmary Air Purifier")
    field = {"attribute_key": "sales_package", "label": "Sales Package"}

    assert adapter._constrained_execution_answer(field, original) is original
