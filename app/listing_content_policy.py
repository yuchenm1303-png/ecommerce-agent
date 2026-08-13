from __future__ import annotations

from typing import Any

from .source_bundle import normalize_key


CONTENT_POLICY_VERSION = 1

# These rules are seller-facing presentation/compliance policy only. They refine
# how an already-grounded field is expressed; they must never replace product
# evidence, live Makro options, or the existing business-field lock.
GLOBAL_CONTENT_RULES: tuple[str, ...] = (
    "Target-specific content_policy refines wording and evidence strictness only; it never overrides product evidence, conflicts, live options, qualifiers or seller/business locks.",
    "For generated seller-facing copy, use clear natural English suitable for South African ecommerce shoppers. Prefer relevance and readability over keyword stuffing or unsupported superlatives.",
    "Never inject competitor or unrelated brand names. A dedicated Brand field may contain the verified product brand, but generated Model Name, Description, Keywords and feature copy must not add brand names unless that target explicitly requires a verified brand.",
    "Do not use emoji, decorative symbols, HTML tables or table-like markup. Standard punctuation needed for model numbers, specifications, units and ranges such as USB-C, 2-in-1 or 220-240 V is allowed.",
    "Avoid medical-style claims about diagnosis, treatment, cure, prevention, disease relief or medical efficacy. When grounded, describe physical functions such as heat, massage or kneading neutrally.",
    "Never invent EAN/GTIN values, certifications, package contents, compatibility, legal/compliance facts, seller promises or search-volume claims from convention or absence.",
    "When several phrases belong in one free-text field, use concise readable wording or newline-separated phrases. When the live field is truly multi_value, obey that live value shape instead.",
    "Only describe a keyword as high-volume or competitor-validated when real Web evidence from this run supports that claim; otherwise choose keywords by product relevance only.",
)


def _names(field: dict[str, Any]) -> set[str]:
    return {
        normalize_key(field.get("attribute_key")),
        normalize_key(field.get("label")),
    } - {""}


def _matches(field: dict[str, Any], *aliases: str) -> bool:
    wanted = {normalize_key(alias) for alias in aliases}
    return bool(_names(field) & wanted)


def field_content_policy(field: dict[str, Any]) -> dict[str, Any]:
    """Return seller content policy for one live field, without product reasoning."""

    if _matches(field, "Model Name", "model_name"):
        return {
            "policy_id": "model_name",
            "generation_mode": "grounded_synthesis",
            "instruction": (
                "Write one concise English ecommerce title for South African buyer search behaviour using only "
                "grounded product type, core functions, important supported specifications and supported use-case "
                "terms. Omit brand names from this generated title. Avoid repetition, keyword stuffing and "
                "unsupported claims."
            ),
        }

    if _matches(field, "Sales Package", "sales_package"):
        return {
            "policy_id": "sales_package",
            "generation_mode": "grounded_only",
            "evidence_mode": "exact_product_only",
            "best_effort": "disabled",
            "instruction": (
                "List only items explicitly supported as included in the sold package. Never infer standard "
                "accessories or quantities from product category convention. If exact package contents are not "
                "verified, keep this field MISSING."
            ),
        }

    if _matches(field, "Description", "description"):
        return {
            "policy_id": "description",
            "generation_mode": "grounded_synthesis",
            "instruction": (
                "Write a clear English product description from grounded/resolved facts only. Explain the product "
                "purpose, core functions, supported selling points, supported usage/scenarios and compatibility only "
                "when compatibility is explicitly supported. Use readable plain text, not a table. Do not repeat "
                "conflicted facts and do not add medical-style claims."
            ),
        }

    if _matches(field, "EAN", "ean"):
        return {
            "policy_id": "ean",
            "generation_mode": "grounded_only",
            "evidence_mode": "exact_product_only",
            "best_effort": "disabled",
            "instruction": (
                "EAN is an exact identifier. Never generate, estimate or copy it from a merely similar product. "
                "Return READY only when the exact product/variant EAN is directly verified; otherwise keep MISSING."
            ),
        }

    if _matches(field, "Certifications", "Certification", "certifications", "certification"):
        return {
            "policy_id": "certifications",
            "generation_mode": "grounded_only",
            "evidence_mode": "exact_product_only",
            "best_effort": "disabled",
            "instruction": (
                "Certification/compliance claims require direct verification for the exact product/variant. Never "
                "infer certification from category convention, a family product or a comparable product. If not "
                "verified, keep MISSING."
            ),
        }

    if _matches(field, "Keywords", "Search Keywords", "keywords", "search_keywords"):
        return {
            "policy_id": "keywords",
            "generation_mode": "grounded_synthesis",
            "max_values": 5,
            "instruction": (
                "Return at most 5 relevant English backend search keywords or short phrases for South African "
                "shopping behaviour. Prefer useful synonyms, related terms and long-tail phrases that add coverage "
                "beyond already resolved front-facing title/description wording. Do not add unrelated brands and do "
                "not claim search volume unless real Web evidence supports it."
            ),
        }

    if _matches(field, "Other Features", "Other Feature", "other_features", "other_feature"):
        return {
            "policy_id": "other_features",
            "generation_mode": "grounded_synthesis",
            "instruction": (
                "Provide only additional grounded product features useful to buyers. Prefer concise feature phrases, "
                "avoid duplicating the main title/description, and leave MISSING when no additional supported feature "
                "is available."
            ),
        }

    if _matches(field, "Other Traits", "Other Trait", "other_traits", "other_trait"):
        return {
            "policy_id": "other_traits",
            "generation_mode": "grounded_synthesis",
            "instruction": (
                "Provide only additional grounded product traits useful to buyers. Prefer concise trait phrases, "
                "avoid duplicating the main title/description, and leave MISSING when no additional supported trait "
                "is available."
            ),
        }

    section = normalize_key(field.get("section_heading"))
    if section == normalize_key("Additional Description"):
        return {
            "policy_id": "optional_additional_grounded",
            "generation_mode": "grounded_context_only",
            "instruction": (
                "This is an optional Additional Description field. Do not create category-default filler merely to "
                "complete the form. Use grounded/resolved context when it supports a useful answer; otherwise MISSING "
                "is preferred."
            ),
        }

    return {}


def allow_best_effort_inference(field: dict[str, Any]) -> bool:
    policy = field_content_policy(field)
    return policy.get("best_effort") != "disabled"


def requires_exact_web_identity(field: dict[str, Any]) -> bool:
    policy = field_content_policy(field)
    return policy.get("evidence_mode") == "exact_product_only"


__all__ = [
    "CONTENT_POLICY_VERSION",
    "GLOBAL_CONTENT_RULES",
    "allow_best_effort_inference",
    "field_content_policy",
    "requires_exact_web_identity",
]
