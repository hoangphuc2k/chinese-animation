from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from localizer_agent.media import CommandResult
from localizer_agent.transcription.whisper_cpp import WhisperCpp, WhisperError


def test_whisper_command_pins_the_model_forces_chinese_and_writes_json(tmp_path: Path) -> None:
    model = tmp_path / "ggml-small-q5_1.bin"
    model.write_bytes(b"model")
    whisper = WhisperCpp(tmp_path / "whisper-cli", model, expected_model_sha256="9372c470eeadd5f2d77b3c3c9be4bfb5d795ad6c7e67058b1d6b3b1c4fd69c00")

    command = whisper.command(tmp_path / "audio.wav", tmp_path / "output" )

    assert command == [
        str(tmp_path / "whisper-cli"),
        "-m",
        str(model),
        "-f",
        str(tmp_path / "audio.wav"),
        "-l",
        "zh",
        "-oj",
        "-of",
        str(tmp_path / "output"),
        "-np",
    ]


def test_whisper_rejects_a_model_checksum_mismatch(tmp_path: Path) -> None:
    model = tmp_path / "ggml-small-q5_1.bin"
    model.write_bytes(b"model")

    with pytest.raises(WhisperError):
        WhisperCpp(tmp_path / "whisper-cli", model, expected_model_sha256="a" * 64).validate_model()


class _RecordingRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def run(self, command: list[str], timeout_seconds: int) -> CommandResult:
        self.commands.append(command)
        return CommandResult(0, "", "")


def test_pcm_extraction_names_the_wav_muxer_because_stage_outputs_end_in_tmp(tmp_path: Path) -> None:
    runner = _RecordingRunner()
    whisper = WhisperCpp(tmp_path / "whisper-cli", tmp_path / "model.bin", expected_model_sha256="a" * 64, runner=runner)
    destination = tmp_path / ".extracted-audio-x.wav.tmp"

    whisper.extract_pcm(tmp_path / "normalized.mp4", destination)

    assert runner.commands[0][-3:] == ["-f", "wav", str(destination)]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="requires FFmpeg")
def test_pcm_extraction_really_writes_to_a_tmp_suffixed_path(tmp_path: Path) -> None:
    source = tmp_path / "tone.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1", "-c:a", "aac", str(source)],
        check=True,
    )
    whisper = WhisperCpp(tmp_path / "whisper-cli", tmp_path / "model.bin", expected_model_sha256="a" * 64)
    destination = tmp_path / ".extracted-audio-x.wav.tmp"

    whisper.extract_pcm(source, destination)

    assert destination.read_bytes().startswith(b"RIFF")


class _WhisperCppLikeRunner:
    """Writes output the way whisper.cpp does: `-of <prefix>` plus `-oj` produces `<prefix>.json`."""

    def run(self, command: list[str], timeout_seconds: int) -> CommandResult:
        prefix = command[command.index("-of") + 1]
        Path(f"{prefix}.json").write_text(
            json.dumps({"transcription": [{"offsets": {"from": 0, "to": 1500}, "text": " 大家好 "}]}),
            encoding="utf-8",
        )
        return CommandResult(0, "", "")


def test_transcribe_reads_the_json_whisper_cpp_appends_to_a_dotted_prefix(tmp_path: Path) -> None:
    model = tmp_path / "model.bin"
    model.write_bytes(b"model")
    whisper = WhisperCpp(
        tmp_path / "whisper-cli",
        model,
        expected_model_sha256=hashlib.sha256(b"model").hexdigest(),
        runner=_WhisperCppLikeRunner(),
    )

    artifact = whisper.transcribe(tmp_path / "audio.wav", tmp_path / ".transcript-key.whisper")

    assert [(segment.start_ms, segment.end_ms, segment.text) for segment in artifact.segments] == [(0, 1500, "大家好")]
