"""Provider-agnostic AI gateway foundation.

This package intentionally does not own business prompts or Agent logic.
It only provides a stable model-provider boundary.
"""

from .gateway import AIGateway
from .models import AIRequest, AIResponse, ModelCapability

__all__ = ["AIGateway", "AIRequest", "AIResponse", "ModelCapability"]
