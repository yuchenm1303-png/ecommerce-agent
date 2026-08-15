from __future__ import annotations

import hashlib
import os
from typing import Any

from .source_bundle import normalize_key


LISTING_INTENT_ENV = "ECOMMERCE_LISTING_INTENT"
LISTING_AI_GUIDANCE_ENV = "ECOMMERCE_LISTING_AI_GUIDANCE"
MODEL_NAME_KEYWORDS_ENV = "ECOMMERCE_MODEL_NAME_KEYWORDS"
_BASE_CONTENT_POLICY_VERSION = 5


def _env_text(name: str, *, limit: int) -> str:
    raw = str(os.getenv(name, "") or "")
    compact = " ".join(raw.split()).strip()
    return compact[: max(1, int(limit))]


def current_listing_intent() -> str:
    """Return the explicit seller-selected listing scope for this process.

    The value is optional and is intentionally *not* a product identifier. It is
    a per-run offer/variant instruction such as ``Black purifier + 2 fragrance
    oils``. GUI Single and every Batch worker receive their own process-local
    value so concurrent jobs cannot leak scope into one another.
    """

    return _env_text(LISTING_INTENT_ENV, limit=600)


def current_listing_ai_guidance() -> str:
    """Return optional user guidance for AI wording/emphasis in this run.

    Guidance is deliberately soft. It may help choose emphasis among supported
    interpretations, but it never becomes evidence and can never override source
    facts, conflicts, exact identifiers, brand/compliance data or live Makro
    constraints.
    """

    return _env_text(LISTING_AI_GUIDANCE_ENV, limit=1000)


def current_model_name_keywords() -> str:
    """Return optional candidate search terms for Model Name synthesis only."""

    return _env_text(MODEL_NAME_KEYWORDS_ENV, limit=500)


def _policy_version() -> str:
    # Product-fact caching includes CONTENT_POLICY_VERSION. Include all per-run
    # seller guidance so the same supplier URL cannot reuse answers generated for
    # a different sold offer, AI emphasis or Model Name keyword brief.
    payload = "\n".join(
        (
            current_listing_intent(),
            current_listing_ai_guidance(),
            current_model_name_keywords(),
        )
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12] if payload else "none"
    return f"{_BASE_CONTENT_POLICY_VERSION}:{digest}"


CONTENT_POLICY_VERSION = _policy_version()


# These rules are seller-facing presentation/compliance policy only. They refine
# how an already-grounded field is expressed; they must never replace product
# evidence, live Makro options, or the existing business-field lock.
_GLOBAL_BASE_RULES: tuple[str, ...] = (
    "Target-specific content_policy refines wording and evidence strictness only; it never overrides product evidence, conflicts, live options, qualifiers or seller/business locks.",
    "For generated seller-facing copy, use clear natural English suitable for South African ecommerce shoppers. Prefer relevance and readability over keyword stuffing or unsupported superlatives.",
    "Never inject competitor or unrelated brand names. A dedicated Brand field may contain the verified product brand, but generated Model Name, Description, Keywords and feature copy must not add brand names unless that target explicitly requires a verified brand.",
    "Do not use emoji, decorative symbols, HTML tables or table-like markup. Standard punctuation needed for model numbers, specifications, units and ranges such as USB-C, 2-in-1 or 220-240 V is allowed.",
    "Avoid medical-style claims about diagnosis, treatment, cure, prevention, disease relief or medical efficacy. When grounded, describe physical functions such as heat, massage or kneading neutrally.",
    "Never invent EAN/GTIN values, certifications, package contents, compatibility, legal/compliance facts, seller promises or search-volume claims from convention or absence.",
    "When several phrases belong in one ordinary free-text field, use concise readable wording or newline-separated phrases. For every live field with multi_value=true, return each independent value as a separate element of the decision values array; never join distinct values with commas, semicolons, '+', 'and', or newlines just to fit one input. The Makro execution layer will create one live row per value when the field exposes its + control.",
    "Only describe a keyword as high-volume or competitor-validated when real Web evidence from this run supports that claim; otherwise choose keywords by product relevance only.",
)

