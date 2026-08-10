from __future__ import annotations

import pytest

from localizer_agent.pipeline.approval_gate import ApprovalSnapshot, StaleApprovalError, require_exact_approval


def test_gate_accepts_only_the_exact_active_approved_script() -> None:
    approval = ApprovalSnapshot(
        approved=True,
        content_sha256="a" * 64,
        job_id="00000000-0000-4000-8000-000000000001",
        script_version=2,
    )

    assert require_exact_approval("00000000-0000-4000-8000-000000000001", 2, "a" * 64, approval) == approval


@pytest.mark.parametrize(
    "approval",
    [
        None,
        ApprovalSnapshot(approved=False, content_sha256="a" * 64, job_id="00000000-0000-4000-8000-000000000001", script_version=2),
        ApprovalSnapshot(approved=True, content_sha256="a" * 64, job_id="00000000-0000-4000-8000-000000000001", script_version=3),
        ApprovalSnapshot(approved=True, content_sha256="b" * 64, job_id="00000000-0000-4000-8000-000000000001", script_version=2),
    ],
)
def test_gate_rejects_missing_or_stale_approval(approval: ApprovalSnapshot | None) -> None:
    with pytest.raises(StaleApprovalError):
        require_exact_approval("00000000-0000-4000-8000-000000000001", 2, "a" * 64, approval)
