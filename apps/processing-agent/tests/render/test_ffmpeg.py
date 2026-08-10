from __future__ import annotations

from pathlib import Path

from localizer_agent.render.ffmpeg import RenderInputs, build_render_command


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
