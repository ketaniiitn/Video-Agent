"""HMAC-signed local download URLs. Cloud object storage is out of M3b."""

from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import quote, urlencode
from uuid import UUID

from app.config import Settings
from app.domain.errors import AppError

ALLOWED_ARTIFACTS = frozenset(
    {
        "assembled.mp4",
        "thumbnail.jpg",
        "shot_1.mp4",
        "shot_2.mp4",
        "shot_3.mp4",
        "shot_4.mp4",
        "frame_1.jpg",
        "frame_2.jpg",
        "frame_3.jpg",
        "frame_4.jpg",
    }
)


def presign_url(
    *,
    job_id: UUID,
    artifact: str,
    settings: Settings,
    ttl_seconds: int | None = None,
) -> str:
    if artifact not in ALLOWED_ARTIFACTS:
        raise AppError("ARTIFACT_UNKNOWN", f"Unknown artifact {artifact}", http_status=400)
    expires = int(time.time()) + int(ttl_seconds or settings.presigned_url_ttl_seconds)
    signature = _sign(job_id, artifact, expires, settings.presign_secret)
    query = urlencode({"expires": expires, "sig": signature})
    base = settings.app_base_url.rstrip("/")
    return f"{base}/jobs/{job_id}/artifacts/{quote(artifact)}?{query}"


def verify_signature(
    *,
    job_id: UUID,
    artifact: str,
    expires: int,
    signature: str,
    settings: Settings,
) -> None:
    if artifact not in ALLOWED_ARTIFACTS:
        raise AppError("ARTIFACT_UNKNOWN", f"Unknown artifact {artifact}", http_status=400)
    if int(expires) < int(time.time()):
        raise AppError("ARTIFACT_EXPIRED", "Download URL has expired", http_status=403)
    expected = _sign(job_id, artifact, int(expires), settings.presign_secret)
    if not hmac.compare_digest(expected, signature):
        raise AppError("ARTIFACT_FORBIDDEN", "Invalid download signature", http_status=403)


def _sign(job_id: UUID, artifact: str, expires: int, secret: str) -> str:
    payload = f"{job_id}:{artifact}:{expires}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
