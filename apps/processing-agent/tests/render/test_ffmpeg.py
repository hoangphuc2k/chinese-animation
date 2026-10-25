from __future__ import annotations

from pathlib import Path

from localizer_agent.media import CommandResult
from localizer_agent.render.ffmpeg import FfmpegRenderOperations, RenderInputs, build_render_command


def test_render_command_uses_argument_vectors_and_burns_subtitles_and_watermark(tmp_path: Path) -> None:
    inputs = RenderInputs(
        destination=tmp_path / "render.mp4",
        narration=tmp_path / "voice.wav",
        normalized_media=tmp_path / "normalized.mp4",
        subtitles=tmp_path / "captions.srt",
        watermark=tmp_path / "watermark.png",
    )

    command = build_render_command(inputs)

    assert command[0] == "ffmpeg"
    assert "-filter_complex" in command
    assert any("subtitles=" in argument for argument in command)
    assert any("overlay=30:30" in argument for argument in command)
    assert all("shell" not in argument.lower() for argument in command)


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, arguments: list[str], timeout_seconds: int) -> CommandResult:
        self.calls.append(arguments)
        if arguments[0] == "ffmpeg":
            Path(arguments[-1]).write_bytes(b"render")
            return CommandResult(0, "", "")
        return CommandResult(
            0,
            '{"format":{"duration":"10.0"},"streams":[{"codec_name":"h264","codec_type":"video","duration":"10.0"},{"codec_name":"aac","codec_type":"audio","duration":"10.05"}]}',
            "",
        )


def test_render_operations_probe_and_validate_the_produced_streams(tmp_path: Path) -> None:
    runner = FakeRunner()
    inputs = RenderInputs(tmp_path / "render.mp4", tmp_path / "voice.wav", tmp_path / "normalized.mp4", tmp_path / "captions.srt", tmp_path / "watermark.png")

    FfmpegRenderOperations(runner=runner).render(inputs)

    assert [call[0] for call in runner.calls] == ["ffmpeg", "ffprobe"]


def test_render_command_names_the_mp4_muxer_because_stage_outputs_end_in_tmp(tmp_path: Path) -> None:
    destination = tmp_path / ".render-x.mp4.tmp"
    inputs = RenderInputs(
        destination=destination,
        narration=tmp_path / "voice.wav",
        normalized_media=tmp_path / "normalized.mp4",
        subtitles=tmp_path / "captions.srt",
        watermark=tmp_path / "watermark.png",
    )

    assert build_render_command(inputs)[-3:] == ["-f", "mp4", str(destination)]
