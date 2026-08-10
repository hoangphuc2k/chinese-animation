"""Hash-addressed artifact metadata that never stores source locators or real paths."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


class LineageError(ValueError):
    """Raised when an artifact identity or persisted record is invalid."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class StageKey:
    value: str

    @classmethod
    def for_input(cls, job_id: str, job_version: int, stage: str, input_sha256: str, config_version: str) -> "StageKey":
        if job_version <= 0 or not stage or not config_version:
            raise LineageError("Stage identity requires a positive job version, stage and configuration version.")
        if len(input_sha256) != 64 or any(character not in "0123456789abcdef" for character in input_sha256):
            raise LineageError("Stage input hash must be a lowercase SHA-256 digest.")
        encoded = json.dumps(
            {
                "configVersion": config_version,
                "inputSha256": input_sha256,
                "jobId": job_id,
                "jobVersion": job_version,
                "stage": stage,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return cls(hashlib.sha256(encoded).hexdigest())


@dataclass(frozen=True)
class ArtifactRecord:
    byte_count: int
    kind: str
    path: Path
    sha256: str
    tool_version: str

    @classmethod
    def from_path(cls, path: Path, *, kind: str, tool_version: str) -> "ArtifactRecord":
        if not path.is_file():
            raise LineageError("Stage did not produce a regular artifact file.")
        if not kind or not tool_version:
            raise LineageError("Artifact kind and tool version are required.")
        return cls(byte_count=path.stat().st_size, kind=kind, path=path, sha256=_sha256_file(path), tool_version=tool_version)

    def manifest_value(self) -> dict[str, int | str]:
        return {
            "byteCount": self.byte_count,
            "fileName": self.path.name,
            "kind": self.kind,
            "sha256": self.sha256,
            "toolVersion": self.tool_version,
        }
