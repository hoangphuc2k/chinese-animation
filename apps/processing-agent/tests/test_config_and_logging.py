from __future__ import annotations

from pathlib import Path

import pytest

from localizer_agent.config import AgentConfig, ConfigurationError
from localizer_agent.structured_logging import create_logger


def test_config_loads_required_values_without_exposing_the_service_token_secret(tmp_path: Path) -> None:
    config = AgentConfig.from_env(
        {
            "AGENT_ACCESS_CLIENT_ID": "agent-client.access", "AGENT_ACCESS_CLIENT_SECRET": "sensitive-token",
            "CONTROL_PLANE_URL": "https://control-plane.example.test",
            "INBOX_ROOT": str(tmp_path / "inbox"),
            "WORKSPACE_ROOT": str(tmp_path / "workspace"),
        }
    )

    assert config.control_plane_url == "https://control-plane.example.test"
    assert config.workspace_root == tmp_path / "workspace"
    assert "sensitive-token" not in repr(config)
    assert config.access_service_token.client_id == "agent-client.access"


@pytest.mark.parametrize("url", ["http://control-plane.example.test", "https://control-plane.example.test/path", "not-a-url"])
def test_config_requires_a_https_control_plane_origin(tmp_path: Path, url: str) -> None:
    with pytest.raises(ConfigurationError):
        AgentConfig.from_env(
            {
                "AGENT_ACCESS_CLIENT_ID": "agent-client.access", "AGENT_ACCESS_CLIENT_SECRET": "sensitive-token",
                "CONTROL_PLANE_URL": url,
                "INBOX_ROOT": str(tmp_path / "inbox"),
                "WORKSPACE_ROOT": str(tmp_path / "workspace"),
            }
        )


def test_config_enables_whisper_only_with_a_complete_pinned_toolchain(tmp_path: Path) -> None:
    values = {
        "AGENT_ACCESS_CLIENT_ID": "agent-client.access", "AGENT_ACCESS_CLIENT_SECRET": "sensitive-token",
        "CONTROL_PLANE_URL": "https://control-plane.example.test",
        "INBOX_ROOT": str(tmp_path / "inbox"),
        "WHISPER_BINARY_PATH": str(tmp_path / "whisper-cli"),
        "WHISPER_MODEL_PATH": str(tmp_path / "model.bin"),
        "WHISPER_MODEL_SHA256": "a" * 64,
        "WORKSPACE_ROOT": str(tmp_path / "workspace"),
    }

    config = AgentConfig.from_env(values)

    assert config.whisper_binary_path == tmp_path / "whisper-cli"
    assert config.whisper_model_sha256 == "a" * 64

    values.pop("WHISPER_MODEL_SHA256")
    with pytest.raises(ConfigurationError):
        AgentConfig.from_env(values)


def test_config_enables_gemini_only_with_complete_provider_settings(tmp_path: Path) -> None:
    values = {
        "AGENT_ACCESS_CLIENT_ID": "agent-client.access", "AGENT_ACCESS_CLIENT_SECRET": "sensitive-token",
        "CONTROL_PLANE_URL": "https://control-plane.example.test",
        "GEMINI_API_KEY": "gemini-secret",
        "GEMINI_BASE_URL": "https://generativelanguage.googleapis.com/v1beta",
        "GEMINI_MODEL_ID": "gemini-2.5-flash",
        "INBOX_ROOT": str(tmp_path / "inbox"),
        "WORKSPACE_ROOT": str(tmp_path / "workspace"),
    }

    config = AgentConfig.from_env(values)

    assert config.gemini_model_id == "gemini-2.5-flash"
    assert "gemini-secret" not in repr(config)

    values.pop("GEMINI_MODEL_ID")
    with pytest.raises(ConfigurationError):
        AgentConfig.from_env(values)


def test_config_enables_azure_speech_only_with_complete_provider_settings(tmp_path: Path) -> None:
    values = {
        "AGENT_ACCESS_CLIENT_ID": "agent-client.access", "AGENT_ACCESS_CLIENT_SECRET": "sensitive-token",
        "AZURE_SPEECH_ENDPOINT": "https://eastus.tts.speech.microsoft.com/cognitiveservices/v1",
        "AZURE_SPEECH_KEY": "azure-secret",
        "AZURE_SPEECH_VOICE": "vi-VN-HoaiMyNeural",
        "CONTROL_PLANE_URL": "https://control-plane.example.test",
        "INBOX_ROOT": str(tmp_path / "inbox"),
        "WORKSPACE_ROOT": str(tmp_path / "workspace"),
    }

    config = AgentConfig.from_env(values)

    assert config.azure_speech_voice == "vi-VN-HoaiMyNeural"
    assert "azure-secret" not in repr(config)

    values.pop("AZURE_SPEECH_VOICE")
    with pytest.raises(ConfigurationError):
        AgentConfig.from_env(values)


def test_config_enables_rendering_only_with_a_pinned_watermark(tmp_path: Path) -> None:
    values = {
        "AGENT_ACCESS_CLIENT_ID": "agent-client.access", "AGENT_ACCESS_CLIENT_SECRET": "sensitive-token", "CONTROL_PLANE_URL": "https://control-plane.example.test",
        "INBOX_ROOT": str(tmp_path / "inbox"), "WATERMARK_PATH": str(tmp_path / "watermark.png"),
        "WATERMARK_SHA256": "a" * 64, "WORKSPACE_ROOT": str(tmp_path / "workspace"),
    }
    config = AgentConfig.from_env(values)
    assert config.watermark_path == tmp_path / "watermark.png"
    values.pop("WATERMARK_SHA256")
    with pytest.raises(ConfigurationError):
        AgentConfig.from_env(values)


def test_structured_logs_redact_credentials_urls_and_paths() -> None:
    entries: list[str] = []
    logger = create_logger(entries.append)

    logger.info(
        "ingest_started",
        job_id="00000000-0000-4000-8000-000000000001",
        source_url="https://creator.example.test/private.mp4",
        source_path="/private/workspace/source.mp4",
        token="sensitive-token",
    )

    assert len(entries) == 1
    assert "sensitive-token" not in entries[0]
    assert "creator.example.test" not in entries[0]
    assert "/private/workspace" not in entries[0]
    assert '"event":"ingest_started"' in entries[0]
