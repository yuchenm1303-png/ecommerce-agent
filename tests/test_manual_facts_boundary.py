from __future__ import annotations

import json

import pytest

from app.evidence_pipeline import bundle_from_facts_json


def test_facts_json_rejects_image_web_or_ai_source_types(tmp_path):
    for source_type in ("product_image", "supplier_web", "supplier_doc", "ai_synthesis"):
        path = tmp_path / f"{source_type}.json"
        path.write_text(
            json.dumps(
                {
                    "facts": [
                        {
                            "key": "Image Resolution",
                            "value": "1920x1080",
                            "source_type": source_type,
                            "source_reference": "external:spec",
                            "confidence": 0.99,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="--evidence-packet"):
            bundle_from_facts_json(path)
