"""Structured logs that never emit source locations or credentials."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any


_SENSITIVE_KEY = ("authorization", "cookie", "credential", "header", "locator", "password", "path", "secret", "token", "uri", "url")
_REDACTED = "[REDACTED]"


def _sanitize(value: Any, key: str | None = None) -> Any:
    if key and any(fragment in key.lower() for fragment in _SENSITIVE_KEY):
        return _REDACTED
    if isinstance(value, dict):
        return {nested_key: _sanitize(nested_value, nested_key) for nested_key, nested_value in value.items()}
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


class AgentLogger:
    def __init__(self, write: Callable[[str], None]) -> None:
        self._write = write

    def info(self, event: str, **fields: Any) -> None:
        self._write(json.dumps({"atUtc": datetime.now(UTC).isoformat(), "event": event, "level": "INFO", **_sanitize(fields)}, separators=(",", ":")))

    def warning(self, event: str, **fields: Any) -> None:
        self._write(json.dumps({"atUtc": datetime.now(UTC).isoformat(), "event": event, "level": "WARN", **_sanitize(fields)}, separators=(",", ":")))

    def error(self, event: str, **fields: Any) -> None:
        self._write(json.dumps({"atUtc": datetime.now(UTC).isoformat(), "event": event, "level": "ERROR", **_sanitize(fields)}, separators=(",", ":")))


def create_logger(write: Callable[[str], None] = print) -> AgentLogger:
    return AgentLogger(write)
