from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.api.errors import register_exception_handlers
from app.domain.errors import AppError
from app.observability.tracing import current_trace_id, mint_trace_id


def test_app_error_returns_stable_envelope():
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    def boom():
        current_trace_id.set(mint_trace_id())
        raise AppError("FEATURE_DISABLED", "off", http_status=403)

    client = TestClient(app)
    r = client.get("/boom")
    assert r.status_code == 403
    body = r.json()
    assert body["code"] == "FEATURE_DISABLED"
    assert "trace_id" in body and body["trace_id"].startswith("tr_")


def test_request_validation_error_returns_stable_envelope():
    """FastAPI's default 422 body shape (``{"detail": [...]}}``) doesn't
    match the platform's ``{code, message, trace_id}`` envelope that every
    other error path uses. A body that fails Pydantic validation must get
    the same shape, not leak FastAPI's default."""
    app = FastAPI()
    register_exception_handlers(app)

    class Body(BaseModel):
        prompt: str

    @app.post("/needs-prompt")
    def needs_prompt(body: Body):
        current_trace_id.set(mint_trace_id())
        return {"prompt": body.prompt}

    client = TestClient(app)
    r = client.post("/needs-prompt", json={})
    assert r.status_code == 422
    body = r.json()
    assert set(body.keys()) == {"code", "message", "trace_id"}
    assert body["code"] == "REQUEST_VALIDATION_FAILED"
    assert body["trace_id"].startswith("tr_")
