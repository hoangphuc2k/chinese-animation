from __future__ import annotations

from pathlib import Path

import pytest

from localizer_agent.pipeline.lineage import ArtifactRecord, StageKey
from localizer_agent.pipeline.runner import CorruptArtifactError, StageRunner
from localizer_agent.workspace import JobWorkspaceManager


def workspace(tmp_path: Path):
    return JobWorkspaceManager(tmp_path / "workspaces", minimum_free_bytes=1).create(
        "00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000002",
    )


def test_stage_key_changes_with_input_hash_and_is_stable() -> None:
    first = StageKey.for_input("00000000-0000-4000-8000-000000000001", 3, "TRANSCRIBING", "a" * 64, "whisper-v1")

    assert first == StageKey.for_input("00000000-0000-4000-8000-000000000001", 3, "TRANSCRIBING", "a" * 64, "whisper-v1")
    assert first != StageKey.for_input("00000000-0000-4000-8000-000000000001", 3, "TRANSCRIBING", "b" * 64, "whisper-v1")


def test_runner_atomically_publishes_and_replays_only_a_validated_output(tmp_path: Path) -> None:
    runner = StageRunner(workspace(tmp_path))
    key = StageKey.for_input("00000000-0000-4000-8000-000000000001", 3, "TRANSCRIBING", "a" * 64, "whisper-v1")
    calls = 0

    def execute(candidate: Path) -> None:
        nonlocal calls
        calls += 1
        candidate.write_text('{"segments":[]}')

    first = runner.run(key, kind="TRANSCRIPT", tool_version="whisper.cpp-1.7.4", suffix=".json", execute=execute)
    second = runner.run(key, kind="TRANSCRIPT", tool_version="whisper.cpp-1.7.4", suffix=".json", execute=execute)

    assert first.replayed is False
    assert second.replayed is True
    assert calls == 1
    assert first.artifact == ArtifactRecord.from_path(first.artifact.path, kind="TRANSCRIPT", tool_version="whisper.cpp-1.7.4")
    assert not list(first.artifact.path.parent.glob("*.tmp"))


def test_runner_rejects_a_corrupt_replay_without_overwriting_it(tmp_path: Path) -> None:
    runner = StageRunner(workspace(tmp_path))
    key = StageKey.for_input("00000000-0000-4000-8000-000000000001", 3, "TRANSCRIBING", "a" * 64, "whisper-v1")
    result = runner.run(key, kind="TRANSCRIPT", tool_version="whisper.cpp-1.7.4", suffix=".json", execute=lambda path: path.write_text("ok"))
    result.artifact.path.write_text("tampered")

    with pytest.raises(CorruptArtifactError):
        runner.run(key, kind="TRANSCRIPT", tool_version="whisper.cpp-1.7.4", suffix=".json", execute=lambda path: path.write_text("replacement"))
