"""Optional OpenAI-style Chat Completions adapter."""

from portable_memory_engine.adapters.openai_compatible.config import (
    OpenAICompatibleChatConfig,
    StructuredOutputCapability,
    StructuredOutputMode,
)
from portable_memory_engine.adapters.openai_compatible.model import OpenAICompatibleChatModel

__all__ = (
    "OpenAICompatibleChatConfig",
    "OpenAICompatibleChatModel",
    "StructuredOutputCapability",
    "StructuredOutputMode",
)
