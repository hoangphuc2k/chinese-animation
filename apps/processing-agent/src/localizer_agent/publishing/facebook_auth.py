"""Facebook Page credentials loaded through a Vault abstraction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol


class FacebookAuthError(RuntimeError):
    """Raised when the Facebook Page token is absent or invalid."""


class VaultSecretReader(Protocol):
    def read_secret(self, reference: str) -> str: ...


@dataclass(frozen=True, repr=False)
class FacebookPageCredentials:
    page_access_token: str


def load_facebook_page_credentials(vault: VaultSecretReader, reference: str) -> FacebookPageCredentials:
    try:
        payload = json.loads(vault.read_secret(reference))
        if not isinstance(payload, dict) or set(payload) != {"pageAccessToken"}:
            raise ValueError("invalid fields")
        token = payload["pageAccessToken"]
        if not isinstance(token, str) or not token.strip():
            raise ValueError("empty credential")
        return FacebookPageCredentials(page_access_token=token)
    except Exception:
        raise FacebookAuthError("Facebook Vault credentials are unavailable or invalid.") from None
