import json
import logging
import sys

from app.observability.tracing import resolve_trace_id

_VIDEO_AGENT_HANDLER = "_video_agent"


def configure_logging() -> None:
    """Send ``app.*`` INFO logs to stderr so uvicorn shows pipeline progress.

    Uvicorn leaves the root logger at WARNING, so ``logger.info`` from nodes
    otherwise never appears in the terminal.
    """
    app_logger = logging.getLogger("app")
    app_logger.setLevel(logging.INFO)
    if not any(getattr(handler, _VIDEO_AGENT_HANDLER, False) for handler in app_logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        setattr(handler, _VIDEO_AGENT_HANDLER, True)
        app_logger.addHandler(handler)
    app_logger.propagate = False
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def log_json(logger: logging.Logger, message: str, **fields: object) -> None:
    """Emit one JSON log line that always carries the Langfuse trace id."""
    payload = {"message": message, "trace_id": resolve_trace_id(), **fields}
    logger.info(json.dumps(payload, default=str))


def log_json_error(logger: logging.Logger, message: str, **fields: object) -> None:
    payload = {"message": message, "trace_id": resolve_trace_id(), **fields}
    logger.error(json.dumps(payload, default=str))
