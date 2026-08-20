from typing import Protocol

from pydantic import BaseModel, Field


class GenerateClipRequest(BaseModel):
    prompt: str
    duration_seconds: int = 10
    prior_frame_path: str | None = None
    seed: int | None = None


class GenerateClipResult(BaseModel):
    video_bytes: bytes
    cost_usd: float = 0.0
    provider_id: str
    seed: int | None = None


class VideoProvider(Protocol):
    def capabilities(self) -> set[str]:
        """Capability tokens, e.g. ``frame_conditioning``."""

    async def generate_clip(self, req: GenerateClipRequest) -> GenerateClipResult:
        """Generate a single clip; may use prior frame for continuity."""
