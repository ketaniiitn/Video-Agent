from uuid import uuid4

import pytest

from app.config import Settings
from app.domain.errors import AppError
from app.storage.presign import presign_url, verify_signature


def test_presign_roundtrip():
    settings = Settings(_env_file=None, presign_secret="secret", app_base_url="http://x")
    job_id = uuid4()
    url = presign_url(job_id=job_id, artifact="assembled.mp4", settings=settings)
    assert "assembled.mp4" in url
    expires = int(url.split("expires=")[1].split("&")[0])
    sig = url.split("sig=")[1]
    verify_signature(
        job_id=job_id,
        artifact="assembled.mp4",
        expires=expires,
        signature=sig,
        settings=settings,
    )


def test_presign_rejects_bad_signature():
    settings = Settings(_env_file=None, presign_secret="secret")
    job_id = uuid4()
    with pytest.raises(AppError) as ei:
        verify_signature(
            job_id=job_id,
            artifact="assembled.mp4",
            expires=9999999999,
            signature="deadbeef",
            settings=settings,
        )
    assert ei.value.code == "ARTIFACT_FORBIDDEN"
