from __future__ import annotations

from pathlib import Path

import pytest

from localizer_agent.media import CommandResult, MediaError, MediaProcessor


class RecordingRunner:
    def __init__(self, results: list[CommandResult]) -> None:
        self.calls: list[tuple[list[str], int]] = []
        self.results = results

    def run(self, arguments: list[str], timeout_seconds: int) -> CommandResult:
        self.calls.append((arguments, timeout_seconds))
        return self.results.pop(0)


def test_media_processor_probes_and_normalizes_with_argument_vectors(tmp_path: Path) -> None:
    runner = RecordingRunner(
        [
            CommandResult(0, '{"format":{"duration":"12.5","format_name":"mov,mp4"},"streams":[{"codec_type":"video"},{"codec_type":"audio"}]}', ""),
            CommandResult(0, "", ""),
        ]
    )
    processor = MediaProcessor(runner, timeout_seconds=60)
    source = tmp_path / "source.bin"
    normalized = tmp_path / "normalized.mp4"
    source.write_bytes(b"source")

    metadata = processor.probe(source)
    processor.normalize(source, normalized)

    assert metadata.duration_seconds == 12.5
    assert metadata.format_name == "mov,mp4"
    assert metadata.has_video is True
    assert metadata.has_audio is True
    assert runner.calls[0][0][0] == "ffprobe"
    assert runner.calls[1][0][0] == "ffmpeg"
    assert all(isinstance(value, str) for value in runner.calls[1][0])
    assert "shell" not in " ".join(runner.calls[1][0]).lower()


def test_media_processor_rejects_invalid_probe_metadata(tmp_path: Path) -> None:
    runner = RecordingRunner([CommandResult(0, '{"format":{"duration":"bad","format_name":""},"streams":[]}', "")])

    with pytest.raises(MediaError):
        MediaProcessor(runner, timeout_seconds=60).probe(tmp_path / "source.bin")


def test_subprocess_runner_lowers_child_priority_so_heartbeats_are_not_starved() -> None:
    import os
    import sys

    from localizer_agent.media import SubprocessRunner

    result = SubprocessRunner().run([sys.executable, "-c", "import os; print(os.nice(0))"], timeout_seconds=10)

    assert result.return_code == 0
    assert int(result.stdout.strip()) >= os.nice(0) + 10
