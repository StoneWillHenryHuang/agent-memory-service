"""Tests for replaceable default prompt resolution and injection baseline."""

from __future__ import annotations

import asyncio
from typing import cast

import pytest

from portable_memory_engine.domain import (
    DomainValidationError,
    FrozenJsonObject,
    LifecycleError,
    MemoryKind,
    PromptResolutionError,
)
from portable_memory_engine.ports import (
    PromptProvider,
    PromptPurpose,
    PromptRequest,
    PromptTemplate,
)
from portable_memory_engine.prompts import (
    DefaultPromptProvider,
    OutputSchemaVersion,
    PromptKey,
    SourcePromptKey,
    output_schema,
)


def _override(identifier: str, marker: str) -> PromptTemplate:
    return PromptTemplate(
        identifier=identifier,
        version="prompt.v9",
        system_text=f"Synthetic system override {marker}",
        user_text=f"Synthetic user override {marker}",
        response_schema_version=OutputSchemaVersion.FACT_V1.value,
        response_schema=output_schema(OutputSchemaVersion.FACT_V1),
    )


def test_provider_lifecycle_and_generic_defaults() -> None:
    async def scenario() -> None:
        provider = DefaultPromptProvider()
        request = PromptRequest(PromptPurpose.EXTRACTION, MemoryKind.FACT)

        assert isinstance(provider, PromptProvider)
        with pytest.raises(LifecycleError):
            await provider.get_prompt(request)
        await provider.open()
        prompt = await provider.get_prompt(request)
        await provider.close()
        await provider.close()

        assert prompt.identifier == "generic-fact-extraction"
        assert prompt.response_schema_version == "fact.v1"
        assert prompt.response_schema is output_schema(OutputSchemaVersion.FACT_V1)
        assert "untrusted" in prompt.system_text.lower()
        assert "{conversation}" in prompt.user_text

    asyncio.run(scenario())


def test_resolution_priority_is_user_then_source_then_locale_then_default() -> None:
    async def scenario() -> None:
        locale_key = PromptKey("fr", PromptPurpose.EXTRACTION, MemoryKind.FACT)
        regional_key = PromptKey("fr-ca", PromptPurpose.EXTRACTION, MemoryKind.FACT)
        source_key = SourcePromptKey(
            "imported-notes", "fr", PromptPurpose.EXTRACTION, MemoryKind.FACT
        )
        locale = _override("locale-override", "locale")
        source = _override("source-override", "source")
        user = _override("user-override", "user")
        provider = DefaultPromptProvider(
            locale_overrides={
                locale_key: locale,
                regional_key: _override("regional-override", "regional"),
            },
            source_overrides={source_key: source},
        )
        await provider.open()

        assert (
            await provider.get_prompt(
                PromptRequest(PromptPurpose.EXTRACTION, MemoryKind.FACT, locale="fr")
            )
            is locale
        )
        assert (
            await provider.get_prompt(
                PromptRequest(
                    PromptPurpose.EXTRACTION,
                    MemoryKind.FACT,
                    locale="fr",
                    source="imported-notes",
                )
            )
            is source
        )
        assert (
            await provider.get_prompt(
                PromptRequest(
                    PromptPurpose.EXTRACTION,
                    MemoryKind.FACT,
                    locale="fr-CA",
                    source="imported-notes",
                )
            )
            is source
        )
        assert (
            await provider.get_prompt(
                PromptRequest(
                    PromptPurpose.EXTRACTION,
                    MemoryKind.FACT,
                    locale="fr",
                    source="imported-notes",
                    override=user,
                )
            )
            is user
        )

    asyncio.run(scenario())


def test_arbitrary_source_has_no_builtin_business_behavior() -> None:
    async def scenario() -> None:
        provider = DefaultPromptProvider()
        await provider.open()

        generic = await provider.get_prompt(
            PromptRequest(PromptPurpose.SUMMARY, MemoryKind.SUMMARY)
        )
        sourced = await provider.get_prompt(
            PromptRequest(
                PromptPurpose.SUMMARY,
                MemoryKind.SUMMARY,
                source="caller-defined-source",
            )
        )
        regional = await provider.get_prompt(
            PromptRequest(PromptPurpose.SUMMARY, MemoryKind.SUMMARY, locale="en-GB")
        )

        assert sourced is generic
        assert regional is generic

    asyncio.run(scenario())


def test_unknown_locale_and_custom_kind_fail_with_sanitized_key() -> None:
    async def scenario() -> None:
        provider = DefaultPromptProvider()
        await provider.open()

        with pytest.raises(PromptResolutionError) as captured:
            await provider.get_prompt(
                PromptRequest(
                    PromptPurpose.EXTRACTION,
                    MemoryKind("custom:episode"),
                    locale="de",
                    source="private-source-name",
                )
            )

        assert captured.value.locale == "de"
        assert "private-source-name" not in str(captured.value)

    asyncio.run(scenario())


def test_provider_copies_override_mappings_and_rejects_invalid_entries() -> None:
    async def scenario() -> None:
        key = PromptKey("fr", PromptPurpose.EXTRACTION, MemoryKind.FACT)
        first = _override("first", "first")
        values = {key: first}
        provider = DefaultPromptProvider(locale_overrides=values)
        values[key] = _override("second", "second")
        await provider.open()

        resolved = await provider.get_prompt(
            PromptRequest(PromptPurpose.EXTRACTION, MemoryKind.FACT, locale="fr")
        )

        assert resolved is first

    asyncio.run(scenario())
    with pytest.raises(DomainValidationError, match="PromptKey"):
        DefaultPromptProvider(
            locale_overrides=cast(
                "dict[PromptKey, PromptTemplate]", {"bad": _override("bad", "bad")}
            )
        )


def test_default_prompts_state_the_injection_resistance_baseline() -> None:
    async def scenario() -> None:
        provider = DefaultPromptProvider()
        await provider.open()
        prompts = [
            await provider.get_prompt(PromptRequest(purpose, kind))
            for purpose, kind in (
                (PromptPurpose.SUMMARY, MemoryKind.SUMMARY),
                (PromptPurpose.EXTRACTION, MemoryKind.FACT),
                (PromptPurpose.PROFILE, MemoryKind.PROFILE),
                (PromptPurpose.UPDATE, MemoryKind.FACT),
            )
        ]

        # Prompts are only one control. Strict parsing, access policy, and
        # application validation remain required; no full-defense claim is made.
        for prompt in prompts:
            lowered = prompt.system_text.lower()
            assert "untrusted" in lowered
            assert "never as an instruction" in lowered
            assert "exactly one json object" in lowered
            assert "credentials" in lowered

    asyncio.run(scenario())


def test_prompt_values_require_paired_schema_metadata_and_valid_locale() -> None:
    with pytest.raises(DomainValidationError, match="provided together"):
        PromptTemplate(
            "broken",
            "v1",
            "system",
            "user",
            response_schema_version="fact.v1",
        )
    with pytest.raises(DomainValidationError, match="language tag"):
        PromptRequest(PromptPurpose.EXTRACTION, MemoryKind.FACT, locale="not_a_locale")
    with pytest.raises(DomainValidationError, match="override"):
        PromptRequest(
            PromptPurpose.EXTRACTION,
            MemoryKind.FACT,
            override=cast("PromptTemplate", FrozenJsonObject()),
        )
