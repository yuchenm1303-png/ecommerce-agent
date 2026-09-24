from __future__ import annotations

from .capabilities import ModelCapability
from .roles import ModelRole


AGENT_FAST_PROFILE_ID = "agent.fast"
AGENT_REASONING_PROFILE_ID = "agent.reasoning"
AGENT_VISION_PROFILE_ID = "agent.vision"


AGENT_FAST_ROLE = ModelRole(
    role_id=AGENT_FAST_PROFILE_ID,
    required_capabilities=frozenset(
        {
            ModelCapability.TEXT,
            ModelCapability.TOOL_CALLING,
            ModelCapability.STREAMING,
        }
    ),
    allow_fallback=False,
)

AGENT_REASONING_ROLE = ModelRole(
    role_id=AGENT_REASONING_PROFILE_ID,
    required_capabilities=frozenset(
        {
            ModelCapability.TEXT,
            ModelCapability.TOOL_CALLING,
            ModelCapability.STREAMING,
            ModelCapability.REASONING,
        }
    ),
    allow_fallback=False,
)

AGENT_VISION_ROLE = ModelRole(
    role_id=AGENT_VISION_PROFILE_ID,
    required_capabilities=frozenset(
        {
            ModelCapability.TEXT,
            ModelCapability.TOOL_CALLING,
            ModelCapability.STREAMING,
            ModelCapability.VISION,
        }
    ),
    allow_fallback=False,
)


__all__ = [
    "AGENT_FAST_PROFILE_ID",
    "AGENT_FAST_ROLE",
    "AGENT_REASONING_PROFILE_ID",
    "AGENT_REASONING_ROLE",
    "AGENT_VISION_PROFILE_ID",
    "AGENT_VISION_ROLE",
]
