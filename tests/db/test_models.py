from sqlalchemy import ForeignKeyConstraint, UniqueConstraint

from app.db.models import (
    ContinuityBibleRow,
    IdempotencyKey,
    Job,
    StoryPlanRow,
)
from app.domain.schemas import JobStatus


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
