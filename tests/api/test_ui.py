import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from tests.api.conftest import build_test_app


@pytest.mark.asyncio
async def test_console_page_is_served_without_cloud_deps():
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Generate 40s" in response.text
    assert "telecine" in response.text
    assert "On the gate" in response.text


@pytest.mark.asyncio
async def test_console_assets_are_served():
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        css = await client.get("/static/console.css")
        js = await client.get("/static/console.js")
    assert css.status_code == 200
    assert "telecine-type" in css.text
    assert js.status_code == 200
    assert "crypto.randomUUID" in js.text
    assert "last_error_message" in js.text
    assert "isTerminal" in js.text
    assert "feature_shot_generation" in js.text
    assert "loader-type" in js.text
    assert "setPhase" in js.text
    assert 'if ["FAILED"' not in js.text
    compile_js(js.text)


def compile_js(source: str) -> None:
    """Catch syntax errors that a substring check would miss."""
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    node = shutil.which("node")
    if node is None:
        return
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
        handle.write(source)
        path = Path(handle.name)
    try:
        subprocess.run([node, "--check", str(path)], check=True, capture_output=True)
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_ui_config_exposes_tenant_from_settings():
    app = await build_test_app(
        settings=Settings(
            _env_file=None, tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        )
    )
    try:
        response = await app.client.get("/ui/config")
        assert response.status_code == 200
        body = response.json()
        assert body["tenant_id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        assert body["ready"] is True
        assert "feature_shot_generation" in body
        assert "feature_assemble_deliver" in body
    finally:
        await app.aclose()
