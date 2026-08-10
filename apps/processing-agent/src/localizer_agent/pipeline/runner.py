"""Atomic, deterministic stage output publication and replay validation."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from localizer_agent.pipeline.lineage import ArtifactRecord, LineageError, StageKey
from localizer_agent.workspace import JobWorkspace


class CorruptArtifactError(RuntimeError):
    """Raised when a prior stage output no longer matches its committed lineage."""


@dataclass(frozen=True)
class StageResult:
    artifact: ArtifactRecord
    replayed: bool


class StageRunner:
    def __init__(self, workspace: JobWorkspace) -> None:
        self._workspace = workspace

    def run(
        self,
        key: StageKey,
        *,
        kind: str,
        tool_version: str,
        suffix: str,
        execute: Callable[[Path], None],
    ) -> StageResult:
        if not suffix.startswith(".") or "/" in suffix or "\\" in suffix:
            raise ValueError("Artifact suffix must be a filename extension.")
        artifact_directory = self._workspace.root / "artifacts"
        artifact_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        output_path = artifact_directory / f"{key.value}{suffix}"
        record_path = artifact_directory / f"{key.value}.artifact.json"
        if output_path.exists() or record_path.exists():
            return StageResult(self._load_validated(record_path, output_path), replayed=True)

        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{key.value}-", suffix=f"{suffix}.tmp", dir=artifact_directory)
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        record_temporary: Path | None = None
        try:
            execute(temporary_path)
            artifact = ArtifactRecord.from_path(temporary_path, kind=kind, tool_version=tool_version)
            committed_artifact = ArtifactRecord(
                byte_count=artifact.byte_count,
                kind=artifact.kind,
                path=output_path,
                sha256=artifact.sha256,
                tool_version=artifact.tool_version,
            )
            record_temporary = self._write_record_candidate(artifact_directory, committed_artifact)
            os.replace(temporary_path, output_path)
            os.replace(record_temporary, record_path)
            self._fsync_directory(artifact_directory)
            return StageResult(ArtifactRecord.from_path(output_path, kind=kind, tool_version=tool_version), replayed=False)
        except LineageError as error:
            raise CorruptArtifactError(str(error)) from error
        finally:
            if temporary_path.exists():
                temporary_path.unlink()
            if record_temporary is not None and record_temporary.exists():
                record_temporary.unlink()

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _write_record_candidate(directory: Path, artifact: ArtifactRecord) -> Path:
        descriptor, temporary_name = tempfile.mkstemp(prefix=".artifact-", suffix=".json.tmp", dir=directory)
        try:
            with os.fdopen(descriptor, "wb") as temporary:
                temporary.write(json.dumps(artifact.manifest_value(), separators=(",", ":"), sort_keys=True).encode())
                temporary.flush()
                os.fsync(temporary.fileno())
        except BaseException:
            os.close(descriptor)
            raise
        return Path(temporary_name)

    @staticmethod
    def _load_validated(record_path: Path, output_path: Path) -> ArtifactRecord:
        if not record_path.is_file() or not output_path.is_file():
            raise CorruptArtifactError("A stage output is missing its matching immutable artifact record.")
        try:
            persisted = json.loads(record_path.read_bytes())
            if not isinstance(persisted, dict) or persisted.get("fileName") != output_path.name:
                raise ValueError("invalid persisted artifact record")
            actual = ArtifactRecord.from_path(output_path, kind=str(persisted["kind"]), tool_version=str(persisted["toolVersion"]))
            if actual.manifest_value() != persisted:
                raise ValueError("artifact hash or byte count differs from its record")
            return actual
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, LineageError) as error:
            raise CorruptArtifactError("A completed stage artifact failed integrity validation.") from error
