"""Run the OCI processing agent with an explicit state capability map."""

from __future__ import annotations

import asyncio

from localizer_agent.config import AgentConfig
from localizer_agent.control_plane import AgentControlPlaneClient
from localizer_agent.media import MediaProcessor
from localizer_agent.pipeline.supervisor import ProcessingSupervisor, StageHandler
from localizer_agent.pipeline.transcription_stage import TranscriptionHandler
from localizer_agent.structured_logging import create_logger
from localizer_agent.supervisor import ImportSupervisor
from localizer_agent.transcription.whisper_cpp import WhisperCpp
from localizer_agent.workspace import JobWorkspaceManager


async def run() -> None:
    config = AgentConfig.from_env()
    logger = create_logger()
    workspace_manager = JobWorkspaceManager(config.workspace_root, config.minimum_free_bytes)
    recovered = workspace_manager.recover()
    if recovered:
        logger.info("workspace_recovery_detected", recovered_job_count=len(recovered))
    client = AgentControlPlaneClient(config.control_plane_url, config.access_token, request_timeout_seconds=config.request_timeout_seconds)
    import_supervisor = ImportSupervisor(
        client,
        workspace_manager,
        MediaProcessor(),
        config.inbox_root,
        config.max_source_bytes,
        config.heartbeat_interval_seconds,
        config.request_timeout_seconds,
    )
    handlers: dict[str, StageHandler] = {"QUEUED": import_supervisor, "INGESTING": import_supervisor}
    if config.whisper_binary_path is not None and config.whisper_model_path is not None and config.whisper_model_sha256 is not None:
        whisper = WhisperCpp(
            config.whisper_binary_path,
            config.whisper_model_path,
            binary_version=config.whisper_binary_version,
            expected_model_sha256=config.whisper_model_sha256,
        )
        handlers["TRANSCRIBING"] = TranscriptionHandler(
            client,
            workspace_manager,
            whisper,
            config_version=f"{config.whisper_binary_version}:{config.whisper_model_sha256}",
            heartbeat_interval_seconds=config.heartbeat_interval_seconds,
            tool_version=config.whisper_binary_version,
        )
    supervisor = ProcessingSupervisor(
        client,
        handlers,
        supported_job_types=["IMPORT"],
    )
    while True:
        try:
            processed = await supervisor.run_once()
            if not processed:
                await asyncio.sleep(config.poll_interval_seconds)
        except Exception as error:
            logger.error("processing_cycle_failed", error_type=type(error).__name__)
            await asyncio.sleep(config.poll_interval_seconds)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
