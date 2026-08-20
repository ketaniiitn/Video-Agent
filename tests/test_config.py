from app.config import Settings


def test_database_url_for_sweep_falls_back_to_database_url_when_unset():
    """Documented fallback (see .env.example / db/session.py get_raw_session):
    unset DATABASE_URL_SWEEP means dev/tests use one DSN for both the
    per-request and sweep session factories."""
    settings = Settings(_env_file=None, database_url="sqlite+aiosqlite:///:memory:")
    assert settings.database_url_sweep is None
    assert settings.database_url_for_sweep() == settings.database_url


def test_database_url_for_sweep_uses_override_when_set():
    """Production must be able to point the sweep at a distinct,
    BYPASSRLS-capable DSN without touching the per-request DSN."""
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://app_role@host/db",
        database_url_sweep="postgresql+asyncpg://sweep_role@host/db",
    )
    assert settings.database_url_for_sweep() == "postgresql+asyncpg://sweep_role@host/db"
    assert settings.database_url == "postgresql+asyncpg://app_role@host/db"
