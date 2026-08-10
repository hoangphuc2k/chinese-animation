"""Small injectable Gemini REST client that never logs prompts or API keys."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class GeminiResponse:
    status_code: int
    body: bytes


class GeminiTransport(Protocol):
    async def send(self, url: str, headers: dict[str, str], body: bytes, timeout_seconds: int) -> GeminiResponse: ...


class GeminiError(RuntimeError):
    def __init__(self, status_code: int, retryable: bool) -> None:
        category = "retryable" if retryable else "non-retryable"
        super().__init__(f"Gemini provider returned a {category} HTTP {status_code} error.")
        self.retryable = retryable
        self.status_code = status_code


class UrllibGeminiTransport:
    async def send(self, url: str, headers: dict[str, str], body: bytes, timeout_seconds: int) -> GeminiResponse:
        def request() -> GeminiResponse:
            try:
                with urlopen(Request(url, data=body, headers=headers, method="POST"), timeout=timeout_seconds) as response:  # noqa: S310 -- configured Gemini HTTPS origin
                    return GeminiResponse(response.status, response.read())
            except HTTPError as error:
                return GeminiResponse(error.code, error.read())

        return await asyncio.to_thread(request)


class GeminiClient:
    def __init__(
        self,
        base_url: str,
        model_id: str,
        api_key: str,
        *,
        transport: GeminiTransport | None = None,
        timeout_seconds: int = 60,
    ) -> None:
        if not base_url.startswith("https://") or not model_id or not api_key or timeout_seconds <= 0:
            raise ValueError("Gemini requires an HTTPS base URL, model ID, API key and positive timeout.")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model_id = model_id
        self._timeout_seconds = timeout_seconds
        self._transport = transport or UrllibGeminiTransport()

    async def generate_json(self, prompt: str) -> list[dict[str, Any]]:
        payload = json.dumps(
            {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json"},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        response = await self._transport.send(
            f"{self._base_url}/models/{self._model_id}:generateContent",
            {"content-type": "application/json", "x-goog-api-key": self._api_key},
            payload,
            self._timeout_seconds,
        )
        if response.status_code != 200:
            raise GeminiError(response.status_code, response.status_code == 429 or response.status_code >= 500)
        try:
            payload_data = json.loads(response.body)
            candidate_text = payload_data["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(candidate_text)
            if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
                raise TypeError("candidate JSON is not an array of objects")
            return parsed
        except (IndexError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise GeminiError(502, retryable=False) from error
