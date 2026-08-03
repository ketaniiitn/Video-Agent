from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.domain.errors import AppError
from app.observability.tracing import current_trace_id, mint_trace_id


class ErrorBody(BaseModel):
    code: str
    message: str
    trace_id: str


def _resolve_trace_id() -> str:
    trace_id = current_trace_id.get()
    if trace_id is None:
        trace_id = mint_trace_id()
        current_trace_id.set(trace_id)
    return trace_id


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        body = ErrorBody(
            code=exc.code,
            message=exc.message,
            trace_id=_resolve_trace_id(),
        )
        return JSONResponse(
            status_code=exc.http_status,
            content=body.model_dump(),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_request: Request, _exc: Exception) -> JSONResponse:
        body = ErrorBody(
            code="INTERNAL",
            message="An internal error occurred",
            trace_id=_resolve_trace_id(),
        )
        return JSONResponse(
            status_code=500,
            content=body.model_dump(),
        )
