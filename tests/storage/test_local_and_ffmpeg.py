from pathlib import Path
from uuid import uuid4

import pytest

from app.domain.errors import AppError
from app.media.ffmpeg import extract_last_frame
from app.storage.local import clip_path, frame_path, job_dir, save_bytes


def test_media_paths_layout():
    tenant = uuid4()
    job = uuid4()
    root = "/tmp/media"
    assert job_dir(root, tenant, job) == Path(root) / str(tenant) / str(job)
    assert clip_path(root, tenant, job, 2) == Path(root) / str(tenant) / str(job) / "shot_2.mp4"
    assert frame_path(root, tenant, job, 2) == Path(root) / str(tenant) / str(job) / "frame_2.jpg"


@pytest.mark.asyncio
async def test_save_bytes_creates_parents(tmp_path: Path):
    target = tmp_path / "a" / "b" / "c.bin"
    out = await save_bytes(target, b"hello")
    assert out.read_bytes() == b"hello"


@pytest.mark.asyncio
async def test_extract_last_frame_success(tmp_path: Path):
    video = tmp_path / "in.mp4"
    video.write_bytes(b"fake")
    out = tmp_path / "out.jpg"

    async def runner(*args: str):
        Path(args[-1]).write_bytes(b"JPEG")
        return 0, b"", b""

    path = await extract_last_frame(video, out, runner=runner)
    assert path.read_bytes() == b"JPEG"


@pytest.mark.asyncio
async def test_extract_last_frame_raises_on_ffmpeg_failure(tmp_path: Path):
    video = tmp_path / "in.mp4"
    video.write_bytes(b"fake")
    out = tmp_path / "out.jpg"

    async def runner(*args: str):
        return 1, b"", b"boom"

    with pytest.raises(AppError) as ei:
        await extract_last_frame(video, out, runner=runner)
    assert ei.value.code == "FFMPEG_FAILED"
