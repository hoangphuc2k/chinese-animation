from __future__ import annotations

import io
import wave
from pathlib import Path

import pytest

from localizer_agent.localization.validator import ScriptSegment
from localizer_agent.media import CommandResult, MediaError
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


class FakeTempoRunner:
    """Emulates FFmpeg atempo: writes a mono 16 kHz WAV of round(input / tempo) + extra frames."""

    def __init__(self, extra_frames: int = 0, return_code: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.extra_frames = extra_frames
        self.return_code = return_code

    def run(self, arguments: list[str], timeout_seconds: int) -> CommandResult:
        self.calls.append(arguments)
        if self.return_code != 0:
            return CommandResult(self.return_code, "", "atempo failed")
        source = Path(arguments[arguments.index("-i") + 1])
        tempo = float(arguments[arguments.index("-filter:a") + 1].removeprefix("atempo="))
        with wave.open(str(source), "rb") as reader:
            frame_count = reader.getnframes()
        with wave.open(arguments[-1], "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(16_000)
            writer.writeframes(b"\x02\x00" * (round(frame_count / tempo) + self.extra_frames))
        return CommandResult(0, "", "")


def overrun_segment() -> list[ScriptSegment]:
    return [ScriptSegment(end_ms=1000, segment_id="SEG-000001", start_ms=0, text="Hơi dài")]


def test_tempo_adjustment_uses_pitch_preserving_ffmpeg_atempo() -> None:
    runner = FakeTempoRunner()
    operations = AzureVoiceOperations(FakeAzureClient(wav_bytes(1100)), "vi-VN-HoaiMyNeural", runner=runner)

    audio = operations.synthesize("00000000-0000-4000-8000-000000000001", overrun_segment())

    assert len(runner.calls) == 1
    command = runner.calls[0]
    assert command[0] == "ffmpeg"
    assert command[command.index("-filter:a") + 1] == "atempo=1.1"
    assert "-nostdin" in command and command[command.index("-c:a") + 1] == "pcm_s16le"
    with wave.open(io.BytesIO(audio), "rb") as generated:
        assert (generated.getnchannels(), generated.getsampwidth(), generated.getframerate()) == (1, 2, 16_000)
        assert generated.getnframes() == 16_000
        frames = generated.readframes(generated.getnframes())
    assert frames == b"\x02\x00" * 16_000


def test_tempo_output_is_trimmed_to_the_exact_expected_length() -> None:
    runner = FakeTempoRunner(extra_frames=37)
    operations = AzureVoiceOperations(FakeAzureClient(wav_bytes(1050)), "vi-VN-HoaiMyNeural", runner=runner)

    audio = operations.synthesize("00000000-0000-4000-8000-000000000001", overrun_segment())

    with wave.open(io.BytesIO(audio), "rb") as generated:
        frames = generated.readframes(generated.getnframes())
    expected = round(1050 * 16 / 1.05)
    assert frames[: expected * 2] == b"\x02\x00" * expected
    assert frames[expected * 2 :] == b"\0\0" * (16_000 - expected)


def test_unit_tempo_is_a_no_op_without_invoking_ffmpeg() -> None:
    runner = FakeTempoRunner()
    operations = AzureVoiceOperations(FakeAzureClient(wav_bytes(800)), "vi-VN-HoaiMyNeural", runner=runner)

    audio = operations.synthesize("00000000-0000-4000-8000-000000000001", overrun_segment())

    assert runner.calls == []
    with wave.open(io.BytesIO(audio), "rb") as generated:
        assert generated.readframes(generated.getnframes()) == b"\x01\x00" * 12_800 + b"\0\0" * 3_200


def test_failed_tempo_adjustment_raises_a_sanitized_media_error() -> None:
    operations = AzureVoiceOperations(
        FakeAzureClient(wav_bytes(1100)),
        "vi-VN-HoaiMyNeural",
        runner=FakeTempoRunner(return_code=1),
    )

    with pytest.raises(MediaError, match="tempo"):
        operations.synthesize("00000000-0000-4000-8000-000000000001", overrun_segment())
