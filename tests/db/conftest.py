import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


# Local PostgreSQL:
# docker run --rm --name video-agent-test-db -e POSTGRES_PASSWORD=postgres \
#   -e POSTGRES_DB=video_agent_test -p 5433:5432 postgres:16
# Then run:
# TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/video_agent_test \
#   alembic upgrade head && TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/video_agent_test pytest tests/db -v
@pytest_asyncio.fixture
async def db_engine():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is unset; PostgreSQL RLS test requires a database")

    admin_engine = create_async_engine(database_url)
    role_name: str | None = None
    async with admin_engine.connect() as connection:
        bypasses_rls = await connection.scalar(
            text(
                "SELECT rolsuper OR rolbypassrls "
                "FROM pg_roles WHERE rolname = current_user"
            )
        )

    if bypasses_rls:
        role_name = f"rls_test_{uuid.uuid4().hex}"
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE ROLE "{role_name}" NOLOGIN'))
            await connection.execute(
                text(f'GRANT USAGE ON SCHEMA public TO "{role_name}"')
            )
            await connection.execute(
                text(
                    f'GRANT SELECT, INSERT, UPDATE, DELETE '
                    f'ON ALL TABLES IN SCHEMA public TO "{role_name}"'
                )
            )
        engine = create_async_engine(
            database_url,
            connect_args={"server_settings": {"role": role_name}},
        )
    else:
        engine = admin_engine

    try:
        yield engine
    finally:
        await engine.dispose()
        if role_name is not None:
            async with admin_engine.begin() as connection:
                await connection.execute(text(f'DROP ROLE "{role_name}"'))
        await admin_engine.dispose()
