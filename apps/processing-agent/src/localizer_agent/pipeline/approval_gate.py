"""Exact script-approval check required immediately before TTS or rendering."""

from __future__ import annotations

from dataclasses import dataclass


class StaleApprovalError(RuntimeError):
    """Signals non-retryable work for a superseded or revoked script version."""


@dataclass(frozen=True)
class ApprovalSnapshot:
    approved: bool
    content_sha256: str
    job_id: str
    script_version: int


def require_exact_approval(
    job_id: str,
    script_version: int,
    script_sha256: str,
    approval: ApprovalSnapshot | None,
) -> ApprovalSnapshot:
    if approval is None or not approval.approved:
        raise StaleApprovalError("The script does not have an active approval.")
    if approval.job_id != job_id or approval.script_version != script_version or approval.content_sha256 != script_sha256:
        raise StaleApprovalError("The active approval does not match the claimed script version and hash.")
    return approval
