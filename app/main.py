"""FastAPI app factory + lifespan: pools, feature gateway, startup sweep."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from functools import partial

import redis.asyncio as redis_asyncio
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.api.deps import AppState
from app.api.errors import register_exception_handlers
from app.api.jobs import router as jobs_router
from app.config import Settings
from app.db.session import (
    create_engine_from_settings,
    get_raw_session,
    get_session,
    get_sessionmaker,
)
from app.gateway.client import build_gateway
from app.graph.compile import build_graph, postgres_checkpointer
from app.jobs.runner import sweep_stale_jobs
from app.observability.tracing import current_trace_id, mint_trace_id


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    async with AsyncExitStack() as stack:
        engine = create_engine_from_settings(settings)
        stack.push_async_callback(engine.dispose)
        sessionmaker = get_sessionmaker(engine)

        redis = redis_asyncio.from_url(settings.redis_url)
        stack.push_async_callback(redis.aclose)

        gateway = build_gateway(settings)
        checkpointer = await stack.enter_async_context(postgres_checkpointer(settings))

        session_factory = partial(get_session, sessionmaker=sessionmaker)
        graph = await build_graph(
            checkpointer, gateway=gateway, session_factory=session_factory
        )

        app.state.app_state = AppState(
            settings=settings,
            redis=redis,
            session_factory=session_factory,
            sweep_session_factory=partial(get_raw_session, sessionmaker=sessionmaker),
            graph=graph,
            gateway=gateway,
        )

        await sweep_stale_jobs(app.state.app_state)
        yield


async def _mint_trace_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Mint the trace id before anything else runs, including header
    validation, so even a 400/403 response carries a trace_id."""
    token = current_trace_id.set(mint_trace_id())
    try:
        response = await call_next(request)
    finally:
        current_trace_id.reset(token)
    return response


def create_app() -> FastAPI:
    app = FastAPI(title="Video Agent", lifespan=lifespan)
    app.middleware("http")(_mint_trace_id_middleware)
    register_exception_handlers(app)
    app.include_router(jobs_router)

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return app


app = create_app()
