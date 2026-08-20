"""Local test console — static page + tenant bootstrap. Not a product UI."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import Settings

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
router = APIRouter(tags=["ui"])


@router.get("/")
async def test_console() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/ui/config")
async def ui_config(request: Request) -> JSONResponse:
    state = getattr(request.app.state, "app_state", None)
    settings = state.settings if state is not None else Settings(_env_file=None)
    return JSONResponse(
        {
            "tenant_id": settings.tenant_id,
            "ready": bool(settings.tenant_id),
            "feature_shot_generation": settings.feature_shot_generation,
            "feature_qc_repair": settings.feature_qc_repair,
            "feature_assemble_deliver": settings.feature_assemble_deliver,
        }
    )


def mount_static(app) -> None:
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
