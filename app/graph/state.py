from typing import Literal, NotRequired, TypedDict

from app.domain.schemas import ContinuityBible, StoryPlan


class VideoAgentState(TypedDict):
    job_id: str
    tenant_id: str
    prompt: str
    story_plan: NotRequired[StoryPlan | dict]
    continuity_bible: NotRequired[ContinuityBible | dict]
    budget_used_usd: float
    budget_used_tokens: int
    budget_used_iterations: int
    budget_max_usd: float
    budget_max_tokens: int
    budget_max_iterations: int
    budget_max_wall_clock_seconds: int
    started_at_iso: str
    outcome: NotRequired[
        Literal["SUCCESS", "PARTIAL", "FAILED", "FAILED_NO_PROGRESS", "ESCALATED"]
    ]
    prior_frame_path: NotRequired[str | None]
    current_clip_path: NotRequired[str | None]
    current_beat_index: NotRequired[int]
    shots_completed: NotRequired[bool]
    qc_passed: NotRequired[bool]
    repair_count: NotRequired[int]
    job_degraded: NotRequired[bool]
    last_failure_signature: NotRequired[str | None]
    last_error_message: NotRequired[str | None]
    assembled_path: NotRequired[str | None]
    download_url: NotRequired[str | None]
    delivered: NotRequired[bool]
