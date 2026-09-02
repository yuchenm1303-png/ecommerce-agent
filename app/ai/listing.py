from __future__ import annotations

from .capabilities import ModelCapability
from .profiles import ModelProfile


LISTING_SEMANTIC_PROFILE_ID = "listing.semantic"


def listing_semantic_profile(*, provider: str, model: str) -> ModelProfile:
    """Pinned profile for the existing deterministic Listing AI pipeline.

    Both current OpenAI and OpenAI-compatible semantic adapters accept text,
    grounded images and strict/prompt-validated JSON tasks. No tool calling,
    web-search, generic reasoning or fallback capability is claimed here.
    """

    return ModelProfile(
        profile_id=LISTING_SEMANTIC_PROFILE_ID,
        provider=provider,
        model=model,
        capabilities=frozenset(
            {
                ModelCapability.TEXT,
                ModelCapability.STRUCTURED_OUTPUT,
                ModelCapability.VISION,
            }
        ),
        allow_fallback=False,
    )


__all__ = ["LISTING_SEMANTIC_PROFILE_ID", "listing_semantic_profile"]
