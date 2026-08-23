"""Build deterministic mono narration while preserving approved script timing."""

from __future__ import annotations

import asyncio
import hashlib
import io
import sys
import uuid
import wave
from array import array
from typing import Protocol, Sequence

from localizer_agent.localization.validator import ScriptSegment
from localizer_agent.speech.timing import fit_timing


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


def _speed_up(frames: bytes, tempo: float) -> bytes:
    if tempo == 1.0:
        return frames
    samples = array("h")
    samples.frombytes(frames)
    if sys.byteorder != "little":
        samples.byteswap()
    target_count = max(1, round(len(samples) / tempo))
    adjusted = array("h", (samples[min(int(index * tempo), len(samples) - 1)] for index in range(target_count)))
    if sys.byteorder != "little":
        adjusted.byteswap()
    return adjusted.tobytes()


class AzureVoiceOperations:
    def __init__(self, client: AzureSynthesizer, voice: str) -> None:
        if not voice:
            raise ValueError("An allowlisted Azure voice is required.")
        self._client = client
        self._voice = voice

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
            output_frames.extend(_speed_up(frames, timing.tempo))
            end_frame = segment.end_ms * 16
            current_frames = len(output_frames) // 2
            if current_frames > end_frame:
                raise TimingReviewRequired(f"Narration for {segment.segment_id} still exceeds its source timing.")
            output_frames.extend(b"\0\0" * (end_frame - current_frames))

        output = io.BytesIO()
        with wave.open(output, "wb") as destination:
            destination.setnchannels(1)
            destination.setsampwidth(2)
            destination.setframerate(16_000)
            destination.writeframes(output_frames)
        return output.getvalue()
