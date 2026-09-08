"""Google Gen AI SDK implementation of the provider-neutral embedding port."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import httpx
from google.auth.credentials import Credentials
from google.genai import Client, errors, types
from google.genai.client import AsyncClient

from portable_memory_engine.adapters.google_vertex.config import (
    GoogleVertexEmbeddingConfig,
)
from portable_memory_engine.domain import (
    DomainValidationError,
    Embedding,
    EmbeddingTask,
    LifecycleError,
    ProviderError,
    ProviderParseError,
    ProviderUnavailableError,
)
from portable_memory_engine.ports import EmbeddingRequest

_RESPONSE_SCHEMA_VERSION = "google-genai-embedding.v1"
_TASK_TYPES = {
    EmbeddingTask.DOCUMENT: "RETRIEVAL_DOCUMENT",
    EmbeddingTask.QUERY: "RETRIEVAL_QUERY",
}


def _new_client(
    config: GoogleVertexEmbeddingConfig,
    credentials: Credentials | None,
) -> AsyncClient:
    return Client(
        vertexai=True,
        project=config.project,
        location=config.region,
        credentials=credentials,
        http_options=types.HttpOptions(api_version=config.api_version),
    ).aio


class GoogleVertexEmbedder:
    """Batching Vertex implementation with adapter-local task translation."""

    def __init__(
        self,
        config: GoogleVertexEmbeddingConfig,
        *,
        credentials: Credentials | None = None,
        client: AsyncClient | None = None,
    ) -> None:
        if not isinstance(config, GoogleVertexEmbeddingConfig):
            raise DomainValidationError("config must be GoogleVertexEmbeddingConfig")
        if credentials is not None and client is not None:
            raise DomainValidationError("credentials and an injected client are mutually exclusive")
        self._config = config
        self._credentials = credentials
        self._injected_client = client
        self._client: AsyncClient | None = None
        self._condition = asyncio.Condition()
        self._open = False
        self._closing = False
        self._active_calls = 0

    @property
    def model_id(self) -> str:
        """Return the caller-configured embedding model identity."""

        return self._config.model

    @property
    def dimension(self) -> int:
        """Return the exact output dimension required from the provider."""

        return self._config.dimension

    async def open(self) -> None:
        """Attach an injected client or create an owned Vertex async client."""

        async with self._condition:
            while self._closing:
                await self._condition.wait()
            if self._open:
                return
            try:
                client = self._injected_client or _new_client(
                    self._config,
                    self._credentials,
                )
            except Exception:
                raise ProviderError("embedding client could not be opened") from None
            self._client = client
            self._open = True

    async def close(self) -> None:
        """Wait for active calls and close only an adapter-owned client."""

        async with self._condition:
            while self._closing:
                await self._condition.wait()
            if not self._open:
                return
            self._open = False
            self._closing = True
            try:
                while self._active_calls:
                    await self._condition.wait()
            except asyncio.CancelledError:
                self._open = True
                self._closing = False
                self._condition.notify_all()
                raise
            client = self._client
            owned = self._injected_client is None
            self._client = None

        close_error = False
        if owned and client is not None:
            close_task = asyncio.create_task(client.aclose())
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError:
                try:
                    await close_task
                finally:
                    async with self._condition:
                        self._closing = False
                        self._condition.notify_all()
                raise
            except Exception:
                close_error = True
        async with self._condition:
            self._closing = False
            self._condition.notify_all()
        if close_error:
            raise ProviderError("embedding client could not be closed") from None

    async def _acquire_client(self) -> AsyncClient:
        async with self._condition:
            if not self._open or self._client is None:
                raise LifecycleError("Google Vertex embedder is not open")
            self._active_calls += 1
            return self._client

    async def _release_client(self) -> None:
        async with self._condition:
            self._active_calls -= 1
            if not self._active_calls:
                self._condition.notify_all()

    async def embed(self, request: EmbeddingRequest) -> tuple[Embedding, ...]:
        """Return one validated immutable vector for each input text."""

        if not isinstance(request, EmbeddingRequest):
            raise DomainValidationError("embedding request must be EmbeddingRequest")
        client = await self._acquire_client()
        try:
            return await self._embed_batches(client, request)
        finally:
            await self._release_client()

    async def _embed_batches(
        self,
        client: AsyncClient,
        request: EmbeddingRequest,
    ) -> tuple[Embedding, ...]:
        output: list[Embedding] = []
        task_type = _TASK_TYPES[request.task]
        try:
            for offset in range(0, len(request.texts), self._config.batch_size):
                texts = request.texts[offset : offset + self._config.batch_size]
                response = await client.models.embed_content(
                    model=self._config.model,
                    contents=list(texts),
                    config=types.EmbedContentConfig(
                        task_type=task_type,
                        output_dimensionality=self._config.dimension,
                        auto_truncate=self._config.auto_truncate,
                    ),
                )
                output.extend(self._parse_batch(response.embeddings, texts, request.task))
        except ProviderError:
            raise
        except errors.APIError as error:
            if error.code == 429 or 500 <= error.code < 600:
                raise ProviderUnavailableError(
                    f"embedding provider is temporarily unavailable (status {error.code})"
                ) from None
            raise ProviderError(
                f"embedding provider rejected the request (status {error.code})"
            ) from None
        except httpx.TransportError:
            raise ProviderUnavailableError("embedding provider transport failed") from None
        except Exception:
            raise ProviderError("embedding provider call failed") from None
        return tuple(output)

    def _parse_batch(
        self,
        native_embeddings: Sequence[types.ContentEmbedding] | None,
        texts: Sequence[str],
        task: EmbeddingTask,
    ) -> tuple[Embedding, ...]:
        if not native_embeddings:
            raise ProviderParseError(
                schema_version=_RESPONSE_SCHEMA_VERSION,
                reason_code="empty_response",
            )
        if len(native_embeddings) != len(texts):
            raise ProviderParseError(
                schema_version=_RESPONSE_SCHEMA_VERSION,
                reason_code="result_count_mismatch",
            )
        output: list[Embedding] = []
        for native in native_embeddings:
            values = native.values
            if values is None or len(values) != self._config.dimension:
                raise ProviderParseError(
                    schema_version=_RESPONSE_SCHEMA_VERSION,
                    reason_code="dimension_mismatch",
                )
            try:
                output.append(
                    Embedding(
                        values=tuple(values),
                        model_id=self._config.model,
                        task=task,
                    )
                )
            except DomainValidationError:
                raise ProviderParseError(
                    schema_version=_RESPONSE_SCHEMA_VERSION,
                    reason_code="invalid_vector",
                ) from None
        return tuple(output)
