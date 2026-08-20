"""Local CLI: ``python -m app seed-tenant``."""

from __future__ import annotations

import argparse
import asyncio
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import Settings
from app.db.models import Tenant
from app.db.session import create_engine_from_settings, get_sessionmaker


async def seed_tenant(settings: Settings | None = None) -> UUID:
    """Insert the configured tenant if missing. Safe to run twice."""
    settings = settings or Settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is required")
    if settings.tenant_id:
        tenant_id = UUID(settings.tenant_id)
    else:
        raise SystemExit(
            "TENANT_ID is required. Set it in .env to a UUID you will send as X-Tenant-Id."
        )
    engine = create_engine_from_settings(settings)
    maker = get_sessionmaker(engine)
    try:
        async with maker() as session:
            dialect = session.bind.dialect.name if session.bind is not None else "postgresql"
            insert = sqlite_insert if dialect == "sqlite" else postgresql_insert
            stmt = insert(Tenant).values(id=tenant_id, name=settings.tenant_name)
            stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
            await session.execute(stmt)
            await session.commit()
            existing = await session.scalar(select(Tenant).where(Tenant.id == tenant_id))
            if existing is None:
                # SQLite ON CONFLICT needs the PK; if the row still missing, insert plainly.
                session.add(Tenant(id=tenant_id, name=settings.tenant_name))
                await session.commit()
                existing = await session.scalar(select(Tenant).where(Tenant.id == tenant_id))
        print(f"tenant ready id={tenant_id} name={settings.tenant_name}")
        return tenant_id
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed-tenant", help="Idempotently insert TENANT_ID into tenants")
    args = parser.parse_args(argv)
    if args.command == "seed-tenant":
        asyncio.run(seed_tenant())


if __name__ == "__main__":
    main()
