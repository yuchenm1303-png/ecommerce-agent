from __future__ import annotations

from typing import Any

from .answer_resolver import BUSINESS_ATTRIBUTE_ALIASES
from .evidence_contract import ProductIdentity
from .qa_catalog import QuestionCatalog
from .source_bundle import normalize_key


_BUSINESS_NAMES = {
    normalize_key(name)
    for key, aliases in BUSINESS_ATTRIBUTE_ALIASES.items()
    for name in (key, *aliases)
}


def _is_business_question(question: str) -> bool:
    return normalize_key(question) in _BUSINESS_NAMES


def build_extraction_request_payload(
    catalog: QuestionCatalog,
    *,
    identity: ProductIdentity = ProductIdentity(),
    image_paths: tuple[str, ...] = (),
    product_url: str | None = None,
    supplemental_text: str = "",
) -> dict[str, Any]:
    """Create the provider-neutral input contract for image/web/AI extraction.

    The downstream extractor receives the exact customer question list rather
    than being asked to produce an unconstrained generic product description.
    """

    questions = []
    for item in catalog.questions:
        questions.append(
            {
                "number": item.number,
                "question": item.question,
                "explanation": item.explanation,
                "category": item.category,
                "options": list(item.options),
                "unit": item.unit,
                "already_answered": item.has_answer,
                "business_locked": _is_business_question(item.question),
            }
        )

    return {
        "task": "extract_source_grounded_product_facts",
        "product_identity": {
            "sku": identity.sku,
            "model_number": identity.model_number,
            "brand": identity.brand,
        },
        "sources": {
            "images": list(image_paths),
            "product_url": product_url,
            "supplemental_text": supplemental_text,
        },
        "questions": questions,
        "rules": [
            "Answer only when the supplied source contains direct evidence for the fact.",
            "Do not guess missing product specifications.",
            "Do not answer business_locked questions from images, websites, or AI synthesis.",
            "For dropdown questions, prefer one of the supplied options only when evidence supports it.",
            "Every returned fact must include source_type, source_reference, evidence_text, and confidence 0..1.",
            "If sources disagree, return separate facts rather than reconciling them silently.",
            "If the observed product identity conflicts with the expected identity, report the observed identity and no speculative merge.",
        ],
        "required_output_shape": {
            "extractor": "string",
            "product_identity": {"sku": "string", "model_number": "string", "brand": "string"},
            "facts": [
                {
                    "key": "exact question or explicit canonical fact key",
                    "aliases": ["optional exact equivalent question labels"],
                    "value": "string or string[]",
                    "source_type": "manufacturer_doc|supplier_doc|product_image|official_doc|official_web|supplier_web|ai_synthesis",
                    "source_reference": "precise source/page/image/URL reference",
                    "confidence": 0.0,
                    "evidence_text": "short source-grounded evidence snippet/visual description",
                    "note": "optional",
                }
            ],
            "warnings": ["string"],
        },
    }
