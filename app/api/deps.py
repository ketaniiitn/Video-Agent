"""Request-scoped access to the app's wired dependencies.

Everything a route handler needs (settings, Redis, DB session factory, the
compiled graph, the gateway) hangs off one ``AppState`` attached to
``app.state`` in ``app/main.py``'s lifespan. Tests replace the whole object
via ``app.dependency_overrides[get_app_state]`` instead of patching
individual globals, so production wiring stays the single place that
assembles real pools/gateways.
"""

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.gateway.protocols import GatewayClient

SessionFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]
RawSessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


@dataclass
class AppState:
    settings: Settings
    redis: Any
    session_factory: SessionFactory
    sweep_session_factory: RawSessionFactory
    graph: Any
    gateway: GatewayClient
    background_tasks: list[asyncio.Task[Any]] = field(default_factory=list)


def get_app_state(request: Request) -> AppState:
    return request.app.state.app_state
