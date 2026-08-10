from __future__ import annotations

from pathlib import Path

import pytest

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
