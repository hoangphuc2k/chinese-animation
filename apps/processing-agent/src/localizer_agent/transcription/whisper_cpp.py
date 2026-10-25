"""Pinned whisper.cpp invocation without shell execution."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from localizer_agent.media import CommandResult, CommandRunner, SubprocessRunner
from localizer_agent.transcription.segments import SegmentError, TranscriptSegment, normalize_segments


class WhisperError(RuntimeError):
    """Raised when whisper.cpp or its pinned model cannot produce a transcript."""


@dataclass(frozen=True)
class TranscriptArtifact:
    binary_version: str
    model_sha256: str
    segments: list[TranscriptSegment]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def json_output_path(output_prefix: Path) -> Path:
    """whisper.cpp appends `.json` to the `-of` prefix; it does not replace an existing suffix."""
    return output_prefix.with_name(f"{output_prefix.name}.json")


class WhisperCpp:
    def __init__(
        self,
        binary_path: Path,
        model_path: Path,
        *,
        expected_model_sha256: str,
        runner: CommandRunner | None = None,
        timeout_seconds: int = 3600,
        binary_version: str = "whisper.cpp",
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Whisper timeout must be positive.")
        if len(expected_model_sha256) != 64 or any(character not in "0123456789abcdef" for character in expected_model_sha256):
            raise ValueError("Expected whisper model checksum must be a lowercase SHA-256 digest.")
        self._binary_path = binary_path
        self._binary_version = binary_version
        self._expected_model_sha256 = expected_model_sha256
        self._model_path = model_path
        self._runner = runner or SubprocessRunner()
        self._timeout_seconds = timeout_seconds

    def command(self, audio_path: Path, output_prefix: Path) -> list[str]:
        return [
            str(self._binary_path),
            "-m",
            str(self._model_path),
            "-f",
            str(audio_path),
            "-l",
            "zh",
            "-oj",
            "-of",
            str(output_prefix),
            "-np",
        ]

    def extract_pcm(self, source: Path, destination: Path) -> None:
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        result = self._runner.run(
            ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", str(source), "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-f", "wav", str(destination)],
            self._timeout_seconds,
        )
        if result.return_code != 0:
            raise WhisperError("FFmpeg audio extraction failed.")

    def transcribe(self, audio_path: Path, output_prefix: Path) -> TranscriptArtifact:
        self.validate_model()
        result = self._runner.run(self.command(audio_path, output_prefix), self._timeout_seconds)
        if result.return_code != 0:
            raise WhisperError("whisper.cpp transcription failed.")
        output_path = json_output_path(output_prefix)
        try:
            payload: Any = json.loads(output_path.read_bytes())
            raw_segments = payload["transcription"]
            if not isinstance(raw_segments, list):
                raise TypeError("transcription is not a list")
            segments = normalize_segments(raw_segments)
        except (OSError, TypeError, KeyError, json.JSONDecodeError, SegmentError) as error:
            raise WhisperError("whisper.cpp returned malformed transcript JSON.") from error
        return TranscriptArtifact(binary_version=self._binary_version, model_sha256=self._expected_model_sha256, segments=segments)

    def validate_model(self) -> None:
        try:
            actual = _sha256(self._model_path)
        except OSError as error:
            raise WhisperError("Pinned whisper model is unavailable.") from error
        if actual != self._expected_model_sha256:
            raise WhisperError("Pinned whisper model checksum does not match configuration.")
