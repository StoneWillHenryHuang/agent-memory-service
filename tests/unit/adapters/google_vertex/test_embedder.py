"""Fake-client tests for the optional Google Vertex embedding adapter."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence
from typing import cast

import httpx
import pytest
from google.auth.credentials import Credentials
from google.genai import errors, types
from google.genai.client import AsyncClient

import portable_memory_engine.adapters.google_vertex.embedder as embedder_module
from portable_memory_engine.adapters.google_vertex import (
    GoogleVertexEmbedder,
    GoogleVertexEmbeddingConfig,
)
from portable_memory_engine.domain import (
    DomainValidationError,
    EmbeddingTask,
    LifecycleError,
    ProviderError,
    ProviderParseError,
    ProviderUnavailableError,
)
from portable_memory_engine.ports import Embedder, EmbeddingRequest


class FakeModels:
    def __init__(
        self,
        outcomes: Sequence[types.EmbedContentResponse | Exception],
        *,
        started: asyncio.Event | None = None,
        release: asyncio.Event | None = None,
    ) -> None:
        self._outcomes = list(outcomes)
        self._started = started
        self._release = release
        self.calls: list[tuple[str, list[str], types.EmbedContentConfig]] = []

    async def embed_content(
        self,
        *,
        model: str,
        contents: object,
        config: types.EmbedContentConfig | types.EmbedContentConfigDict | None = None,
    ) -> types.EmbedContentResponse:
        text_values = cast("list[str]", contents)
        typed_config = cast("types.EmbedContentConfig", config)
        self.calls.append((model, list(text_values), typed_config))
        if self._started is not None:
            self._started.set()
        if self._release is not None:
            await self._release.wait()
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeClient:
    def __init__(
        self,
        outcomes: Sequence[types.EmbedContentResponse | Exception],
        *,
        started: asyncio.Event | None = None,
        release: asyncio.Event | None = None,
    ) -> None:
        self.models = FakeModels(outcomes, started=started, release=release)
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1


def _config(**overrides: object) -> GoogleVertexEmbeddingConfig:
    values: dict[str, object] = {
        "project": "synthetic-project",
        "region": "example-region1",
        "model": "publisher-embedding-v1",
        "dimension": 3,
        "batch_size": 2,
    }
    values.update(overrides)
    return GoogleVertexEmbeddingConfig(**values)  # type: ignore[arg-type]


def _response(*vectors: Sequence[float]) -> types.EmbedContentResponse:
    return types.EmbedContentResponse(
        embeddings=[types.ContentEmbedding(values=list(vector)) for vector in vectors]
    )


def _model(
    client: FakeClient,
    *,
    config: GoogleVertexEmbeddingConfig | None = None,
) -> GoogleVertexEmbedder:
    return GoogleVertexEmbedder(
        config or _config(),
        client=cast("AsyncClient", client),
    )


def test_config_requires_caller_owned_provider_values_and_is_project_safe() -> None:
    config = _config()
    model = _model(FakeClient((_response((1.0, 2.0, 3.0)),)), config=config)

    assert config.region == "example-region1"
    assert config.model == "publisher-embedding-v1"
    assert config.dimension == 3
    assert config.auto_truncate is False
    assert config.api_version == "v1"
    assert "synthetic-project" not in repr(config)
    assert "example-region1" not in repr(config)
    assert "publisher-embedding-v1" not in repr(config)
    assert "v1" not in repr(config)
    assert model.model_id == config.model
    assert model.dimension == config.dimension
    assert isinstance(model, Embedder)

    for overrides in (
        {"project": ""},
        {"region": "bad region"},
        {"model": "*"},
        {"dimension": 0},
        {"dimension": 16_001},
        {"batch_size": 0},
        {"batch_size": 251},
        {"auto_truncate": "false"},
        {"api_version": ""},
    ):
        with pytest.raises(DomainValidationError):
            _config(**overrides)


def test_credentials_and_injected_client_are_mutually_exclusive() -> None:
    client = FakeClient((_response((1.0, 2.0, 3.0)),))

    with pytest.raises(DomainValidationError, match="mutually exclusive"):
        GoogleVertexEmbedder(
            _config(),
            credentials=cast("Credentials", object()),
            client=cast("AsyncClient", client),
        )


def test_batching_document_task_and_vectors_are_copied() -> None:
    native = types.ContentEmbedding(values=[1.0, 2.0, 3.0])
    client = FakeClient(
        (
            types.EmbedContentResponse(
                embeddings=[native, types.ContentEmbedding(values=[4.0, 5.0, 6.0])]
            ),
            _response((7.0, 8.0, 9.0)),
        )
    )
    model = _model(client)

    async def scenario() -> None:
        with pytest.raises(LifecycleError):
            await model.embed(EmbeddingRequest(("one",), EmbeddingTask.DOCUMENT))
        await model.open()
        await model.open()
        embeddings = await model.embed(
            EmbeddingRequest(("one", "two", "three"), EmbeddingTask.DOCUMENT)
        )
        await model.close()
        await model.close()

        assert [embedding.values for embedding in embeddings] == [
            (1.0, 2.0, 3.0),
            (4.0, 5.0, 6.0),
            (7.0, 8.0, 9.0),
        ]
        assert all(embedding.model_id == "publisher-embedding-v1" for embedding in embeddings)
        assert all(embedding.task is EmbeddingTask.DOCUMENT for embedding in embeddings)
        assert client.close_calls == 0
        assert native.values is not None
        native.values[0] = 999.0
        assert embeddings[0].values == (1.0, 2.0, 3.0)

    asyncio.run(scenario())

    assert [call[1] for call in client.models.calls] == [["one", "two"], ["three"]]
    for model_id, _, config in client.models.calls:
        assert model_id == "publisher-embedding-v1"
        assert config.task_type == "RETRIEVAL_DOCUMENT"
        assert config.output_dimensionality == 3
        assert config.auto_truncate is False


def test_query_task_hint_is_mapped_only_at_provider_call() -> None:
    client = FakeClient((_response((0.1, 0.2, 0.3)),))
    model = _model(client)

    async def scenario() -> None:
        await model.open()
        result = await model.embed(EmbeddingRequest(("query",), EmbeddingTask.QUERY))
        await model.close()
        assert result[0].task is EmbeddingTask.QUERY

    asyncio.run(scenario())

    assert client.models.calls[0][2].task_type == "RETRIEVAL_QUERY"


@pytest.mark.parametrize(
    ("response", "reason_code"),
    [
        (types.EmbedContentResponse(embeddings=None), "empty_response"),
        (_response((1.0, 2.0, 3.0)), "result_count_mismatch"),
        (_response((1.0, 2.0)), "dimension_mismatch"),
        (_response((1.0, math.nan, 3.0)), "invalid_vector"),
    ],
)
def test_invalid_provider_results_fail_before_returning_embeddings(
    response: types.EmbedContentResponse,
    reason_code: str,
) -> None:
    client = FakeClient((response,))
    model = _model(client)
    texts = ("one", "two") if reason_code == "result_count_mismatch" else ("one",)

    async def scenario() -> None:
        await model.open()
        with pytest.raises(ProviderParseError) as captured:
            await model.embed(EmbeddingRequest(texts, EmbeddingTask.DOCUMENT))
        await model.close()
        assert captured.value.reason_code == reason_code
        assert "one" not in str(captured.value)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            errors.ClientError(429, {"error": {"message": "synthetic private payload"}}),
            ProviderUnavailableError,
        ),
        (
            errors.ServerError(503, {"error": {"message": "synthetic private payload"}}),
            ProviderUnavailableError,
        ),
        (
            errors.ClientError(400, {"error": {"message": "synthetic private payload"}}),
            ProviderError,
        ),
        (
            httpx.ConnectError(
                "synthetic transport payload",
                request=httpx.Request("POST", "https://models.example.com/embed"),
            ),
            ProviderUnavailableError,
        ),
        (RuntimeError("synthetic private payload"), ProviderError),
    ],
)
def test_native_failures_are_translated_without_payload(
    error: Exception,
    expected: type[ProviderError],
) -> None:
    model = _model(FakeClient((error,)))

    async def scenario() -> None:
        await model.open()
        with pytest.raises(expected) as captured:
            await model.embed(EmbeddingRequest(("private input",), EmbeddingTask.DOCUMENT))
        await model.close()
        rendered = f"{captured.value!s} {captured.value!r}"
        assert "synthetic private payload" not in rendered
        assert "private input" not in rendered

    asyncio.run(scenario())


def test_cancellation_propagates_and_releases_lifecycle() -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        client = FakeClient(
            (_response((1.0, 2.0, 3.0)),),
            started=started,
            release=release,
        )
        model = _model(client)
        await model.open()
        task = asyncio.create_task(model.embed(EmbeddingRequest(("cancel",), EmbeddingTask.QUERY)))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await model.close()
        assert client.close_calls == 0

    asyncio.run(scenario())


def test_owned_client_is_closed_and_recreated(monkeypatch: pytest.MonkeyPatch) -> None:
    first = FakeClient((_response((1.0, 2.0, 3.0)),))
    second = FakeClient((_response((4.0, 5.0, 6.0)),))
    clients = [first, second]

    def fake_new_client(
        config: GoogleVertexEmbeddingConfig,
        credentials: Credentials | None,
    ) -> AsyncClient:
        assert config.project == "synthetic-project"
        assert credentials is None
        return cast("AsyncClient", clients.pop(0))

    monkeypatch.setattr(embedder_module, "_new_client", fake_new_client)
    model = GoogleVertexEmbedder(_config())

    async def scenario() -> None:
        await model.open()
        await model.close()
        await model.open()
        await model.close()
        assert first.close_calls == 1
        assert second.close_calls == 1

    asyncio.run(scenario())