_intent = current_listing_intent()
_INTENT_RULES: tuple[str, ...] = (
    (
        "The seller explicitly selected this listing offer/variant scope for the current run: "
        + repr(_intent)
        + ". Use it only to disambiguate the sold colour/variant/bundle/quantity among supplier-supported choices. "
        "It is authoritative for what this listing is intended to sell, but it must not create unrelated product specifications or override a real contradiction."
    ),
) if _intent else ()

_ai_guidance = current_listing_ai_guidance()
_AI_GUIDANCE_RULES: tuple[str, ...] = (
    (
        "Optional seller AI guidance for this run: "
        + repr(_ai_guidance)
        + ". Treat it only as a soft instruction for emphasis, wording and selection among interpretations already supported by grounded evidence. "
        "It is not product evidence and must never create or override specifications, package contents, identifiers, brand, compliance facts, live options, qualifiers or seller/business locks."
    ),
) if _ai_guidance else ()

GLOBAL_CONTENT_RULES: tuple[str, ...] = _GLOBAL_BASE_RULES + _INTENT_RULES + _AI_GUIDANCE_RULES


def _names(field: dict[str, Any]) -> set[str]:
    return {
        normalize_key(field.get("attribute_key")),
        normalize_key(field.get("label")),
    } - {""}


def _matches(field: dict[str, Any], *aliases: str) -> bool:
    wanted = {normalize_key(alias) for alias in aliases}
    return bool(_names(field) & wanted)


def _title_contributor(field: dict[str, Any]) -> bool:
    if bool(field.get("title_attribute") or field.get("contributes_to_title")):
        return True
    context = " ".join(
        str(field.get(key) or "")
        for key in ("context_text", "help_text")
    ).casefold()
    return "make up title" in context or "generated title" in context


def _with_intent(policy: dict[str, Any]) -> dict[str, Any]:
    intent = current_listing_intent()
    if not intent:
        return policy
    return {**policy, "listing_intent": intent}


def _with_ai_guidance(policy: dict[str, Any]) -> dict[str, Any]:
    guidance = current_listing_ai_guidance()
    if not guidance:
        return policy
    return {**policy, "ai_guidance": guidance}


def _with_user_context(policy: dict[str, Any]) -> dict[str, Any]:
    return _with_ai_guidance(_with_intent(policy))


def _sales_package_value_shape(field: dict[str, Any]) -> dict[str, str]:
    if bool(field.get("multi_value")):
        return {
            "value_shape": "one_package_item_per_value",
            "value_format": "<quantity> x <concise item name>",
            "shape_instruction": (
                "This live Sales Package field is multi-value. Return one delivered physical item per values[] "
                "element so Makro can put each item on its own row. Use '<quantity> x <concise item name>' when the "
                "listing intent or exact product evidence establishes the quantity, for example '1 x Inflatable "
                "Pool', '1 x Electric Air Pump', '1 x Instruction Manual'. Never combine separate package items in "
                "one values[] element with commas, semicolons, '+', 'and', or newlines. Keep model/specification text "
                "that belongs to one physical item inside that same element. Do not invent a quantity."
            ),
        }
    return {
        "value_shape": "single_package_string",
        "value_format": "concise package contents",
        "shape_instruction": (
            "This live Sales Package field is single-value. Return one concise string containing only the grounded "
            "package contents; keep exact quantities when known and separate distinct items with '; '."
        ),
    }


