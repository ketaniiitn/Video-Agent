from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from fastapi.responses import FileResponse

from app.api.deps import AppState, get_app_state
from app.db.models import Job
from app.domain.errors import AppError
from app.storage.local import job_dir
from app.storage.presign import ALLOWED_ARTIFACTS, verify_signature

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _require_header(value: str | None, code: str, message: str) -> str:
    if not value:
        raise AppError(code, message, http_status=400)
    return value


def _parse_tenant_id(raw: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as exc:
        raise AppError(
            "TENANT_ID_INVALID", "X-Tenant-Id must be a UUID", http_status=400
        ) from exc


@router.get("/{job_id}/artifacts/{artifact}")
async def download_artifact(
    job_id: UUID,
    artifact: str,
    expires: int,
    sig: str,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    state: AppState = Depends(get_app_state),
) -> FileResponse:
    x_tenant_id = _require_header(
        x_tenant_id, "TENANT_ID_MISSING", "X-Tenant-Id header is required"
    )
    tenant_id = _parse_tenant_id(x_tenant_id)
    if artifact not in ALLOWED_ARTIFACTS:
        raise AppError("ARTIFACT_UNKNOWN", f"Unknown artifact {artifact}", http_status=404)
    verify_signature(
        job_id=job_id,
        artifact=artifact,
        expires=expires,
        signature=sig,
        settings=state.settings,
    )

    async with state.session_factory(tenant_id) as session:
        job = await session.get(Job, job_id)
        if job is None or job.tenant_id != tenant_id:
            raise AppError("JOB_NOT_FOUND", "Job was not found", http_status=404)

    root = job_dir(state.settings.media_root, tenant_id, job_id).resolve()
    candidate = (root / artifact).resolve()
    if not candidate.is_relative_to(root):
        raise AppError("ARTIFACT_FORBIDDEN", "Invalid artifact path", http_status=403)
    if not candidate.is_file():
        raise AppError("ARTIFACT_NOT_FOUND", "Artifact is not on disk", http_status=404)
    return FileResponse(candidate, filename=artifact)
