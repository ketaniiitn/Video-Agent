from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.domain.errors import AppError
from app.observability.tracing import resolve_trace_id


class ErrorBody(BaseModel):
    code: str
    message: str
    trace_id: str


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        body = ErrorBody(
            code=exc.code,
            message=exc.message,
            trace_id=resolve_trace_id(),
        )
        return JSONResponse(
            status_code=exc.http_status,
            content=body.model_dump(),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        body = ErrorBody(
            code="REQUEST_VALIDATION_FAILED",
            message=str(exc),
            trace_id=resolve_trace_id(),
        )
        return JSONResponse(
            status_code=422,
            content=body.model_dump(),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_request: Request, _exc: Exception) -> JSONResponse:
        body = ErrorBody(
            code="INTERNAL",
            message="An internal error occurred",
            trace_id=resolve_trace_id(),
        )
        return JSONResponse(
            status_code=500,
            content=body.model_dump(),
        )