def field_content_policy(field: dict[str, Any]) -> dict[str, Any]:
    """Return seller content policy for one live field, without product reasoning."""

    if _matches(field, "Vehicle Model Name", "vehicle_model_name"):
        return _with_user_context(
            {
                "policy_id": "vehicle_model_name",
                "generation_mode": "grounded_only",
                "evidence_mode": "exact_product_only",
                "best_effort": "disabled",
                "required_fallback": "manual_only",
                "instruction": (
                    "Fill Vehicle Model Name only from exact selected-offer supplier evidence or exact-product Web evidence. "
                    "This field describes compatible vehicle model information; do not copy the product Model Number, product Model Name or Brand into it. "
                    "Use 'Universal' only when exact evidence explicitly establishes universal vehicle compatibility. "
                    "Never output N/A, None or a guessed vehicle model. If compatibility is not verified, keep MISSING."
                ),
            }
        )

    if _matches(field, "Model Name", "model_name"):
        model_keywords = current_model_name_keywords()
        keyword_instruction = ""
        if model_keywords:
            keyword_instruction = (
                " The seller supplied these candidate Model Name search terms: "
                + repr(model_keywords)
                + ". Use only terms that are genuinely relevant to this exact product and supported by the grounded evidence. "
                "Blend useful terms naturally into the title, omit irrelevant or unsupported terms, do not add brands, and never keyword-stuff."
            )
        policy: dict[str, Any] = {
            "policy_id": "model_name",
            "generation_mode": "grounded_synthesis",
            "required_fallback": "manual_only",
            "instruction": (
                "Write one concise English ecommerce title attribute for South African buyer search behaviour "
                "using the selected listing intent when present plus grounded product type, core functions, "
                "important supported specifications and supported use-case terms. Omit brand names from this "
                "generated title. Never output N/A, None or a numeric placeholder for Model Name. Avoid "
                "repetition, keyword stuffing and unsupported claims."
                + keyword_instruction
            ),
        }
        if model_keywords:
            policy["model_name_candidate_keywords"] = model_keywords
        return _with_user_context(policy)

    if _matches(field, "Sales Package", "sales_package"):
        intent = current_listing_intent()
        shape = _sales_package_value_shape(field)
        if intent:
            return _with_user_context(
                {
                    "policy_id": "sales_package",
                    "generation_mode": "grounded_synthesis",
                    "evidence_mode": "exact_product_only",
                    "best_effort": "listing_intent_allowed",
                    "required_fallback": "manual_only",
                    **shape,
                    "instruction": (
                        "Describe what the buyer receives for this exact selected listing offer. Treat listing_intent "
                        "as the seller's explicit sold bundle/variant scope. List the units/items named by that intent "
                        "and reconcile them with exact supplier evidence. Add a base accessory only when resolved or "
                        "exact-product evidence explicitly shows it is included with the selected variant. Never infer "
                        "standard accessories or quantities from category convention. Never output N/A. "
                        + shape["shape_instruction"]
                    ),
                }
            )
        return _with_ai_guidance(
            {
                "policy_id": "sales_package",
                "generation_mode": "grounded_only",
                "evidence_mode": "exact_product_only",
                "best_effort": "disabled",
                "required_fallback": "manual_only",
                **shape,
                "instruction": (
                    "List only items explicitly supported as included in the sold package. Never infer standard "
                    "accessories or quantities from product category convention. If exact package contents are not "
                    "verified, keep this field MISSING. Never use N/A as Sales Package. "
                    + shape["shape_instruction"]
                ),
            }
        )

    if _matches(field, "Description", "description"):
        return _with_user_context(
            {
                "policy_id": "description",
                "generation_mode": "grounded_synthesis",
                "instruction": (
                    "Write a clear English product description for the selected listing offer from grounded/resolved "
                    "facts only. Explain the product purpose, core functions, supported selling points, supported "
                    "usage/scenarios and compatibility only when compatibility is explicitly supported. Use readable "
                    "plain text, not a table. Do not repeat conflicted facts and do not add medical-style claims."
                ),
            }
        )

    if _matches(field, "EAN", "ean"):
        return {
            "policy_id": "ean",
            "generation_mode": "grounded_only",
            "evidence_mode": "exact_product_only",
            "best_effort": "disabled",
            "required_fallback": "manual_only",
            "instruction": (
                "EAN is an exact identifier. Never generate, estimate, use N/A, or copy it from a merely similar "
                "product. Return READY only when the exact product/variant EAN is directly verified; otherwise keep "
                "MISSING."
            ),
        }

    if _matches(field, "Certifications", "Certification", "certifications", "certification"):
        return {
            "policy_id": "certifications",
            "generation_mode": "grounded_only",
            "evidence_mode": "exact_product_only",
            "best_effort": "disabled",
            "required_fallback": "manual_only",
            "instruction": (
                "Certification/compliance claims require direct verification for the exact product/variant. Never "
                "infer certification from category convention, a family product or a comparable product, and never "
                "use N/A merely to satisfy a required field. If not verified, keep MISSING."
            ),
        }

    if _matches(field, "Keywords", "Search Keywords", "keywords", "search_keywords"):
        return _with_user_context(
            {
                "policy_id": "keywords",
                "generation_mode": "grounded_synthesis",
                "max_values": 5,
                "instruction": (
                    "Return at most 5 relevant English backend search keywords or short phrases for South African "
                    "shopping behaviour and this selected listing offer. Prefer useful synonyms, related terms and "
                    "long-tail phrases that add coverage beyond already resolved front-facing title/description "
                    "wording. Do not add unrelated brands and do not claim search volume unless real Web evidence "
                    "supports it."
                ),
            }
        )

    if _matches(field, "Other Features", "Other Feature", "other_features", "other_feature"):
        return _with_user_context(
            {
                "policy_id": "other_features",
                "generation_mode": "grounded_synthesis",
                "instruction": (
                    "Provide only additional grounded product features useful to buyers for the selected listing "
                    "offer. Prefer concise feature phrases, avoid duplicating the main title/description, and leave "
                    "MISSING when no additional supported feature is available."
                ),
            }
        )

    if _matches(field, "Other Traits", "Other Trait", "other_traits", "other_trait"):
        return _with_user_context(
            {
                "policy_id": "other_traits",
                "generation_mode": "grounded_synthesis",
                "instruction": (
                    "Provide only additional grounded product traits useful to buyers for the selected listing "
                    "offer. Prefer concise trait phrases, avoid duplicating the main title/description, and leave "
                    "MISSING when no additional supported trait is available."
                ),
            }
        )

    if _title_contributor(field):
        return _with_user_context(
            {
                "policy_id": "title_contributor",
                "generation_mode": "grounded_context_only",
                "required_fallback": "manual_only",
                "instruction": (
                    "This live Makro attribute can contribute to the generated product title. Keep it accurate and "
                    "specific to the selected offer. Never use N/A, None, 0, 1 or an arbitrary option merely to make "
                    "the title complete."
                ),
            }
        )

    section = normalize_key(field.get("section_heading"))
    if section == normalize_key("Additional Description"):
        return _with_user_context(
            {
                "policy_id": "optional_additional_grounded",
                "generation_mode": "grounded_context_only",
                "instruction": (
                    "This is an optional Additional Description field. Do not create category-default filler merely "
                    "to complete the form. Use grounded/resolved context for the selected listing offer when it "
                    "supports a useful answer; otherwise MISSING is preferred."
                ),
            }
        )

    intent = current_listing_intent()
    guidance = current_listing_ai_guidance()
    if intent or guidance:
        policy: dict[str, Any] = {
            "policy_id": "user_listing_context",
            "generation_mode": "grounded_context_only",
            "instruction": (
                "Use user-provided listing context only inside the boundaries of grounded supplier evidence and the live Makro field contract. "
                "Do not infer unmentioned specifications, identifiers, brands, compliance facts or package contents from guidance alone."
            ),
        }
        return _with_user_context(policy)

    return {}


