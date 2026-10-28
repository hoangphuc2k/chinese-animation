"""FFprobe validation and deterministic FFmpeg normalization without shell execution."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class MediaError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommandResult:
    return_code: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    def run(self, arguments: list[str], timeout_seconds: int) -> CommandResult: ...


class SubprocessRunner:
    """Runs media tools below the agent's priority, so a CPU-saturating ffmpeg or whisper.cpp
    cannot starve the lease heartbeat and lose the job's lease mid-stage."""

    def __init__(self, niceness: int = 10) -> None:
        self._niceness = niceness

    def run(self, arguments: list[str], timeout_seconds: int) -> CommandResult:
        try:
            result = subprocess.run(  # noqa: S603
                ["nice", "-n", str(self._niceness), *arguments],
                capture_output=True,
                check=False,
                shell=False,
                text=True,
                timeout=timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise MediaError("Media command could not be completed.") from error
        return CommandResult(result.returncode, result.stdout, result.stderr)


@dataclass(frozen=True)
class TechnicalMetadata:
    duration_seconds: float
    format_name: str
    has_audio: bool
    has_video: bool

    def control_plane_value(self) -> dict[str, float | str | bool]:
        return {
            "durationSeconds": self.duration_seconds,
            "formatName": self.format_name,
            "hasAudio": self.has_audio,
            "hasVideo": self.has_video,
        }


class MediaProcessor:
    def __init__(self, runner: CommandRunner | None = None, timeout_seconds: int = 900) -> None:
        self._runner = runner or SubprocessRunner()
        self._timeout_seconds = timeout_seconds

    def probe(self, source: Path) -> TechnicalMetadata:
        result = self._runner.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration,format_name:stream=codec_type", "-of", "json", str(source)],
            self._timeout_seconds,
        )
        if result.return_code != 0:
            raise MediaError("FFprobe rejected the source media.")
        try:
            payload = json.loads(result.stdout)
            format_data = payload["format"]
            duration = float(format_data["duration"])
            format_name = str(format_data["format_name"])
            stream_types = {str(stream["codec_type"]) for stream in payload.get("streams", [])}
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise MediaError("FFprobe returned invalid media metadata.") from error
        if duration < 0 or not format_name or "video" not in stream_types:
            raise MediaError("Source media does not contain valid video metadata.")
        return TechnicalMetadata(duration, format_name, "audio" in stream_types, True)

    def normalize(self, source: Path, destination: Path) -> None:
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        result = self._runner.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-y",
                "-i",
                str(source),
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(destination),
            ],
            self._timeout_seconds,
        )
        if result.return_code != 0:
            raise MediaError("FFmpeg normalization failed.")
