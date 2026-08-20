from collections.abc import Callable, Coroutine
from pathlib import Path
from shutil import which
from typing import Any
import os

from app.domain.errors import AppError

FfmpegRunner = Callable[..., Coroutine[Any, Any, tuple[int, bytes, bytes]]]


def ffmpeg_binary(configured: str | None = None) -> str:
    configured = configured or os.environ.get("FFMPEG_BINARY") or "ffmpeg"
    if Path(configured).is_file():
        return configured
    found = which(configured)
    if found:
        return found
    return configured


def ffmpeg_available(configured: str = "ffmpeg") -> bool:
    path = ffmpeg_binary(configured)
    return Path(path).is_file() or which(configured) is not None


async def _default_runner(*args: str, timeout_seconds: float = 120.0) -> tuple[int, bytes, bytes]:
    import asyncio

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_seconds
        )
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise AppError(
            "FFMPEG_TIMEOUT",
            f"ffmpeg exceeded {timeout_seconds}s and was killed",
            http_status=500,
        ) from exc
    return proc.returncode or 0, stdout, stderr


def _runner_with_timeout(timeout_seconds: float) -> FfmpegRunner:
    async def run(*args: str) -> tuple[int, bytes, bytes]:
        return await _default_runner(*args, timeout_seconds=timeout_seconds)

    return run


async def extract_last_frame(
    video_path: Path,
    output_path: Path,
    *,
    runner: FfmpegRunner | None = None,
    binary: str | None = None,
    timeout_seconds: float = 120.0,
) -> Path:
    """Extract the last frame of a video to ``output_path`` via ffmpeg."""
    if not video_path.exists():
        raise AppError(
            "FFMPEG_FAILED",
            f"clip is missing: {video_path}",
            http_status=500,
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    run = runner or _runner_with_timeout(timeout_seconds)
    bin_path = ffmpeg_binary(binary)
    code, _stdout, stderr = await run(
        bin_path,
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


async def stitch_clips(
    clip_paths: list[Path],
    output_path: Path,
    *,
    runner: FfmpegRunner | None = None,
    binary: str | None = None,
    timeout_seconds: float = 120.0,
) -> Path:
    """Concatenate clips in order into ``output_path`` via ffmpeg.

    Tries stream copy first; if the provider codecs cannot concat with
    ``-c copy``, re-encodes to H.264 rather than faking a successful stitch.
    """
    if not clip_paths:
        raise AppError(
            "ASSEMBLE_NO_CLIPS",
            "No clips available to assemble",
            http_status=500,
        )
    missing = [str(path) for path in clip_paths if not path.exists()]
    if missing:
        raise AppError(
            "FFMPEG_FAILED",
            f"clip files missing: {missing}",
            http_status=500,
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    list_path = output_path.with_name(output_path.stem + ".concat.txt")
    lines = []
    for clip in clip_paths:
        escaped = str(clip).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    run = runner or _runner_with_timeout(timeout_seconds)
    bin_path = ffmpeg_binary(binary)
    copy_args = (
        bin_path,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-c",
        "copy",
        str(output_path),
    )
    code, _stdout, stderr = await run(*copy_args)
    if code == 0 and output_path.exists():
        return output_path

    reencode_args = (
        bin_path,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(output_path),
    )
    code, _stdout, stderr = await run(*reencode_args)
    if code != 0:
        detail = stderr.decode("utf-8", errors="replace")[:500]
        raise AppError(
            "FFMPEG_FAILED",
            f"ffmpeg failed stitching clips: {detail}",
            http_status=500,
        )
    if not output_path.exists():
        raise AppError(
            "FFMPEG_FAILED",
            "ffmpeg completed but assembled output is missing",
            http_status=500,
        )
    return output_path