def allow_best_effort_inference(field: dict[str, Any]) -> bool:
    policy = field_content_policy(field)
    return policy.get("best_effort") != "disabled"


def allow_required_fallback(field: dict[str, Any]) -> bool:
    """Keep final deterministic completion available for every required field.

    ``required_fallback=manual_only`` remains useful metadata for the Resolver: it
    tells AI/search stages not to invent a product fact or use a placeholder as a
    normal answer. It must not become an execution lock. Only after the normal
    evidence + synthesis pipeline has finished unresolved does the shared
    required-field layer apply its deterministic live-schema fallback so Single
    and Batch can continue through the canonical executor.
    """

    del field
    return True


def requires_exact_web_identity(field: dict[str, Any]) -> bool:
    policy = field_content_policy(field)
    return policy.get("evidence_mode") == "exact_product_only"


__all__ = [
    "CONTENT_POLICY_VERSION",
    "GLOBAL_CONTENT_RULES",
    "LISTING_AI_GUIDANCE_ENV",
    "LISTING_INTENT_ENV",
    "MODEL_NAME_KEYWORDS_ENV",
    "allow_best_effort_inference",
    "allow_required_fallback",
    "current_listing_ai_guidance",
    "current_listing_intent",
    "current_model_name_keywords",
    "field_content_policy",
    "requires_exact_web_identity",
]
