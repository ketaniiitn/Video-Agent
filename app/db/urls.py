"""Normalize Postgres URLs for SQLAlchemy asyncpg and psycopg (Neon SSL)."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def to_asyncpg_url(url: str) -> str:
    """Return a SQLAlchemy ``postgresql+asyncpg://`` URL.

    Accepts Neon-style ``postgresql://`` / ``postgres://`` DSNs and maps
    ``sslmode=require`` to asyncpg's ``ssl=require``.
    """
    if not url or url.startswith("sqlite"):
        return url
    normalized = url.replace("postgres://", "postgresql://", 1)
    if normalized.startswith("postgresql+asyncpg://"):
        rest = normalized
    elif normalized.startswith("postgresql+psycopg://"):
        rest = "postgresql+asyncpg://" + normalized[len("postgresql+psycopg://") :]
    elif normalized.startswith("postgresql://"):
        rest = "postgresql+asyncpg://" + normalized[len("postgresql://") :]
    else:
        rest = normalized
    return _with_ssl_query(rest, asyncpg=True)


def to_psycopg_url(url: str) -> str:
    """Return a ``postgresql://`` DSN for psycopg (LangGraph checkpointer)."""
    if not url or url.startswith("sqlite"):
        return url
    async_url = to_asyncpg_url(url)
    psycopg = async_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return _with_ssl_query(psycopg, asyncpg=False)


def _with_ssl_query(url: str, *, asyncpg: bool) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    host = (parts.hostname or "").lower()
    if asyncpg:
        query.pop("channel_binding", None)
        if "sslmode" in query and "ssl" not in query:
            mode = query.pop("sslmode")
            query["ssl"] = "require" if mode not in {"disable", "allow", "prefer"} else "false"
        if "neon.tech" in host and "ssl" not in query:
            query["ssl"] = "require"
    else:
        if "ssl" in query and "sslmode" not in query:
            flag = query.pop("ssl")
            query["sslmode"] = (
                "require" if flag.lower() in {"require", "true", "1"} else "disable"
            )
        if "neon.tech" in host and "sslmode" not in query:
            query["sslmode"] = "require"
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )
