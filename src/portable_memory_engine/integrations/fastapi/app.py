"""FastAPI reference application over the stable public MemoryEngine facade."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from portable_memory_engine import (
    AccessDeniedError,
    CapabilityError,
    ConflictError,
    DomainValidationError,
    LifecycleError,
    MemoryEngine,
    MemoryEngineError,
    ProviderError,
    ProviderParseError,
    ProviderUnavailableError,
    StoreError,
    StoreUnavailableError,
    __version__,
)
from portable_memory_engine.integrations.fastapi.config import ReferenceServiceConfig
from portable_memory_engine.integrations.fastapi.mappers import (
    from_add_result,
    from_delete_result,
    from_recall_result,
    to_add_command,
    to_delete_command,
    to_recall_request,
)
from portable_memory_engine.integrations.fastapi.schemas import (
    AddRequest,
    AddResponse,
    DeleteRequest,
    DeleteResponse,
    ErrorBody,
    ErrorEnvelope,
    HealthResponse,
    RecallRequestSchema,
    RecallResponse,
)
from portable_memory_engine.integrations.fastapi.security import (
    AuthorizationContext,
    AuthorizationHook,
    LocalhostOnlyAuthorization,
)

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorEnvelope} for status in (400, 403, 409, 422, 500, 502, 503)
}


class _AuthorizationDenied(Exception):
    pass


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else uuid4().hex


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    request_id = _request_id(request)
    envelope = ErrorEnvelope(error=ErrorBody(code=code, message=message, request_id=request_id))
    return JSONResponse(
        status_code=status_code,
        content=envelope.model_dump(mode="json"),
        headers={"X-Request-ID": request_id},
    )


def _memory_error(error: MemoryEngineError) -> tuple[int, str, str]:
    if isinstance(error, AccessDeniedError):
        return 403, "access_denied", "access policy denied the operation"
    if isinstance(error, DomainValidationError):
        return 422, "invalid_request", "request violated a memory invariant"
    if isinstance(error, CapabilityError):
        return 409, "capability_unavailable", "operation requires an unavailable capability"
    if isinstance(error, ConflictError):
        return 409, "conflict", "operation conflicted with stored state"
    if isinstance(error, (ProviderUnavailableError, StoreUnavailableError, LifecycleError)):
        return 503, "temporarily_unavailable", "memory service is temporarily unavailable"
    if isinstance(error, ProviderParseError):
        return 502, "provider_output_invalid", "provider output failed validation"
    if isinstance(error, ProviderError):
        return 502, "provider_failure", "provider operation failed"
    if isinstance(error, StoreError):
        return 500, "store_failure", "store operation failed"
    return 500, "memory_engine_failure", "memory operation failed"


def create_reference_app(
    *,
    engine: MemoryEngine,
    config: ReferenceServiceConfig | None = None,
    authorization: AuthorizationHook | None = None,
) -> FastAPI:
    """Create a localhost-safe reference app without opening resources at import."""

    if not isinstance(engine, MemoryEngine):
        raise DomainValidationError("engine must be the public MemoryEngine facade")
    selected_config = config or ReferenceServiceConfig()
    if not isinstance(selected_config, ReferenceServiceConfig):
        raise DomainValidationError("config must be ReferenceServiceConfig")
    selected_authorization = authorization or LocalhostOnlyAuthorization()
    if not isinstance(selected_authorization, AuthorizationHook):
        raise DomainValidationError("authorization must implement AuthorizationHook")

    ready = False

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        nonlocal ready
        if selected_config.owns_engine:
            await engine.open()
        ready = True
        try:
            yield
        finally:
            ready = False
            if selected_config.owns_engine:
                await engine.close()

    app = FastAPI(
        title="Portable Memory Engine Reference API",
        description=(
            "Non-production reference wrapper over the public MemoryEngine facade. "
            "It provides no production authentication, rate limiting, or deployment policy."
        ),
        version=__version__,
        lifespan=lifespan,
        debug=False,
        redirect_slashes=False,
        strict_content_type=True,
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        servers=[{"url": "http://127.0.0.1:8000", "description": "Local reference server"}],
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(selected_config.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=list(selected_config.allowed_hosts),
        www_redirect=False,
    )

    @app.middleware("http")
    async def add_request_id(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = uuid4().hex
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        except Exception:
            response = _error_response(
                request,
                status_code=500,
                code="internal_error",
                message="reference service failed",
            )
        response.headers["X-Request-ID"] = request_id
        return response

    async def authorize(
        request: Request,
        authorization_header: Annotated[
            str | None,
            Header(alias="Authorization", include_in_schema=True),
        ] = None,
    ) -> None:
        client_host = request.client.host if request.client is not None else None
        context = AuthorizationContext(
            method=request.method,
            path=request.url.path,
            client_host=client_host,
            request_id=_request_id(request),
            authorization=authorization_header,
        )
        if not await selected_authorization.authorize(context):
            raise _AuthorizationDenied

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(
        request: Request,
        _: RequestValidationError,
    ) -> JSONResponse:
        return _error_response(
            request,
            status_code=422,
            code="invalid_http_request",
            message="request did not match the HTTP schema",
        )

    @app.exception_handler(_AuthorizationDenied)
    async def authorization_denied(
        request: Request,
        _: _AuthorizationDenied,
    ) -> JSONResponse:
        return _error_response(
            request,
            status_code=403,
            code="authorization_denied",
            message="request was not authorized",
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException) -> JSONResponse:
        code = "route_not_found" if error.status_code == 404 else "method_not_allowed"
        message = "route was not found" if error.status_code == 404 else "method is not allowed"
        return _error_response(
            request,
            status_code=error.status_code,
            code=code,
            message=message,
        )

    @app.exception_handler(MemoryEngineError)
    async def memory_engine_error(
        request: Request,
        error: MemoryEngineError,
    ) -> JSONResponse:
        status_code, code, message = _memory_error(error)
        return _error_response(
            request,
            status_code=status_code,
            code=code,
            message=message,
        )

    protected = [Depends(authorize)]

    @app.get(
        "/health",
        response_model=HealthResponse,
        operation_id="health",
        tags=["health"],
    )
    async def health() -> HealthResponse:
        if not ready:
            raise LifecycleError("reference service is not ready")
        return HealthResponse()

    @app.post(
        "/v1/memories/add",
        response_model=AddResponse,
        responses=_ERROR_RESPONSES,
        dependencies=protected,
        operation_id="add_memory",
        tags=["memory"],
    )
    async def add_memory(request: AddRequest) -> AddResponse:
        return from_add_result(await engine.add(to_add_command(request)))

    @app.post(
        "/v1/memories/recall",
        response_model=RecallResponse,
        responses=_ERROR_RESPONSES,
        dependencies=protected,
        operation_id="recall_memory",
        tags=["memory"],
    )
    async def recall_memory(request: RecallRequestSchema) -> RecallResponse:
        return from_recall_result(await engine.recall(to_recall_request(request)))

    @app.post(
        "/v1/memories/delete",
        response_model=DeleteResponse,
        responses=_ERROR_RESPONSES,
        dependencies=protected,
        operation_id="delete_memory",
        tags=["memory"],
    )
    async def delete_memory(request: DeleteRequest) -> DeleteResponse:
        return from_delete_result(await engine.delete(to_delete_command(request)))

    return app
