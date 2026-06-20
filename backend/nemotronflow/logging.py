"""Structured logging for NemotronFlow.

Every record carries `session_id` (one per process boot) and, when relevant,
`utterance_id`. Output is JSONL to a rotating file plus a human-readable
mirror to stderr in dev. A module-level test sink captures the last record.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
import uuid
from pathlib import Path
from typing import Any

import structlog

_CURRENT_SESSION_ID: str = ""
_LAST_RECORD: dict[str, Any] | None = None


def reset_session(session_id: str | None = None) -> None:
    """(Test helper.) Start a fresh session id and clear the test sink."""
    global _CURRENT_SESSION_ID, _LAST_RECORD  # noqa: PLW0603
    _CURRENT_SESSION_ID = session_id or uuid.uuid4().hex
    _LAST_RECORD = None


def session_id() -> str:
    global _CURRENT_SESSION_ID  # noqa: PLW0603
    if not _CURRENT_SESSION_ID:
        _CURRENT_SESSION_ID = uuid.uuid4().hex
    return _CURRENT_SESSION_ID


def _test_sink(_logger: Any, _method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    global _LAST_RECORD  # noqa: PLW0603
    _LAST_RECORD = dict(event_dict)
    return event_dict


def _session_injector(
    _logger: Any, _method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    event_dict.setdefault("session_id", session_id())
    return event_dict


def configure(level: str = "INFO", log_dir: Path | None = None) -> None:
    """Configure structlog. Idempotent. Safe in tests with log_dir=None."""
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        _session_injector,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _test_sink,
        structlog.processors.JSONRenderer(),
    ]
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=False,
    )
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_dir / "nemotronflow.log", maxBytes=5 * 1024 * 1024, backupCount=5
        )
        root = logging.getLogger("nemotronflow")
        root.addHandler(handler)
        root.setLevel(getattr(logging, level.upper(), logging.INFO))


def get_logger(name: str = "nemotronflow") -> Any:
    """Return a bound logger. Also exposes get_logger.last_record() for tests."""
    log = structlog.get_logger(name)

    def _last_record() -> dict[str, Any] | None:
        return _LAST_RECORD

    setattr(get_logger, "last_record", _last_record)  # noqa: B010
    return log
