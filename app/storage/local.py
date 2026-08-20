from pathlib import Path
from uuid import UUID


def job_dir(media_root: str | Path, tenant_id: UUID | str, job_id: UUID | str) -> Path:
    return Path(media_root) / str(tenant_id) / str(job_id)


def clip_path(
    media_root: str | Path,
    tenant_id: UUID | str,
    job_id: UUID | str,
    beat_index: int,
) -> Path:
    return job_dir(media_root, tenant_id, job_id) / f"shot_{beat_index}.mp4"


def frame_path(
    media_root: str | Path,
    tenant_id: UUID | str,
    job_id: UUID | str,
    beat_index: int,
) -> Path:
    return job_dir(media_root, tenant_id, job_id) / f"frame_{beat_index}.jpg"


def assembled_path(
    media_root: str | Path,
    tenant_id: UUID | str,
    job_id: UUID | str,
) -> Path:
    return job_dir(media_root, tenant_id, job_id) / "assembled.mp4"


def thumbnail_path(
    media_root: str | Path,
    tenant_id: UUID | str,
    job_id: UUID | str,
) -> Path:
    return job_dir(media_root, tenant_id, job_id) / "thumbnail.jpg"


async def save_bytes(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path

