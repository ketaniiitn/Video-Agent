from app.providers.protocols import GenerateClipRequest, GenerateClipResult

# Minimal placeholder bytes — not a real MP4; tests mock ffmpeg separately.
FAKE_MP4_BYTES = b"FAKEMP4\x00\x00\x00\x18ftypisom"


class FakeVideoProvider:
    """Injectable provider for tests — never calls a real video API."""

    def __init__(
        self, *, cost_usd: float = 0.01, fail_times: int = 0, fail_code: str = "PROVIDER_UNAVAILABLE"
    ) -> None:
        self.cost_usd = cost_usd
        self.fail_times = fail_times
        self.fail_code = fail_code
        self.calls: list[GenerateClipRequest] = []

    def capabilities(self) -> set[str]:
        return {"frame_conditioning", "text_to_video"}

    async def generate_clip(self, req: GenerateClipRequest) -> GenerateClipResult:
        self.calls.append(req.model_copy())
        if self.fail_times > 0:
            self.fail_times -= 1
            from app.domain.errors import AppError

            raise AppError(self.fail_code, "fake provider failure", http_status=502)
        return GenerateClipResult(
            video_bytes=FAKE_MP4_BYTES,
            cost_usd=self.cost_usd,
            provider_id="fake",
            seed=req.seed,
        )
