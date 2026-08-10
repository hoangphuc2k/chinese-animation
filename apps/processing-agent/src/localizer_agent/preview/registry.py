"""Keep OCI preview paths behind short-lived job-bound opaque tokens."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath


@dataclass(frozen=True)
class PreviewRecord:
    expires_at: datetime
    job_id: str
    relative_reference: str


class PreviewRegistry:
    def __init__(self) -> None:
        self._records: dict[str, PreviewRecord] = {}

    def register(self, job_id: str, relative_reference: str, expires_at: datetime) -> str:
        self._validate_reference(relative_reference)
        if expires_at.tzinfo is None:
            raise ValueError("Preview expiry must include a timezone.")
        token = secrets.token_urlsafe(32)
        self._records[token] = PreviewRecord(expires_at=expires_at, job_id=job_id, relative_reference=relative_reference)
        return token

    def resolve(self, token: str, job_id: str, now: datetime) -> str | None:
        record = self._records.get(token)
        if record is None or record.job_id != job_id or now >= record.expires_at:
            return None
        return record.relative_reference

    def revoke(self, token: str) -> None:
        self._records.pop(token, None)

    @staticmethod
    def _validate_reference(relative_reference: str) -> None:
        path = PurePosixPath(relative_reference)
        if not relative_reference or path.is_absolute() or ".." in path.parts:
            raise ValueError("Preview artifact reference must be a non-empty safe relative path.")
