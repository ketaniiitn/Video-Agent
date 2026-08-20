from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.domain.schemas import JobStatus, ShotStatus


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_jobs_id_tenant_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status", native_enum=True),
        nullable=False,
        default=JobStatus.QUEUED,
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    trace_id: Mapped[str] = mapped_column(String(255), nullable=False)
    budget_max_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    budget_max_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    budget_max_iterations: Mapped[int] = mapped_column(Integer, nullable=False)
    budget_max_wall_clock_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    budget_used_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, default=Decimal("0")
    )
    budget_used_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    budget_used_iterations: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    degraded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    assembled_path: Mapped[str | None] = mapped_column(String(1024))
    download_url: Mapped[str | None] = mapped_column(String(2048))
    thumbnail_url: Mapped[str | None] = mapped_column(String(2048))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class StoryPlanRow(Base):
    __tablename__ = "story_plans"
    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id", "tenant_id"],
            ["jobs.id", "jobs.tenant_id"],
            name="fk_story_plans_job_tenant",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    beats_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ContinuityBibleRow(Base):
    __tablename__ = "continuity_bibles"
    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id", "tenant_id"],
            ["jobs.id", "jobs.tenant_id"],
            name="fk_continuity_bibles_job_tenant",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    bible_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Shot(Base):
    __tablename__ = "shots"
    __table_args__ = (
        UniqueConstraint("job_id", "beat_index", name="uq_shots_job_id_beat_index"),
        CheckConstraint(
            "beat_index BETWEEN 1 AND 4",
            name="ck_shots_beat_index",
        ),
        ForeignKeyConstraint(
            ["job_id", "tenant_id"],
            ["jobs.id", "jobs.tenant_id"],
            name="fk_shots_job_tenant",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    beat_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ShotStatus] = mapped_column(
        Enum(ShotStatus, name="shot_status", native_enum=True),
        nullable=False,
        default=ShotStatus.PENDING,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    clip_path: Mapped[str | None] = mapped_column(String(1024))
    frame_path: Mapped[str | None] = mapped_column(String(1024))
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )
    provider_id: Mapped[str] = mapped_column(String(255), nullable=False)
    seed: Mapped[int | None] = mapped_column(BigInteger)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    qc_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    degraded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    repair_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class QcScore(Base):
    __tablename__ = "qc_scores"
    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id", "tenant_id"],
            ["jobs.id", "jobs.tenant_id"],
            name="fk_qc_scores_job_tenant",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    shot_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("shots.id", ondelete="SET NULL"),
    )
    beat_index: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CostLedger(Base):
    __tablename__ = "cost_ledger"
    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id", "tenant_id"],
            ["jobs.id", "jobs.tenant_id"],
            name="fk_cost_ledger_job_tenant",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    shot_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("shots.id", ondelete="SET NULL"),
    )
    usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    tokens: Mapped[int | None] = mapped_column(BigInteger)
    provider_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("tenant_id", "key", name="uq_idempotency_keys_tenant_key"),
        ForeignKeyConstraint(
            ["job_id", "tenant_id"],
            ["jobs.id", "jobs.tenant_id"],
            name="fk_idempotency_keys_job_tenant",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
