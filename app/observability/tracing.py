from contextvars import ContextVar
from uuid import uuid4

current_trace_id: ContextVar[str | None] = ContextVar("current_trace_id", default=None)


def mint_trace_id() -> str:
    return f"tr_{uuid4().hex}"


def resolve_trace_id() -> str:
    """Return the request's trace id, minting one if nothing set it yet.

    Normal requests get their trace id from the ASGI middleware in
    ``app/main.py``, minted before header validation. This lazy fallback
    only fires for code paths exercised outside that middleware (e.g. unit
    tests that raise ``AppError`` directly).
    """
    trace_id = current_trace_id.get()
    if trace_id is None:
        trace_id = mint_trace_id()
        current_trace_id.set(trace_id)
    return trace_id
