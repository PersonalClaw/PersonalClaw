"""Capability descriptors for provider types.

This module is loaded as a side effect of importing ``personalclaw.llm``
and MUST NOT import any provider SDKs (``anthropic``, ``openai``,
``httpx``). Property 11 (Provider SDK Lazy Import) depends on this guarantee.
"""

from dataclasses import dataclass
from enum import Enum


class Capability(str, Enum):
    """Static capability flags advertised by a provider type."""

    CHAT = "chat"
    CODE_TOOLS = "code_tools"
    SUMMARIZATION = "summarization"
    PLANNING = "planning"
    EMBEDDING = "embedding"
    VISION = "vision"
    STREAMING = "streaming"
    TOOL_APPROVAL = "tool_approval"


@dataclass(frozen=True)
class ProviderCapability:
    """Static descriptor of what a provider type can do."""

    type: str  # "openai", "anthropic", ...
    capabilities: frozenset[Capability]
    supports_streaming: bool
    supports_tools: bool
    supports_embeddings: bool
    supports_vision: bool
    max_context_tokens: int  # 0 == unknown / model-dependent
    notes: str = ""
