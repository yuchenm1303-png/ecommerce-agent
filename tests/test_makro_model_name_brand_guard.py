from __future__ import annotations

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
        source_type="ai_decision",
    )


def test_model_name_execution_preserves_ai_value_even_when_listing_brand_matches() -> None:
    adapter = MakroDomainAdapter(_Page())
    original = _answer("Dexmary Air Purifier")
    field = {"attribute_key": "model_name", "label": "Model Name"}

    constrained = adapter._constrained_execution_answer(field, original)

    assert constrained.answer == "Dexmary Air Purifier"
    assert constrained.answer_values == ["Dexmary Air Purifier"]
    assert original.answer == "Dexmary Air Purifier"
    assert original.answer_values == ["Dexmary Air Purifier"]


def test_model_name_brand_only_is_not_downgraded_by_python() -> None:
    adapter = MakroDomainAdapter(_Page())
    original = _answer("Dexmary")
    field = {"attribute_key": "model_name", "label": "Model Name"}

    constrained = adapter._constrained_execution_answer(field, original)

    assert constrained.answer == "Dexmary"
    assert constrained.answer_values == ["Dexmary"]


def test_non_model_field_is_untouched() -> None:
    adapter = MakroDomainAdapter(_Page())
    original = _answer("Dexmary Air Purifier")
    field = {"attribute_key": "sales_package", "label": "Sales Package"}

    assert adapter._constrained_execution_answer(field, original) is original
