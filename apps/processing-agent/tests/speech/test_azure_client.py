from __future__ import annotations

import asyncio

import pytest

from localizer_agent.speech.azure_client import AzureSpeechClient, AzureSpeechError, AzureSpeechResponse


class FakeTransport:
    def __init__(self, response: AzureSpeechResponse) -> None:
        self.calls: list[tuple[str, dict[str, str], bytes]] = []
        self.response = response

    async def send(self, url: str, headers: dict[str, str], body: bytes, timeout_seconds: int) -> AzureSpeechResponse:
        self.calls.append((url, headers, body))
        return self.response


def test_azure_client_escapes_ssml_and_requires_an_allowlisted_voice() -> None:
    transport = FakeTransport(AzureSpeechResponse(200, {}, b"RIFFaudio"))
    client = AzureSpeechClient("https://eastus.tts.speech.microsoft.com/cognitiveservices/v1", "secret", {"vi-VN-HoaiMyNeural"}, transport=transport)

    audio = asyncio.run(client.synthesize("request-01", "vi-VN-HoaiMyNeural", "A < B & C"))

    assert audio == b"RIFFaudio"
    assert b"A &lt; B &amp; C" in transport.calls[0][2]
    assert transport.calls[0][1]["X-Microsoft-OutputFormat"] == "riff-16khz-16bit-mono-pcm"
    with pytest.raises(AzureSpeechError):
        asyncio.run(client.synthesize("request-02", "vi-VN-Unapproved", "Xin chao"))


def test_azure_client_marks_retry_after_as_retryable() -> None:
    client = AzureSpeechClient(
        "https://eastus.tts.speech.microsoft.com/cognitiveservices/v1",
        "secret",
        {"vi-VN-HoaiMyNeural"},
        transport=FakeTransport(AzureSpeechResponse(429, {"retry-after": "7"}, b"quota")),
    )

    with pytest.raises(AzureSpeechError, match="retryable") as error:
        asyncio.run(client.synthesize("request-01", "vi-VN-HoaiMyNeural", "Xin chao"))
    assert error.value.retry_after_seconds == 7
