"""Code-controlled FFmpeg render vectors for Vietnamese narration previews."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from localizer_agent.media import CommandRunner, MediaError, SubprocessRunner
from localizer_agent.render.validator import RenderProbe, validate_render


@dataclass(frozen=True)
class RenderInputs:
    destination: Path
    narration: Path
    normalized_media: Path
    subtitles: Path
    watermark: Path


def _filter_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def build_render_command(inputs: RenderInputs, *, source_duration_seconds: float) -> list[str]:
    if source_duration_seconds <= 0:
        raise MediaError("Render requires a positive source duration.")
    inputs.destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    subtitle_path = _filter_path(inputs.subtitles)
    duration = f"{source_duration_seconds:.3f}"
    filters = (
        f"[0:v]subtitles='{subtitle_path}':charenc=UTF-8[captioned];"
        # The watermark is one PNG frame; eof_action=repeat keeps it on screen for the whole video.
        # Never `-loop 1` it: a looped image never ends and ffmpeg then renders forever.
        "[captioned][2:v]overlay=30:30:eof_action=repeat:format=auto[video];"
        # Audio is bounded to the source video's duration: the ambience is padded with silence when the
        # source audio track is shorter, and the mix is trimmed so over-long narration cannot outlast
        # the video. `-shortest` is not used; it does not reliably stop this filter graph.
        f"[0:a]volume=0.18,apad=whole_dur={duration}[ambience];"
        "[1:a]aresample=48000[voice];"
        f"[ambience][voice]amix=inputs=2:duration=first:dropout_transition=0,atrim=end={duration}[audio]"
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
        # Stage outputs are written to `*.tmp` candidates, so the muxer cannot be inferred from the name.
        "-f",
        "mp4",
        str(inputs.destination),
    ]


class RenderExecutor:
    def __init__(self, runner: CommandRunner | None = None, timeout_seconds: int = 3600) -> None:
        self._runner = runner or SubprocessRunner()
        self._timeout_seconds = timeout_seconds

    def render(self, inputs: RenderInputs, source_duration_seconds: float) -> None:
        result = self._runner.run(
            build_render_command(inputs, source_duration_seconds=source_duration_seconds), self._timeout_seconds
        )
        if result.return_code != 0:
            raise MediaError("FFmpeg render failed.")


def _probe_duration_seconds(runner: CommandRunner, media: Path, timeout_seconds: int) -> float:
    result = runner.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(media)], timeout_seconds
    )
    if result.return_code != 0:
        raise MediaError("FFprobe could not read the source duration.")
    try:
        duration = float(json.loads(result.stdout)["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise MediaError("FFprobe returned an invalid source duration.") from error
    if duration <= 0:
        raise MediaError("Source media has no positive duration.")
    return duration


class FfmpegRenderOperations:
    def __init__(self, runner: CommandRunner | None = None, timeout_seconds: int = 3600) -> None:
        self._runner = runner or SubprocessRunner()
        self._timeout_seconds = timeout_seconds
        self._executor = RenderExecutor(self._runner, timeout_seconds)

    def _source_duration_seconds(self, media: Path) -> float:
        return _probe_duration_seconds(self._runner, media, self._timeout_seconds)

    def render(self, inputs: RenderInputs) -> None:
        self._executor.render(inputs, self._source_duration_seconds(inputs.normalized_media))
        result = self._runner.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,codec_name,duration",
                "-of",
                "json",
                str(inputs.destination),
            ],
            self._timeout_seconds,
        )
        if result.return_code != 0:
            raise MediaError("FFprobe rejected the rendered preview.")
        try:
            payload = json.loads(result.stdout)
            format_duration = float(payload["format"]["duration"])
            streams = payload["streams"]
            audio = next(stream for stream in streams if stream.get("codec_type") == "audio")
            video = next(stream for stream in streams if stream.get("codec_type") == "video")
            probe = RenderProbe(
                audio_codec=str(audio["codec_name"]),
                audio_duration_ms=round(float(audio.get("duration", format_duration)) * 1000),
                has_audio=True,
                has_video=True,
                video_codec=str(video["codec_name"]),
                video_duration_ms=round(float(video.get("duration", format_duration)) * 1000),
            )
        except (KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError) as error:
            raise MediaError("FFprobe returned invalid render metadata.") from error
        validate_render(probe)
