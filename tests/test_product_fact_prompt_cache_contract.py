from __future__ import annotations

from app.providers.openai_compatible import _prompt_payload


def _request(task: str) -> dict:
    return {
        "task": task,
        "product_identity": {"source_product_url": "https://supplier.test/item"},
        "context": {},
        "target_fields": [{"field_id": "mf_one", "label": "Field One"}],
        "all_marketplace_fields": [],
        "rules": ["ground every answer"],
        "grounded_sources": [
            {
                "source_id": "compact:web",
                "source_type": "compact_product_evidence",
                "kind": "text",
                "origin": "https://supplier.test/item",
                "content": "[s1] stable evidence block",
            }
        ],
        "json_contract": {"type": "object", "properties": {"facts": {"type": "array"}}},
    }


def test_product_fact_prompt_keeps_stable_evidence_before_batch_specific_targets() -> None:
    request = _request("resolve_compact_product_facts")
    payload = _prompt_payload(request)
    keys = list(payload)

    assert keys.index("rules") < keys.index("grounded_sources")
    assert keys.index("grounded_sources") < keys.index("target_fields")
    assert keys.index("target_fields") < keys.index("json_contract")
    assert payload["grounded_sources"][0]["content"] == "[s1] stable evidence block"
    assert payload["target_fields"] == request["target_fields"]
    assert payload["json_contract"] == request["json_contract"]


def test_non_product_fact_tasks_keep_historical_prompt_order() -> None:
    request = _request("generic_json_task")
    keys = list(_prompt_payload(request))

    assert keys.index("target_fields") < keys.index("grounded_sources")
