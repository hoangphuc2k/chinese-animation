"""Safe runtime configuration for the processing agent."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse


class ConfigurationError(ValueError):
    """Raised when the agent cannot start with a safe configuration."""


def _required(values: Mapping[str, str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"{name} is required")
    return value


def _positive_int(values: Mapping[str, str], name: str, default: int, maximum: int) -> int:
    raw = values.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be an integer") from error
    if value <= 0 or value > maximum:
        raise ConfigurationError(f"{name} must be between 1 and {maximum}")
    return value


def _control_plane_origin(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ConfigurationError("CONTROL_PLANE_URL must be a credential-free HTTPS origin")
    if parsed.path not in ("", "/") or parsed.params or parsed.query or parsed.fragment:
        raise ConfigurationError("CONTROL_PLANE_URL must not include a path, query, or fragment")
    return value.rstrip("/")


def _whisper_configuration(values: Mapping[str, str]) -> tuple[Path | None, Path | None, str | None]:
    binary = values.get("WHISPER_BINARY_PATH", "").strip()
    model = values.get("WHISPER_MODEL_PATH", "").strip()
    model_sha256 = values.get("WHISPER_MODEL_SHA256", "").strip()
    if not binary and not model and not model_sha256:
        return None, None, None
    if not binary or not model or len(model_sha256) != 64 or any(character not in "0123456789abcdef" for character in model_sha256):
        raise ConfigurationError("Whisper requires binary/model paths and a lowercase model SHA-256 together")
    return Path(binary).expanduser(), Path(model).expanduser(), model_sha256


@dataclass(frozen=True, repr=False)
class AgentConfig:
    control_plane_url: str
    access_token: str = field(repr=False)
    inbox_root: Path
    workspace_root: Path
    poll_interval_seconds: int = 5
    heartbeat_interval_seconds: int = 30
    request_timeout_seconds: int = 30
    max_source_bytes: int = 20 * 1024 * 1024 * 1024
    minimum_free_bytes: int = 5 * 1024 * 1024 * 1024
    whisper_binary_path: Path | None = None
    whisper_model_path: Path | None = None
    whisper_model_sha256: str | None = None
    whisper_binary_version: str = "whisper.cpp"

    @classmethod
    def from_env(cls, values: Mapping[str, str] | None = None) -> "AgentConfig":
        env = os.environ if values is None else values
        workspace_root = Path(_required(env, "WORKSPACE_ROOT")).expanduser()
        inbox_root = Path(_required(env, "INBOX_ROOT")).expanduser()
        whisper_binary_path, whisper_model_path, whisper_model_sha256 = _whisper_configuration(env)
        return cls(
            access_token=_required(env, "AGENT_ACCESS_TOKEN"),
            control_plane_url=_control_plane_origin(_required(env, "CONTROL_PLANE_URL")),
            heartbeat_interval_seconds=_positive_int(env, "HEARTBEAT_INTERVAL_SECONDS", 30, 300),
            inbox_root=inbox_root,
            max_source_bytes=_positive_int(env, "MAX_SOURCE_BYTES", 20 * 1024 * 1024 * 1024, 100 * 1024 * 1024 * 1024),
            minimum_free_bytes=_positive_int(env, "MINIMUM_FREE_BYTES", 5 * 1024 * 1024 * 1024, 100 * 1024 * 1024 * 1024),
            poll_interval_seconds=_positive_int(env, "POLL_INTERVAL_SECONDS", 5, 300),
            request_timeout_seconds=_positive_int(env, "REQUEST_TIMEOUT_SECONDS", 30, 300),
            whisper_binary_path=whisper_binary_path,
            whisper_binary_version=env.get("WHISPER_BINARY_VERSION", "whisper.cpp").strip() or "whisper.cpp",
            whisper_model_path=whisper_model_path,
            whisper_model_sha256=whisper_model_sha256,
            workspace_root=workspace_root,
        )
