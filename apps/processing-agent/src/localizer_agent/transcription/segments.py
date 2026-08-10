"""Normalization and validation for immutable timed transcript segments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


class SegmentError(ValueError):
    """Raised when transcription output cannot be represented safely."""


@dataclass(frozen=True)
class TranscriptSegment:
    end_ms: int
    segment_id: str
    start_ms: int
    text: str

    def json_value(self) -> dict[str, int | str]:
        return {"endMs": self.end_ms, "segmentId": self.segment_id, "startMs": self.start_ms, "text": self.text}


def _milliseconds(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SegmentError(f"Transcript {field} must be an integer millisecond value.")
    return value


def normalize_segments(raw_segments: Sequence[Mapping[str, Any]], overlap_tolerance_ms: int = 50) -> list[TranscriptSegment]:
    if overlap_tolerance_ms < 0:
        raise SegmentError("Transcript overlap tolerance cannot be negative.")
    normalized: list[TranscriptSegment] = []
    previous_end_ms = 0
    for index, raw in enumerate(raw_segments, start=1):
        offsets = raw.get("offsets")
        text = raw.get("text")
        if not isinstance(offsets, Mapping) or not isinstance(text, str):
            raise SegmentError("Transcript segments require offsets and text.")
        start_ms = _milliseconds(offsets.get("from"), "start offset")
        end_ms = _milliseconds(offsets.get("to"), "end offset")
        normalized_text = text.strip()
        if start_ms < 0 or end_ms <= start_ms:
            raise SegmentError("Transcript timestamps must be non-negative and increasing.")
        if start_ms < previous_end_ms - overlap_tolerance_ms:
            raise SegmentError("Transcript segments overlap beyond the allowed tolerance.")
        if not normalized_text:
            raise SegmentError("Transcript segments must not be empty.")
        normalized.append(TranscriptSegment(end_ms=end_ms, segment_id=f"SEG-{index:06d}", start_ms=start_ms, text=normalized_text))
        previous_end_ms = end_ms
    return normalized
