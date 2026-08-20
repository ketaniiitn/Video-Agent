from app.db.urls import to_asyncpg_url, to_psycopg_url


def test_neon_postgres_url_becomes_asyncpg_with_ssl():
    url = to_asyncpg_url(
        "postgresql://user:secret@ep-foo.region.aws.neon.tech/neondb?sslmode=require"
    )
    assert url.startswith("postgresql+asyncpg://")
    assert "ssl=require" in url
    assert "sslmode" not in url


def test_neon_channel_binding_is_stripped_for_asyncpg():
    url = to_asyncpg_url(
        "postgresql://user:secret@ep-foo.region.aws.neon.tech/neondb"
        "?sslmode=require&channel_binding=require"
    )
    assert "channel_binding" not in url
    assert "ssl=require" in url


def test_asyncpg_url_passthrough():
    src = "postgresql+asyncpg://u:p@db.example/app?ssl=require"
    assert to_asyncpg_url(src) == src


def test_psycopg_url_uses_sslmode_for_checkpointer():
    url = to_psycopg_url(
        "postgresql+asyncpg://user:secret@ep-foo.region.aws.neon.tech/neondb?ssl=require"
    )
    assert url.startswith("postgresql://")
    assert "asyncpg" not in url
    assert "sslmode=require" in url


def test_sqlite_unchanged():
    assert to_asyncpg_url("sqlite+aiosqlite:///:memory:") == "sqlite+aiosqlite:///:memory:"
    assert to_psycopg_url("sqlite+aiosqlite:///:memory:") == "sqlite+aiosqlite:///:memory:"
