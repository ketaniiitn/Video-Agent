from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from app.domain.errors import AppError

FfmpegRunner = Callable[..., Coroutine[Any, Any, tuple[int, bytes, bytes]]]


async def _default_runner(*args: str) -> tuple[int, bytes, bytes]:
    import asyncio

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode or 0, stdout, stderr


async def extract_last_frame(
    video_path: Path,
    output_path: Path,
    *,
    runner: FfmpegRunner | None = None,
) -> Path:
    """Extract the last frame of a video to ``output_path`` via ffmpeg."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    run = runner or _default_runner
    # -sseof seeks from end; -update 1 writes a single image.
    code, _stdout, stderr = await run(
        "ffmpeg",
        "-y",
        "-sseof",
        "-0.05",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(output_path),
    )
    if code != 0:
        detail = stderr.decode("utf-8", errors="replace")[:500]
        raise AppError(
            "FFMPEG_FAILED",
            f"ffmpeg failed extracting frame: {detail}",
            http_status=500,
        )
    if not output_path.exists():
        raise AppError(
            "FFMPEG_FAILED",
            "ffmpeg completed but output frame is missing",
            http_status=500,
        )
    return output_path
