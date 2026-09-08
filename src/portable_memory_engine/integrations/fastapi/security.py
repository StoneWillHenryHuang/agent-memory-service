"""Small injectable authorization boundary for the reference app."""

from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import ip_address
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class AuthorizationContext:
    """Minimal request metadata supplied to an injected authorization hook."""

    method: str
    path: str
    client_host: str | None
    request_id: str
    authorization: str | None = field(default=None, repr=False)


@runtime_checkable
class AuthorizationHook(Protocol):
    """Reference-only hook; deployments must supply production authorization."""

    async def authorize(self, context: AuthorizationContext) -> bool:
        """Return whether one request may call a memory endpoint."""

        ...


class LocalhostOnlyAuthorization:
    """Default non-production hook allowing only loopback client addresses."""

    async def authorize(self, context: AuthorizationContext) -> bool:
        """Reject missing, host-name, and non-loopback client addresses."""

        if context.client_host is None:
            return False
        try:
            return ip_address(context.client_host).is_loopback
        except ValueError:
            return False
