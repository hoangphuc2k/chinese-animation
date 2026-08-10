"""Validate a provider response against immutable source segment identity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from localizer_agent.transcription.segments import TranscriptSegment


class ScriptValidationError(ValueError):
    """Raised when a generated script cannot safely become a review draft."""


@dataclass(frozen=True)
class ScriptSegment:
    end_ms: int
    segment_id: str
    start_ms: int
    text: str

    def json_value(self) -> dict[str, int | str]:
        return {"endMs": self.end_ms, "segmentId": self.segment_id, "startMs": self.start_ms, "text": self.text}


def validate_script(source_segments: Sequence[TranscriptSegment], translated_segments: Sequence[Mapping[str, Any]]) -> list[ScriptSegment]:
    if len(source_segments) != len(translated_segments):
        raise ScriptValidationError("Translation must contain exactly one entry for every source segment.")
    validated: list[ScriptSegment] = []
    for source, translated in zip(source_segments, translated_segments, strict=True):
        if set(translated) != {"endMs", "segmentId", "startMs", "text"}:
            raise ScriptValidationError("Translation segment fields do not match the canonical script schema.")
        segment_id = translated["segmentId"]
        start_ms = translated["startMs"]
        end_ms = translated["endMs"]
        text = translated["text"]
        if segment_id != source.segment_id or start_ms != source.start_ms or end_ms != source.end_ms:
            raise ScriptValidationError("Translation changed an immutable source segment identity or timestamp.")
        if not isinstance(text, str) or not text.strip() or not text.isprintable():
            raise ScriptValidationError("Translation text must be non-empty printable Unicode.")
        normalized_text = text.strip()
        if len(normalized_text) > max(40, len(source.text) * 12):
            raise ScriptValidationError("Translation exceeds the spoken-length safety envelope.")
        validated.append(ScriptSegment(end_ms=end_ms, segment_id=segment_id, start_ms=start_ms, text=normalized_text))
    return validated
