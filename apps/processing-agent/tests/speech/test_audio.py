from __future__ import annotations

import asyncio
import io
import wave

import pytest

from localizer_agent.localization.validator import ScriptSegment
from localizer_agent.speech.audio import AzureVoiceOperations, TimingReviewRequired


def wav_bytes(duration_ms: int) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(16_000)
        destination.writeframes(b"\x01\x00" * (duration_ms * 16))
    return output.getvalue()


class FakeAzureClient:
    def __init__(self, audio: bytes) -> None:
        self.audio = audio
        self.calls: list[tuple[str, str, str]] = []

    async def synthesize(self, request_id: str, voice: str, text: str) -> bytes:
        self.calls.append((request_id, voice, text))
        return self.audio


def test_azure_voice_operations_fit_speech_into_immutable_segment_timing() -> None:
    client = FakeAzureClient(wav_bytes(800))
    operations = AzureVoiceOperations(client, "vi-VN-HoaiMyNeural")

    audio = operations.synthesize(
        "00000000-0000-4000-8000-000000000001",
        [ScriptSegment(end_ms=1000, segment_id="SEG-000001", start_ms=0, text="Xin chào")],
    )

    with wave.open(io.BytesIO(audio), "rb") as generated:
        assert generated.getnframes() == 16_000
    assert client.calls[0][1:] == ("vi-VN-HoaiMyNeural", "Xin chào")


def test_azure_voice_operations_escalate_an_unsafe_timing_overrun() -> None:
    operations = AzureVoiceOperations(FakeAzureClient(wav_bytes(1300)), "vi-VN-HoaiMyNeural")

    with pytest.raises(TimingReviewRequired):
        operations.synthesize(
            "00000000-0000-4000-8000-000000000001",
            [ScriptSegment(end_ms=1000, segment_id="SEG-000001", start_ms=0, text="Quá dài")],
        )
