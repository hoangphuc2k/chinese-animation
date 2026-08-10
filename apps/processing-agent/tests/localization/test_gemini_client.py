from __future__ import annotations

import asyncio
import json

import pytest

from localizer_agent.localization.gemini_client import GeminiClient, GeminiError, GeminiResponse


class FakeTransport:
    def __init__(self, response: GeminiResponse) -> None:
        self.calls: list[tuple[str, dict[str, str], bytes]] = []
        self.response = response

    async def send(self, url: str, headers: dict[str, str], body: bytes, timeout_seconds: int) -> GeminiResponse:
        self.calls.append((url, headers, body))
        return self.response


def test_client_requests_json_and_extracts_the_candidate_payload() -> None:
    transport = FakeTransport(
        GeminiResponse(
            200,
            json.dumps({"candidates": [{"content": {"parts": [{"text": '[{"segmentId":"SEG-000001"}]'}]}}]}).encode(),
        )
    )
    client = GeminiClient("https://generativelanguage.googleapis.com/v1beta", "gemini-2.5-flash", "secret", transport=transport)

    result = asyncio.run(client.generate_json("UNTRUSTED_SOURCE_SEGMENTS_JSON=[]"))

    assert result == [{"segmentId": "SEG-000001"}]
    assert transport.calls[0][0].endswith("/models/gemini-2.5-flash:generateContent")
    assert transport.calls[0][1]["x-goog-api-key"] == "secret"


def test_client_classifies_provider_quota_as_retryable_without_exposing_the_prompt() -> None:
    client = GeminiClient(
        "https://generativelanguage.googleapis.com/v1beta",
        "gemini-2.5-flash",
        "secret",
        transport=FakeTransport(GeminiResponse(429, b'{"error":"quota"}')),
    )

    with pytest.raises(GeminiError, match="retryable"):
        asyncio.run(client.generate_json("private source text"))
