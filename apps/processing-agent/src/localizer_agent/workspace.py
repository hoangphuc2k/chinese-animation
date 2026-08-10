"""Crash-safe, per-job workspace state without sensitive source locators."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class DiskCapacityError(RuntimeError):
    pass


class JobManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    source_asset_id: str
    stage: str
    source_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    source_byte_count: int | None = Field(default=None, ge=0)
    normalized_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    normalized_byte_count: int | None = Field(default=None, ge=0)


@dataclass(frozen=True)
class JobWorkspace:
    root: Path
    manifest_path: Path
    source_path: Path
    normalized_path: Path


def _uuid(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError as error:
        raise ValueError("Job and source-asset identifiers must be UUIDs.") from error


class JobWorkspaceManager:
    def __init__(self, root: Path, minimum_free_bytes: int) -> None:
        self._root = root.expanduser()
        self._minimum_free_bytes = minimum_free_bytes

    def create(self, job_id: str, source_asset_id: str) -> JobWorkspace:
        safe_job_id = _uuid(job_id)
        safe_source_asset_id = _uuid(source_asset_id)
        self._root.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self._root).free < self._minimum_free_bytes:
            raise DiskCapacityError("Workspace disk free space is below the configured safety floor.")
        root = (self._root / safe_job_id).resolve()
        if root.parent != self._root.resolve():
            raise ValueError("Workspace path escaped the configured root.")
        root.mkdir(mode=0o700, exist_ok=True)
        workspace = JobWorkspace(
            root=root,
            manifest_path=root / "manifest.json",
            normalized_path=root / "normalized.mp4",
            source_path=root / "source.bin",
        )
        if not workspace.manifest_path.exists():
            self._write_manifest(workspace.manifest_path, JobManifest(job_id=safe_job_id, source_asset_id=safe_source_asset_id, stage="CLAIMED"))
        return workspace

    def open(self, job_id: str) -> JobWorkspace:
        safe_job_id = _uuid(job_id)
        root = (self._root / safe_job_id).resolve()
        if root.parent != self._root.resolve() or not root.is_dir():
            raise FileNotFoundError("Job workspace does not exist.")
        workspace = JobWorkspace(
            root=root,
            manifest_path=root / "manifest.json",
            normalized_path=root / "normalized.mp4",
            source_path=root / "source.bin",
        )
        manifest = self.load(workspace)
        if manifest.job_id != safe_job_id:
            raise ValueError("Workspace manifest does not match the requested job.")
        return workspace

    def update(self, workspace: JobWorkspace, *, stage: str, **values: object) -> JobManifest:
        current = self.load(workspace)
        manifest = current.model_copy(update={"stage": stage, **values})
        self._write_manifest(workspace.manifest_path, manifest)
        return manifest

    @staticmethod
    def load(workspace: JobWorkspace) -> JobManifest:
        return JobManifest.model_validate_json(workspace.manifest_path.read_bytes())

    def recover(self) -> list[JobManifest]:
        if not self._root.exists():
            return []
        manifests: list[JobManifest] = []
        for candidate in sorted(self._root.glob("*/manifest.json")):
            manifests.append(JobManifest.model_validate_json(candidate.read_bytes()))
        return manifests

    @staticmethod
    def _write_manifest(path: Path, manifest: JobManifest) -> None:
        encoded = json.dumps(manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
        descriptor, temporary_name = tempfile.mkstemp(prefix=".manifest-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as temporary:
                temporary.write(encoded)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
