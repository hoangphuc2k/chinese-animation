"""YouTube OAuth refresh credentials loaded through a Vault abstraction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol


class YouTubeAuthError(RuntimeError):
    """Raised when YouTube credentials are absent or invalid."""


class VaultSecretReader(Protocol):
    def read_secret(self, reference: str) -> str: ...


@dataclass(frozen=True, repr=False)
class YouTubeCredentials:
    client_id: str
    client_secret: str
    refresh_token: str


def load_youtube_credentials(vault: VaultSecretReader, reference: str) -> YouTubeCredentials:
    try:
        payload = json.loads(vault.read_secret(reference))
        if not isinstance(payload, dict) or set(payload) != {"clientId", "clientSecret", "refreshToken"}:
            raise ValueError("invalid fields")
        values = [payload["clientId"], payload["clientSecret"], payload["refreshToken"]]
        if not all(isinstance(value, str) and value.strip() for value in values):
            raise ValueError("empty credential")
        return YouTubeCredentials(client_id=values[0], client_secret=values[1], refresh_token=values[2])
    except Exception:
        raise YouTubeAuthError("YouTube Vault credentials are unavailable or invalid.") from None
