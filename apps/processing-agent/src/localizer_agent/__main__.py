"""Run the OCI processing agent with an explicit state capability map."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from localizer_agent.config import AgentConfig
from localizer_agent.control_plane import AgentControlPlaneClient
from localizer_agent.localization.gemini_client import GeminiClient
from localizer_agent.media import MediaProcessor
from localizer_agent.pipeline.stages import GeminiTranslationOperations, MixingHandler, PreviewHandler, RenderHandler, TranslationHandler, VoiceHandler
from localizer_agent.pipeline.supervisor import ProcessingSupervisor, StageHandler
from localizer_agent.pipeline.transcription_stage import AudioExtractionHandler, ProbeHandler, TranscriptionHandler
from localizer_agent.preview.registry import PreviewRegistry
from localizer_agent.structured_logging import create_logger
from localizer_agent.supervisor import ImportSupervisor
from localizer_agent.speech.audio import AzureVoiceOperations
from localizer_agent.speech.azure_client import AzureSpeechClient
from localizer_agent.render.ffmpeg import FfmpegRenderOperations
from localizer_agent.transcription.whisper_cpp import WhisperCpp
from localizer_agent.workspace import JobWorkspaceManager


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


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
    job_types_by_state = {"QUEUED": ["IMPORT"], "INGESTING": ["IMPORT"]}
    if config.whisper_binary_path is not None and config.whisper_model_path is not None and config.whisper_model_sha256 is not None:
        whisper = WhisperCpp(
            config.whisper_binary_path,
            config.whisper_model_path,
            binary_version=config.whisper_binary_version,
            expected_model_sha256=config.whisper_model_sha256,
        )
        handlers["PROBING"] = ProbeHandler(
            client,
            heartbeat_interval_seconds=config.heartbeat_interval_seconds,
        )
        job_types_by_state["PROBING"] = ["IMPORT"]
        handlers["EXTRACTING_AUDIO"] = AudioExtractionHandler(
            client,
            workspace_manager,
            whisper,
            config_version="ffmpeg-pcm16-v1",
            heartbeat_interval_seconds=config.heartbeat_interval_seconds,
            tool_version="ffmpeg-pcm16-v1",
        )
        job_types_by_state["EXTRACTING_AUDIO"] = ["IMPORT", "LOCALIZE"]
        handlers["TRANSCRIBING"] = TranscriptionHandler(
            client,
            workspace_manager,
            whisper,
            config_version=f"{config.whisper_binary_version}:{config.whisper_model_sha256}",
            heartbeat_interval_seconds=config.heartbeat_interval_seconds,
            tool_version=config.whisper_binary_version,
        )
        job_types_by_state["TRANSCRIBING"] = ["IMPORT", "LOCALIZE"]
    if config.gemini_api_key is not None and config.gemini_base_url is not None and config.gemini_model_id is not None:
        gemini = GeminiClient(
            config.gemini_base_url,
            config.gemini_model_id,
            config.gemini_api_key,
            timeout_seconds=config.request_timeout_seconds,
        )
        handlers["TRANSLATING"] = TranslationHandler(
            client,
            workspace_manager,
            GeminiTranslationOperations(gemini),
            config_version=f"{config.gemini_model_id}:prompt-v1",
            heartbeat_interval_seconds=config.heartbeat_interval_seconds,
            tool_version=config.gemini_model_id,
        )
        job_types_by_state["TRANSLATING"] = ["IMPORT", "LOCALIZE"]
    if config.azure_speech_endpoint is not None and config.azure_speech_key is not None and config.azure_speech_voice is not None:
        azure_speech = AzureSpeechClient(
            config.azure_speech_endpoint,
            config.azure_speech_key,
            {config.azure_speech_voice},
            timeout_seconds=config.request_timeout_seconds,
        )
        voice_tool_version = f"azure:{config.azure_speech_voice}:riff-16khz-v1"
        handlers["GENERATING_VOICE"] = VoiceHandler(
            client,
            workspace_manager,
            AzureVoiceOperations(azure_speech, config.azure_speech_voice),
            config_version=voice_tool_version,
            heartbeat_interval_seconds=config.heartbeat_interval_seconds,
            tool_version=voice_tool_version,
        )
        job_types_by_state["GENERATING_VOICE"] = ["IMPORT", "LOCALIZE"]
    handlers["MIXING"] = MixingHandler(
        client,
        workspace_manager,
        heartbeat_interval_seconds=config.heartbeat_interval_seconds,
    )
    job_types_by_state["MIXING"] = ["IMPORT", "LOCALIZE", "RENDER"]
    if config.watermark_path is not None and config.watermark_sha256 is not None:
        if not config.watermark_path.is_file() or _sha256(config.watermark_path) != config.watermark_sha256:
            raise RuntimeError("Configured watermark does not match its pinned SHA-256.")
        handlers["RENDERING"] = RenderHandler(
            client,
            workspace_manager,
            FfmpegRenderOperations(timeout_seconds=3600),
            config.watermark_path,
            heartbeat_interval_seconds=config.heartbeat_interval_seconds,
            tool_version="ffmpeg-h264-aac-v1",
        )
        job_types_by_state["RENDERING"] = ["IMPORT", "LOCALIZE", "RENDER"]
    handlers["READY_FOR_PREVIEW"] = PreviewHandler(
        client,
        workspace_manager,
        PreviewRegistry(config.workspace_root / "preview-registry.json"),
        heartbeat_interval_seconds=config.heartbeat_interval_seconds,
    )
    job_types_by_state["READY_FOR_PREVIEW"] = ["IMPORT", "LOCALIZE", "RENDER"]
    supervisor = ProcessingSupervisor(
        client,
        handlers,
        job_types_by_state=job_types_by_state,
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
