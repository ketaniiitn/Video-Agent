from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from app.db.models import (
    ContinuityBibleRow,
    CostLedger,
    IdempotencyKey,
    Job,
    Shot,
    StoryPlanRow,
)
from app.domain.schemas import JobStatus, ShotStatus


def test_job_has_all_budget_and_lifecycle_columns():
    expected = {
        "budget_max_usd",
        "budget_max_tokens",
        "budget_max_iterations",
        "budget_max_wall_clock_seconds",
        "budget_used_usd",
        "budget_used_tokens",
        "budget_used_iterations",
        "started_at",
    }

    assert expected <= set(Job.__table__.columns.keys())
    assert set(Job.__table__.c.status.type.enums) == {status.value for status in JobStatus}


def test_job_artifacts_are_unique_and_tenant_scoped():
    assert StoryPlanRow.__table__.c.job_id.unique
    assert ContinuityBibleRow.__table__.c.job_id.unique
    assert "tenant_id" in StoryPlanRow.__table__.columns
    assert "tenant_id" in ContinuityBibleRow.__table__.columns


def test_job_has_tenant_aware_reference_key():
    constraints = {
        tuple(constraint.columns.keys())
        for constraint in Job.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("id", "tenant_id") in constraints


def test_job_references_include_tenant_id():
    for table in (
        StoryPlanRow.__table__,
        ContinuityBibleRow.__table__,
        IdempotencyKey.__table__,
    ):
        foreign_keys = {
            (
                tuple(constraint.columns.keys()),
                tuple(element.target_fullname for element in constraint.elements),
            )
            for constraint in table.constraints
            if isinstance(constraint, ForeignKeyConstraint)
        }

        assert (
            ("job_id", "tenant_id"),
            ("jobs.id", "jobs.tenant_id"),
        ) in foreign_keys


def test_idempotency_key_is_unique_per_tenant():
    constraints = {
        tuple(constraint.columns.keys())
        for constraint in IdempotencyKey.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("tenant_id", "key") in constraints


def test_shot_metadata_enforces_beat_and_job_identity():
    assert {
        "id",
        "tenant_id",
        "job_id",
        "beat_index",
        "status",
        "attempt_count",
        "clip_path",
        "frame_path",
        "cost_usd",
        "provider_id",
        "seed",
        "prompt",
        "created_at",
        "updated_at",
    } == set(Shot.__table__.columns.keys())
    assert set(Shot.__table__.c.status.type.enums) == {
        status.value for status in ShotStatus
    }

    unique_constraints = {
        tuple(constraint.columns.keys())
        for constraint in Shot.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    check_constraints = {
        str(constraint.sqltext)
        for constraint in Shot.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert ("job_id", "beat_index") in unique_constraints
    assert "beat_index BETWEEN 1 AND 4" in check_constraints


def test_shot_and_cost_ledger_have_tenant_aware_job_references():
    for table in (Shot.__table__, CostLedger.__table__):
        foreign_keys = {
            (
                tuple(constraint.columns.keys()),
                tuple(element.target_fullname for element in constraint.elements),
            )
            for constraint in table.constraints
            if isinstance(constraint, ForeignKeyConstraint)
        }

        assert (
            ("job_id", "tenant_id"),
            ("jobs.id", "jobs.tenant_id"),
        ) in foreign_keys


def test_cost_ledger_metadata_and_nullable_shot_reference():
    assert {
        "id",
        "tenant_id",
        "job_id",
        "shot_id",
        "usd",
        "tokens",
        "provider_id",
        "created_at",
    } == set(CostLedger.__table__.columns.keys())
    assert CostLedger.__table__.c.shot_id.nullable
    assert CostLedger.__table__.c.tokens.nullable
    assert {
        foreign_key.target_fullname
        for foreign_key in CostLedger.__table__.c.shot_id.foreign_keys
    } == {"shots.id"}
