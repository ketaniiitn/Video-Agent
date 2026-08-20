import pytest

from app.config import Settings
from app.cache.redis import create_redis


def test_create_redis_extracts_url_from_redis_cli_command():
    client = create_redis("redis-cli -u redis://default:secret@example.com:6379")
    kwargs = client.connection_pool.connection_kwargs
    assert kwargs.get("host") == "example.com"


def test_create_redis_rejects_empty_url():
    with pytest.raises(ValueError, match="REDIS_URL"):
        create_redis("")


def test_create_redis_accepts_rediss_url():
    client = create_redis("rediss://default:secret@example.com:6380/0")
    assert client is not None
    # from_url parses TLS scheme without connecting until first command.
    kwargs = client.connection_pool.connection_kwargs
    assert kwargs.get("host") == "example.com"


def test_empty_gateway_fallback_aliases_from_dotenv(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("GATEWAY_FALLBACK_ALIASES=\n", encoding="utf-8")
    settings = Settings(_env_file=env_path)
    assert settings.gateway_fallback_aliases == {}

