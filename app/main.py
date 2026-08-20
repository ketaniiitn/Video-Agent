"""FastAPI app factory + lifespan: pools, feature gateway, startup sweep."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from functools import partial

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.deps import AppState
from app.api.errors import register_exception_handlers
from app.api.jobs import router as jobs_router
from app.api.artifacts import router as artifacts_router
from app.api.ui import mount_static, router as ui_router
from app.cache.redis import create_redis
from app.config import Settings
from app.config_validate import format_missing_config, missing_runtime_variables
from app.db.session import (
    create_engine_from_settings,
    get_raw_session,
    get_session,
    get_sessionmaker,
)
from app.gateway.client import build_gateway
from app.graph.compile import build_graph, postgres_checkpointer
from app.jobs.runner import drain_background_tasks, sweep_stale_jobs
from app.media.ffmpeg import ffmpeg_available, ffmpeg_binary
from app.observability.logging import configure_logging
from app.observability.tracing import current_trace_id, mint_trace_id
from app.providers.registry import build_provider


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    if settings.app_env != "test":
        missing = missing_runtime_variables(settings)
        if missing:
            raise RuntimeError(format_missing_config(missing))
        resolved = ffmpeg_binary(settings.ffmpeg_binary)
        os.environ["FFMPEG_BINARY"] = resolved

    async with AsyncExitStack() as stack:
        engine = create_engine_from_settings(settings)
        stack.push_async_callback(engine.dispose)
        sessionmaker = get_sessionmaker(engine)

        sweep_database_url = settings.database_url_for_sweep()
        if sweep_database_url != settings.database_url:
            sweep_engine = create_engine_from_settings(
                settings.model_copy(update={"database_url": sweep_database_url})
            )
            stack.push_async_callback(sweep_engine.dispose)
            sweep_sessionmaker = get_sessionmaker(sweep_engine)
        else:
            sweep_sessionmaker = sessionmaker

        redis = create_redis(settings.redis_url)
        stack.push_async_callback(redis.aclose)

        gateway = build_gateway(settings)
        provider = build_provider(settings)
        checkpointer = await stack.enter_async_context(postgres_checkpointer(settings))

        session_factory = partial(get_session, sessionmaker=sessionmaker)
        graph = await build_graph(
            checkpointer,
            gateway=gateway,
            session_factory=session_factory,
            settings=settings,
            provider=provider,
        )

        app.state.app_state = AppState(
            settings=settings,
            redis=redis,
            session_factory=session_factory,
            sweep_session_factory=partial(
                get_raw_session, sessionmaker=sweep_sessionmaker
            ),
            graph=graph,
            gateway=gateway,
        )
        app.state.engine = engine

        await sweep_stale_jobs(app.state.app_state)
        try:
            yield
        finally:
            await drain_background_tasks(app.state.app_state)


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
    configure_logging()
    app = FastAPI(title="Video Agent", lifespan=lifespan)
    app.middleware("http")(_mint_trace_id_middleware)
    register_exception_handlers(app)
    app.include_router(jobs_router)
    app.include_router(artifacts_router)
    app.include_router(ui_router)
    mount_static(app)

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        state: AppState | None = getattr(app.state, "app_state", None)
        if state is None:
            return JSONResponse(
                {"status": "not_ready", "reason": "app not started"},
                status_code=503,
            )
        checks: dict[str, str] = {}
        try:
            async with state.sweep_session_factory() as session:
                await session.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["database"] = f"error: {type(exc).__name__}"
        try:
            await state.redis.ping()
            checks["redis"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["redis"] = f"error: {type(exc).__name__}"
        checks["ffmpeg"] = (
            "ok"
            if ffmpeg_available(state.settings.ffmpeg_binary)
            else "missing"
        )
        missing = missing_runtime_variables(state.settings)
        checks["config"] = "ok" if not missing else "missing: " + ",".join(missing)
        ready = all(
            checks[key] == "ok" for key in ("database", "redis", "ffmpeg", "config")
        )
        return JSONResponse(
            {"status": "ok" if ready else "not_ready", "checks": checks},
            status_code=200 if ready else 503,
        )

    return app


app = create_app()
