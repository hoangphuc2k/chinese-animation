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

    command = build_render_command(inputs, source_duration_seconds=18.4)

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

    assert [call[0] for call in runner.calls] == ["ffprobe", "ffmpeg", "ffprobe"]
    assert str(inputs.normalized_media) in runner.calls[0]


def test_render_command_names_the_mp4_muxer_because_stage_outputs_end_in_tmp(tmp_path: Path) -> None:
    destination = tmp_path / ".render-x.mp4.tmp"
    inputs = RenderInputs(
        destination=destination,
        narration=tmp_path / "voice.wav",
        normalized_media=tmp_path / "normalized.mp4",
        subtitles=tmp_path / "captions.srt",
        watermark=tmp_path / "watermark.png",
    )

    assert build_render_command(inputs, source_duration_seconds=18.4)[-3:] == ["-f", "mp4", str(destination)]


def test_render_command_never_loops_the_watermark_so_ffmpeg_terminates(tmp_path: Path) -> None:
    # A looped image input never reaches EOF; with this filter graph `-shortest` does not stop it and
    # ffmpeg renders forever. A single PNG frame with overlay eof_action=repeat stays visible instead.
    inputs = RenderInputs(
        destination=tmp_path / "render.mp4",
        narration=tmp_path / "voice.wav",
        normalized_media=tmp_path / "normalized.mp4",
        subtitles=tmp_path / "captions.srt",
        watermark=tmp_path / "watermark.png",
    )

    command = build_render_command(inputs, source_duration_seconds=18.4)

    assert "-loop" not in command
    assert any("overlay=30:30:eof_action=repeat" in argument for argument in command)


def test_render_audio_is_bounded_to_the_source_duration_instead_of_relying_on_shortest(tmp_path: Path) -> None:
    # Source audio can be shorter than its video and narration longer; both must end with the video.
    inputs = RenderInputs(
        destination=tmp_path / "render.mp4",
        narration=tmp_path / "voice.wav",
        normalized_media=tmp_path / "normalized.mp4",
        subtitles=tmp_path / "captions.srt",
        watermark=tmp_path / "watermark.png",
    )

    command = build_render_command(inputs, source_duration_seconds=18.4)
    filters = command[command.index("-filter_complex") + 1]

    assert "apad=whole_dur=18.400" in filters
    assert "atrim=end=18.400" in filters
    assert "-shortest" not in command
