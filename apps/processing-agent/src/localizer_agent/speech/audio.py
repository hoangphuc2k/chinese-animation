"""Build deterministic mono narration while preserving approved script timing."""

from __future__ import annotations

import asyncio
import hashlib
import io
import tempfile
import uuid
import wave
from pathlib import Path
from typing import Protocol, Sequence

from localizer_agent.localization.validator import ScriptSegment
from localizer_agent.media import CommandRunner, MediaError, SubprocessRunner
from localizer_agent.speech.timing import fit_timing


_MAX_TEMPO = 1.10


class TimingReviewRequired(RuntimeError):
    """Signals that approved text cannot fit its immutable timing window safely."""


class AzureSynthesizer(Protocol):
    async def synthesize(self, request_id: str, voice: str, text: str) -> bytes: ...


def _pcm(audio: bytes) -> tuple[bytes, int]:
    try:
        with wave.open(io.BytesIO(audio), "rb") as source:
            if source.getnchannels() != 1 or source.getsampwidth() != 2 or source.getframerate() != 16_000 or source.getcomptype() != "NONE":
                raise ValueError("Azure narration must be uncompressed mono 16 kHz 16-bit PCM.")
            frames = source.readframes(source.getnframes())
            return frames, source.getnframes()
    except wave.Error as error:
        raise ValueError("Azure narration is not a valid WAV file.") from error


def _wav(frames: bytes) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(16_000)
        destination.writeframes(frames)
    return output.getvalue()


def build_tempo_command(source: Path, destination: Path, tempo: float) -> list[str]:
    """FFmpeg atempo changes duration without shifting pitch, unlike sample decimation."""
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-filter:a",
        f"atempo={tempo:g}",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        "-f",
        "wav",
        str(destination),
    ]


def _speed_up(frames: bytes, tempo: float, runner: CommandRunner, timeout_seconds: int) -> bytes:
    if tempo == 1.0:
        return frames
    if not 1.0 < tempo <= _MAX_TEMPO:
        raise ValueError(f"Narration tempo must be between 1.0 and {_MAX_TEMPO}.")
    target_frames = max(1, round(len(frames) // 2 / tempo))
    with tempfile.TemporaryDirectory(prefix="localizer-tempo-") as directory:
        source = Path(directory) / "narration.wav"
        destination = Path(directory) / "narration-tempo.wav"
        source.write_bytes(_wav(frames))
        result = runner.run(build_tempo_command(source, destination, tempo), timeout_seconds)
        if result.return_code != 0 or not destination.is_file():
            raise MediaError("FFmpeg narration tempo adjustment failed.")
        try:
            adjusted, _ = _pcm(destination.read_bytes())
        except ValueError as error:
            raise MediaError("FFmpeg narration tempo adjustment produced invalid audio.") from error
    # FFmpeg filter latency can add or drop a few samples; keep the deterministic timing contract exact.
    adjusted = adjusted[: target_frames * 2]
    return adjusted + b"\0\0" * (target_frames - len(adjusted) // 2)


class AzureVoiceOperations:
    def __init__(
        self,
        client: AzureSynthesizer,
        voice: str,
        *,
        runner: CommandRunner | None = None,
        tempo_timeout_seconds: int = 120,
    ) -> None:
        if not voice:
            raise ValueError("An allowlisted Azure voice is required.")
        if tempo_timeout_seconds <= 0:
            raise ValueError("Narration tempo timeout must be positive.")
        self._client = client
        self._voice = voice
        self._runner = runner or SubprocessRunner()
        self._tempo_timeout_seconds = tempo_timeout_seconds

    def synthesize(self, job_id: str, segments: Sequence[ScriptSegment]) -> bytes:
        if not segments:
            raise ValueError("Voice generation requires at least one script segment.")
        output_frames = bytearray()
        for segment in segments:
            start_frame = segment.start_ms * 16
            if len(output_frames) // 2 > start_frame:
                raise TimingReviewRequired("Narration segments overlap their immutable source timing.")
            output_frames.extend(b"\0\0" * (start_frame - len(output_frames) // 2))
            request_hash = hashlib.sha256(f"{job_id}:{segment.segment_id}:{segment.text}".encode()).hexdigest()
            request_id = str(uuid.UUID(request_hash[:32]))
            audio = asyncio.run(self._client.synthesize(request_id, self._voice, segment.text))
            frames, frame_count = _pcm(audio)
            duration_ms = round(frame_count / 16)
            timing = fit_timing(duration_ms, segment.end_ms - segment.start_ms)
            if timing.requires_review:
                raise TimingReviewRequired(f"Narration for {segment.segment_id} exceeds the safe timing envelope.")
            output_frames.extend(_speed_up(frames, timing.tempo, self._runner, self._tempo_timeout_seconds))
            end_frame = segment.end_ms * 16
            current_frames = len(output_frames) // 2
            if current_frames > end_frame:
                raise TimingReviewRequired(f"Narration for {segment.segment_id} still exceeds its source timing.")
            output_frames.extend(b"\0\0" * (end_frame - current_frames))

        return _wav(bytes(output_frames))
