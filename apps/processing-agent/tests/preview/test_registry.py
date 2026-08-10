from __future__ import annotations

from datetime import UTC, datetime, timedelta

from localizer_agent.preview.registry import PreviewRegistry


def test_registry_uses_opaque_job_bound_expiring_tokens() -> None:
    now = datetime(2026, 8, 7, tzinfo=UTC)
    registry = PreviewRegistry()
    token = registry.register("00000000-0000-4000-8000-000000000001", "renders/final.mp4", now + timedelta(minutes=5))

    assert "final.mp4" not in token
    assert registry.resolve(token, "00000000-0000-4000-8000-000000000001", now) == "renders/final.mp4"
    assert registry.resolve(token, "00000000-0000-4000-8000-000000000002", now) is None
    assert registry.resolve(token, "00000000-0000-4000-8000-000000000001", now + timedelta(minutes=5, seconds=1)) is None


def test_registry_revokes_tokens_and_rejects_unsafe_relative_references() -> None:
    now = datetime(2026, 8, 7, tzinfo=UTC)
    registry = PreviewRegistry()
    token = registry.register("00000000-0000-4000-8000-000000000001", "renders/final.mp4", now + timedelta(minutes=5))

    registry.revoke(token)
    assert registry.resolve(token, "00000000-0000-4000-8000-000000000001", now) is None
