from fastapi import FastAPI
from fastapi.testclient import TestClient

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
