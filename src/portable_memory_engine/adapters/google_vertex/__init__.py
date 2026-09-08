"""Optional Google Vertex text-embedding adapter."""

from portable_memory_engine.adapters.google_vertex.config import (
    GoogleVertexEmbeddingConfig,
)
from portable_memory_engine.adapters.google_vertex.embedder import GoogleVertexEmbedder

__all__ = (
    "GoogleVertexEmbedder",
    "GoogleVertexEmbeddingConfig",
)
