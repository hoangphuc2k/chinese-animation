"""Code-controlled FFmpeg render vectors for Vietnamese narration previews."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from localizer_agent.media import CommandRunner, MediaError, SubprocessRunner


@dataclass(frozen=True)
class RenderInputs:
    destination: Path
    narration: Path
    normalized_media: Path
    subtitles: Path
    watermark: Path


def _filter_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def build_render_command(inputs: RenderInputs) -> list[str]:
    inputs.destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    subtitle_path = _filter_path(inputs.subtitles)
    filters = (
        f"[0:v]subtitles='{subtitle_path}':charenc=UTF-8[captioned];"
        "[captioned][2:v]overlay=30:30:format=auto[video];"
        "[0:a]volume=0.18[ambience];"
        "[1:a]aresample=48000[voice];"
        "[ambience][voice]amix=inputs=2:duration=first:dropout_transition=0[audio]"
    )
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(inputs.normalized_media),
        "-i",
        str(inputs.narration),
        "-loop",
        "1",
        "-i",
        str(inputs.watermark),
        "-filter_complex",
        filters,
        "-map",
        "[video]",
        "-map",
        "[audio]",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        "-shortest",
        str(inputs.destination),
    ]


class RenderExecutor:
    def __init__(self, runner: CommandRunner | None = None, timeout_seconds: int = 3600) -> None:
        self._runner = runner or SubprocessRunner()
        self._timeout_seconds = timeout_seconds

    def render(self, inputs: RenderInputs) -> None:
        result = self._runner.run(build_render_command(inputs), self._timeout_seconds)
        if result.return_code != 0:
            raise MediaError("FFmpeg render failed.")
