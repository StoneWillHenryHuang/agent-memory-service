"""Tests for capabilities and structural port contracts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from portable_memory_engine.domain import (
    CapabilityError,
    DomainValidationError,
    MemoryScope,
    MemorySubject,
)
from portable_memory_engine.ports import (
    MANDATORY_WRITE_CAPABILITIES,
    STORAGE_CONTRACT_VERSION,
    AccessDecision,
    AccessOperation,
    AccessPolicy,
    AccessRequest,
    AsyncLifecycle,
    ChatModel,
    Clock,
    Embedder,
    HealthState,
    HealthStatus,
    IdentifierPurpose,
    IdGenerator,
    MemoryStore,
    Observer,
    PromptProvider,
    StorageCapabilities,
    StorageCapability,
)


def test_storage_capabilities_cover_every_declared_flag() -> None:
    capabilities = StorageCapabilities(
        vector_search=True,
        atomic_compare_and_swap=True,
        atomic_idempotency=True,
        freshness_watermarks=True,
        deletion_barriers=True,
        metadata_filtering=True,
        transactions=True,
        exact_total_count=True,
        subject_deletion=True,
        session_contributions=True,
    )

    assert capabilities.contract_version == STORAGE_CONTRACT_VERSION
    assert all(capabilities.supports(capability) for capability in StorageCapability)
    capabilities.require(operation="write", capabilities=MANDATORY_WRITE_CAPABILITIES)


def test_storage_capabilities_fail_fast_without_fallback() -> None:
    capabilities = StorageCapabilities()

    assert capabilities.supports(StorageCapability.VECTOR_SEARCH) is False
    with pytest.raises(CapabilityError) as captured:
        capabilities.require(
            operation="semantic_recall",
            capabilities=(StorageCapability.VECTOR_SEARCH,),
        )
    assert captured.value.operation == "semantic_recall"
    assert captured.value.capability == "vector_search"


def test_storage_capability_and_health_values_reject_ambiguous_data() -> None:
    assert HealthStatus(HealthState.HEALTHY).detail_code is None
    assert HealthStatus(HealthState.UNHEALTHY, "connection_failed").state is HealthState.UNHEALTHY
    with pytest.raises(DomainValidationError, match="contract version"):
        StorageCapabilities(contract_version="2.0")
    with pytest.raises(DomainValidationError, match="boolean"):
        StorageCapabilities(vector_search=1)  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError, match="StorageCapability"):
        StorageCapabilities().supports("vector_search")  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError, match="detail_code"):
        HealthStatus(HealthState.UNHEALTHY, "private detail")


def test_access_values_are_transport_neutral() -> None:
    request = AccessRequest(
        scope=MemoryScope(subject_id="subject-1"),
        operation=AccessOperation.READ,
    )
    allowed = AccessDecision(allowed=True)
    denied = AccessDecision(allowed=False, reason_code="scope_denied")
    subject_request = AccessRequest(
        scope=MemorySubject(subject_id="subject-1"),
        operation=AccessOperation.DELETE,
    )

    assert request.operation is AccessOperation.READ
    assert subject_request.operation is AccessOperation.DELETE
    assert allowed.reason_code is None
    assert denied.reason_code == "scope_denied"


class _StructuralAdapter:
    async def open(self) -> None: ...

    async def close(self) -> None: ...

    async def authorize(self, request: AccessRequest) -> AccessDecision:
        return AccessDecision(allowed=True)

    def now(self) -> datetime:
        return datetime(2026, 7, 30, tzinfo=UTC)

    def new_id(self, *, purpose: IdentifierPurpose) -> str:
        return purpose.value


def test_runtime_protocols_use_structural_typing() -> None:
    adapter = _StructuralAdapter()

    assert isinstance(adapter, AsyncLifecycle)
    assert isinstance(adapter, AccessPolicy)
    assert isinstance(adapter, Clock)
    assert isinstance(adapter, IdGenerator)
    assert not isinstance(adapter, ChatModel)
    assert not isinstance(adapter, Embedder)
    assert not isinstance(adapter, PromptProvider)
    assert not isinstance(adapter, Observer)
    assert not isinstance(adapter, MemoryStore)
