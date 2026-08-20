import logging

from app.observability.logging import configure_logging, log_json


def test_configure_logging_emits_app_info(caplog):
    configure_logging()
    caplog.set_level(logging.INFO, logger="app")
    log_json(logging.getLogger("app.jobs.runner"), "pipeline_phase", phase="test")
    assert any(
        '"message": "pipeline_phase"' in record.getMessage()
        and '"phase": "test"' in record.getMessage()
        for record in caplog.records
    )


def test_configure_logging_is_idempotent():
    configure_logging()
    configure_logging()
    app_logger = logging.getLogger("app")
    owned = [
        handler
        for handler in app_logger.handlers
        if getattr(handler, "_video_agent", False)
    ]
    assert len(owned) == 1
