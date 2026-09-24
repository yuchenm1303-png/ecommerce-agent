from __future__ import annotations

from .capabilities import ModelCapability
from .credentials import CredentialRef
from .profiles import ModelProfile
from .roles import ModelRole


LISTING_SEMANTIC_PROFILE_ID = "listing.semantic"
LISTING_IDENTITY_PROFILE_ID = "listing.identity"
LISTING_ATTRIBUTES_PROFILE_ID = "listing.attributes"
LISTING_VISION_PROFILE_ID = "listing.vision"
LISTING_WEB_RESEARCH_PROFILE_ID = "listing.web_research"


LISTING_SEMANTIC_ROLE = ModelRole(
    role_id=LISTING_SEMANTIC_PROFILE_ID,
    required_capabilities=frozenset(
        {
            ModelCapability.TEXT,
            ModelCapability.STRUCTURED_OUTPUT,
            ModelCapability.VISION,
        }
    ),
    allow_fallback=False,
)

LISTING_IDENTITY_ROLE = ModelRole(
    role_id=LISTING_IDENTITY_PROFILE_ID,
    required_capabilities=frozenset(
        {
            ModelCapability.TEXT,
            ModelCapability.STRUCTURED_OUTPUT,
            ModelCapability.VISION,
        }
    ),
    allow_fallback=False,
)

LISTING_ATTRIBUTES_ROLE = ModelRole(
    role_id=LISTING_ATTRIBUTES_PROFILE_ID,
    required_capabilities=frozenset(
        {
            ModelCapability.TEXT,
            ModelCapability.STRUCTURED_OUTPUT,
            ModelCapability.VISION,
        }
    ),
    allow_fallback=False,
)

LISTING_VISION_ROLE = ModelRole(
    role_id=LISTING_VISION_PROFILE_ID,
    required_capabilities=frozenset(
        {
            ModelCapability.TEXT,
            ModelCapability.STRUCTURED_OUTPUT,
            ModelCapability.VISION,
        }
    ),
    allow_fallback=False,
)

LISTING_WEB_RESEARCH_ROLE = ModelRole(
    role_id=LISTING_WEB_RESEARCH_PROFILE_ID,
    required_capabilities=frozenset(
        {
            ModelCapability.TEXT,
            ModelCapability.STRUCTURED_OUTPUT,
            ModelCapability.WEB_SEARCH,
        }
    ),
    allow_fallback=False,
)


def listing_semantic_profile(
    *,
    provider: str,
    model: str,
    credential_ref: CredentialRef | None = None,
) -> ModelProfile:
    """Compatibility profile for the existing detached Listing AI foundation.

    The current production Listing pipeline is intentionally not connected to
    ``app.ai``. This helper only describes the already-known capability contract
    of the current semantic provider family for future migration work.
    """

    return LISTING_SEMANTIC_ROLE.bind(
        provider=provider,
        model=model,
        capabilities=(
            ModelCapability.TEXT,
            ModelCapability.STRUCTURED_OUTPUT,
            ModelCapability.VISION,
        ),
        credential_ref=credential_ref,
    )


__all__ = [
    "LISTING_ATTRIBUTES_PROFILE_ID",
    "LISTING_ATTRIBUTES_ROLE",
    "LISTING_IDENTITY_PROFILE_ID",
    "LISTING_IDENTITY_ROLE",
    "LISTING_SEMANTIC_PROFILE_ID",
    "LISTING_SEMANTIC_ROLE",
    "LISTING_VISION_PROFILE_ID",
    "LISTING_VISION_ROLE",
    "LISTING_WEB_RESEARCH_PROFILE_ID",
    "LISTING_WEB_RESEARCH_ROLE",
    "listing_semantic_profile",
]
