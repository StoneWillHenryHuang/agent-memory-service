"""Dependency-free reference adapters available in the base package."""

from portable_memory_engine.adapters.memory import InMemoryMemoryStore
from portable_memory_engine.adapters.testing import (
    DeterministicEmbedder,
    DeterministicIdGenerator,
    FixedClock,
    ScriptedChatModel,
)

__all__ = (
    "DeterministicEmbedder",
    "DeterministicIdGenerator",
    "FixedClock",
    "InMemoryMemoryStore",
    "ScriptedChatModel",
)
