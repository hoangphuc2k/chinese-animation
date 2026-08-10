"""Injectable Azure Neural TTS client with strict voice and SSML handling."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from html import escape
from typing import Mapping, Protocol
from urllib.error import HTTPError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class AzureSpeechResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


class AzureSpeechTransport(Protocol):
    async def send(self, url: str, headers: dict[str, str], body: bytes, timeout_seconds: int) -> AzureSpeechResponse: ...


class AzureSpeechError(RuntimeError):
    def __init__(self, status_code: int, retryable: bool, retry_after_seconds: int | None = None) -> None:
        category = "retryable" if retryable else "non-retryable"
        super().__init__(f"Azure Speech returned a {category} HTTP {status_code} error.")
        self.retry_after_seconds = retry_after_seconds
        self.retryable = retryable
        self.status_code = status_code


class UrllibAzureSpeechTransport:
    async def send(self, url: str, headers: dict[str, str], body: bytes, timeout_seconds: int) -> AzureSpeechResponse:
        def request() -> AzureSpeechResponse:
            try:
                with urlopen(Request(url, data=body, headers=headers, method="POST"), timeout=timeout_seconds) as response:  # noqa: S310 -- configured Azure HTTPS endpoint
                    return AzureSpeechResponse(response.status, dict(response.headers.items()), response.read())
            except HTTPError as error:
                return AzureSpeechResponse(error.code, dict(error.headers.items()) if error.headers else {}, error.read())

        return await asyncio.to_thread(request)


class AzureSpeechClient:
    def __init__(
        self,
        endpoint: str,
        subscription_key: str,
        allowed_voices: set[str],
        *,
        transport: AzureSpeechTransport | None = None,
        timeout_seconds: int = 60,
    ) -> None:
        if not endpoint.startswith("https://") or not subscription_key or not allowed_voices or timeout_seconds <= 0:
            raise ValueError("Azure Speech requires an HTTPS endpoint, subscription key, voices and positive timeout.")
        self._allowed_voices = frozenset(allowed_voices)
        self._endpoint = endpoint
        self._subscription_key = subscription_key
        self._timeout_seconds = timeout_seconds
        self._transport = transport or UrllibAzureSpeechTransport()

    async def synthesize(self, request_id: str, voice: str, text: str) -> bytes:
        if voice not in self._allowed_voices or not request_id or not text.strip():
            raise AzureSpeechError(400, retryable=False)
        ssml = (
            '<speak version="1.0" xml:lang="vi-VN">'
            f'<voice name="{escape(voice, quote=True)}">{escape(text.strip(), quote=False)}</voice>'
            "</speak>"
        ).encode()
        response = await self._transport.send(
            self._endpoint,
            {
                "Content-Type": "application/ssml+xml",
                "User-Agent": "localizer-agent",
                "X-Microsoft-OutputFormat": "riff-16khz-16bit-mono-pcm",
                "X-Microsoft-RequestId": request_id,
                "Ocp-Apim-Subscription-Key": self._subscription_key,
            },
            ssml,
            self._timeout_seconds,
        )
        if response.status_code != 200:
            retry_after = self._retry_after(response.headers)
            raise AzureSpeechError(response.status_code, response.status_code == 429 or response.status_code >= 500, retry_after)
        if not response.body.startswith(b"RIFF"):
            raise AzureSpeechError(502, retryable=False)
        return response.body

    @staticmethod
    def _retry_after(headers: Mapping[str, str]) -> int | None:
        value = next((header_value for name, header_value in headers.items() if name.lower() == "retry-after"), None)
        if value is None:
            return None
        try:
            seconds = int(value)
        except ValueError:
            return None
        return seconds if seconds >= 0 else None
