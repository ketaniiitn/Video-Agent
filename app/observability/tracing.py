from contextvars import ContextVar
from uuid import uuid4

current_trace_id: ContextVar[str | None] = ContextVar("current_trace_id", default=None)


def mint_trace_id() -> str:
    return f"tr_{uuid4().hex}"
