"""Keep OCI preview paths behind short-lived job-bound opaque tokens."""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


@dataclass(frozen=True)
class PreviewRecord:
    expires_at: datetime
    job_id: str
    relative_reference: str


class PreviewRegistry:
    def __init__(self, storage_path: Path | None = None) -> None:
        self._storage_path = storage_path
        self._records = self._load()

    def register(self, job_id: str, relative_reference: str, expires_at: datetime) -> str:
        self._validate_reference(relative_reference)
        if expires_at.tzinfo is None:
            raise ValueError("Preview expiry must include a timezone.")
        token = secrets.token_urlsafe(32)
        self._records[token] = PreviewRecord(expires_at=expires_at, job_id=job_id, relative_reference=relative_reference)
        self._persist()
        return token

    def resolve(self, token: str, job_id: str, now: datetime) -> str | None:
        record = self._records.get(token)
        if record is None or record.job_id != job_id or now >= record.expires_at:
            return None
        return record.relative_reference

    def revoke(self, token: str) -> None:
        if self._records.pop(token, None) is not None:
            self._persist()

    def revoke_job(self, job_id: str) -> None:
        tokens = [token for token, record in self._records.items() if record.job_id == job_id]
        for token in tokens:
            del self._records[token]
        if tokens:
            self._persist()

    def _load(self) -> dict[str, PreviewRecord]:
        if self._storage_path is None or not self._storage_path.exists():
            return {}
        payload = json.loads(self._storage_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Preview registry must contain a JSON object.")
        records: dict[str, PreviewRecord] = {}
        for token, value in payload.items():
            if not isinstance(token, str) or not _TOKEN.fullmatch(token) or not isinstance(value, dict) or set(value) != {"expires_at", "job_id", "relative_reference"}:
                raise ValueError("Preview registry contains an invalid record.")
            expires_at = datetime.fromisoformat(value["expires_at"])
            job_id = value["job_id"]
            relative_reference = value["relative_reference"]
            if expires_at.tzinfo is None or not isinstance(job_id, str) or not isinstance(relative_reference, str):
                raise ValueError("Preview registry contains an invalid record.")
            self._validate_reference(relative_reference)
            records[token] = PreviewRecord(expires_at=expires_at, job_id=job_id, relative_reference=relative_reference)
        return records

    def _persist(self) -> None:
        if self._storage_path is None:
            return
        self._storage_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = {
            token: {
                "expires_at": record.expires_at.isoformat(),
                "job_id": record.job_id,
                "relative_reference": record.relative_reference,
            }
            for token, record in self._records.items()
        }
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{self._storage_path.name}.", dir=self._storage_path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
                json.dump(payload, temporary, separators=(",", ":"), sort_keys=True)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, self._storage_path)
            directory = os.open(self._storage_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    @staticmethod
    def _validate_reference(relative_reference: str) -> None:
        path = PurePosixPath(relative_reference)
        if not relative_reference or path.is_absolute() or ".." in path.parts:
            raise ValueError("Preview artifact reference must be a non-empty safe relative path.")
