from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, field_validator


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    BIBLE_LOCKED = "BIBLE_LOCKED"
    SHOTS_READY = "SHOTS_READY"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    FAILED_NO_PROGRESS = "FAILED_NO_PROGRESS"
    ESCALATED = "ESCALATED"


class ShotStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ShotSummary(BaseModel):
    beat_index: int
    status: ShotStatus
    clip_path: str | None
    frame_path: str | None
    cost_usd: Decimal


BEAT_NAMES = ("setup", "development", "turn", "resolution")


class Beat(BaseModel):
    name: str
    duration_seconds: int
    action: str
    camera: str


class StoryPlan(BaseModel):
    beats: list[Beat]

    @field_validator("beats")
    @classmethod
    def validate_beats(cls, beats: list[Beat]) -> list[Beat]:
        if len(beats) != 4:
            raise ValueError("StoryPlan must have exactly 4 beats")
        for beat, expected_name in zip(beats, BEAT_NAMES, strict=True):
            if beat.name != expected_name:
                raise ValueError(
                    f"Beat names must be {', '.join(BEAT_NAMES)} in order"
                )
            if beat.duration_seconds != 10:
                raise ValueError("Each beat must be exactly 10 seconds")
        return beats


class ContinuityBible(BaseModel):
    character: str
    wardrobe: str
    location: str
    lighting: str
    palette: str
    lens: str


class BudgetCaps(BaseModel):
    budget_max_usd: float = 1.0
    budget_max_tokens: int = 50_000
    budget_max_iterations: int = 20
    budget_max_wall_clock_seconds: int = 600


class CreateJobRequest(BaseModel):
    prompt: str
    budget: BudgetCaps | None = None
