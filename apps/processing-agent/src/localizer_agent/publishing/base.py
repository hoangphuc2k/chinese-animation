"""Shared approval boundary for destination adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Protocol, final

from localizer_agent.publishing.models import Destination, PublicationApprovalSnapshot, PublicationCommand


class StalePublicationApprovalError(RuntimeError):
    """Raised before media access when a publication approval is absent or stale."""


class PublicationApprovalClient(Protocol):
    async def get_publication_approval(self, command: PublicationCommand) -> PublicationApprovalSnapshot | None: ...


class ApprovalBoundPublicationAdapter(ABC):
    """Template that makes approval validation the final action before adapter media access."""

    destination: Destination

    def __init__(self, approval_client: PublicationApprovalClient) -> None:
        self._approval_client = approval_client

    @final
    async def publish(self, command: PublicationCommand, media_path: Path) -> object:
        if command.destination is not self.destination:
            raise ValueError("Publication command destination does not match the adapter.")
        approval = await self._approval_client.get_publication_approval(command)
        require_exact_publication_approval(command, approval)
        return await self.publish_approved(command, media_path)

    @abstractmethod
    async def publish_approved(self, command: PublicationCommand, media_path: Path) -> object:
        """Read media and contact the provider only after the base approval gate passes."""


class PublicationAdapter(Protocol):
    destination: Destination

    async def publish(self, command: PublicationCommand, media_path: Path) -> object: ...


def require_exact_publication_approval(
    command: PublicationCommand,
    approval: PublicationApprovalSnapshot | None,
) -> PublicationApprovalSnapshot:
    if approval is None or not approval.approved:
        raise StalePublicationApprovalError("The publication does not have an active approval.")
    expected = PublicationApprovalSnapshot.from_command(command, approved=True)
    if approval != expected:
        raise StalePublicationApprovalError("The active approval does not match the publication command bindings.")
    return approval
