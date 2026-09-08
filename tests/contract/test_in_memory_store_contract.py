"""Run every reusable storage contract against the public in-memory adapter."""

from __future__ import annotations

import pytest

from portable_memory_engine.adapters import InMemoryMemoryStore
from portable_memory_engine.ports import MemoryStore
from portable_memory_engine.testing.contracts import (
    MemoryStoreContractFactory,
    MemoryStoreContractSuite,
)


@pytest.fixture
def memory_store_factory() -> MemoryStoreContractFactory:
    """Provide a fresh unopened public adapter for each inherited contract."""

    return InMemoryMemoryStore


class TestInMemoryMemoryStoreContract(MemoryStoreContractSuite):
    """Apply the complete v0.1 contract suite without overrides."""


def test_in_memory_store_satisfies_the_runtime_protocol() -> None:
    assert isinstance(InMemoryMemoryStore(), MemoryStore)
