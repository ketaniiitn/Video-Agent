from app.providers.protocols import GenerateClipRequest, GenerateClipResult

# Minimal placeholder bytes — not a real MP4; tests mock ffmpeg separately.
FAKE_MP4_BYTES = b"FAKEMP4\x00\x00\x00\x18ftypisom"


class FakeVideoProvider:
    """Injectable provider for tests — never calls a real video API."""

    def __init__(self, *, cost_usd: float = 0.01) -> None:
        self.cost_usd = cost_usd
        self.calls: list[GenerateClipRequest] = []

    def capabilities(self) -> set[str]:
        return {"frame_conditioning", "text_to_video"}

    async def generate_clip(self, req: GenerateClipRequest) -> GenerateClipResult:
        self.calls.append(req.model_copy())
        return GenerateClipResult(
            video_bytes=FAKE_MP4_BYTES,
            cost_usd=self.cost_usd,
            provider_id="fake",
            seed=req.seed,
        )
