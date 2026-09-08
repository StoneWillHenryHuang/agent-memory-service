"""Optional FastAPI reference service exports."""

from portable_memory_engine.integrations.fastapi.app import create_reference_app
from portable_memory_engine.integrations.fastapi.config import ReferenceServiceConfig
from portable_memory_engine.integrations.fastapi.security import (
    AuthorizationContext,
    AuthorizationHook,
    LocalhostOnlyAuthorization,
)

__all__ = (
    "AuthorizationContext",
    "AuthorizationHook",
    "LocalhostOnlyAuthorization",
    "ReferenceServiceConfig",
    "create_reference_app",
)
