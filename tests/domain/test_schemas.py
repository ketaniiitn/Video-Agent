from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.schemas import (
    Beat,
    ContinuityBible,
    JobStatus,
    ShotStatus,
    ShotSummary,
    StoryPlan,
)


def test_story_plan_requires_four_beats_of_ten_seconds():
    beats = [
        Beat(name="setup", duration_seconds=10, action="a", camera="wide"),
        Beat(name="development", duration_seconds=10, action="b", camera="med"),
        Beat(name="turn", duration_seconds=10, action="c", camera="close"),
        Beat(name="resolution", duration_seconds=10, action="d", camera="wide"),
    ]
    plan = StoryPlan(beats=beats)
    assert len(plan.beats) == 4
    assert sum(b.duration_seconds for b in plan.beats) == 40


def test_story_plan_rejects_wrong_beat_count():
    with pytest.raises(ValidationError):
        StoryPlan(
            beats=[
                Beat(name="setup", duration_seconds=10, action="a", camera="w"),
                Beat(name="development", duration_seconds=10, action="b", camera="m"),
                Beat(name="turn", duration_seconds=10, action="c", camera="c"),
            ]
        )


def test_story_plan_rejects_wrong_beat_names_or_order():
    with pytest.raises(ValidationError):
        StoryPlan(
            beats=[
                Beat(name="development", duration_seconds=10, action="a", camera="w"),
                Beat(name="setup", duration_seconds=10, action="b", camera="m"),
                Beat(name="turn", duration_seconds=10, action="c", camera="c"),
                Beat(name="resolution", duration_seconds=10, action="d", camera="w"),
            ]
        )


def test_story_plan_rejects_wrong_duration():
    with pytest.raises(ValidationError):
        StoryPlan(
            beats=[
                Beat(name="setup", duration_seconds=9, action="a", camera="w"),
                Beat(name="development", duration_seconds=10, action="b", camera="m"),
                Beat(name="turn", duration_seconds=10, action="c", camera="c"),
                Beat(name="resolution", duration_seconds=11, action="d", camera="w"),
            ]
        )


def test_job_status_includes_all_harness_mapped_values():
    names = {s.value for s in JobStatus}
    assert names >= {
        "QUEUED",
        "RUNNING",
        "BIBLE_LOCKED",
        "SHOTS_READY",
        "DELIVERED",
        "PARTIAL",
        "FAILED",
        "FAILED_NO_PROGRESS",
        "ESCALATED",
    }


def test_shot_status_and_summary_fields():
    assert {status.value for status in ShotStatus} == {
        "PENDING",
        "RUNNING",
        "SUCCEEDED",
        "FAILED",
    }

    summary = ShotSummary(
        beat_index=1,
        status=ShotStatus.SUCCEEDED,
        clip_path="/media/shot_1.mp4",
        frame_path="/media/frame_1.jpg",
        cost_usd=Decimal("0.125000"),
    )

    assert summary.beat_index == 1
    assert summary.status is ShotStatus.SUCCEEDED
    assert summary.cost_usd == Decimal("0.125000")


def test_continuity_bible_fields():
    bible = ContinuityBible(
        character="hero",
        wardrobe="coat",
        location="alley",
        lighting="neon",
        palette="cyan/magenta",
        lens="35mm",
    )
    assert bible.character == "hero"
