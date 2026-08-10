from __future__ import annotations

import pytest

from localizer_agent.transcription.segments import SegmentError, normalize_segments


def test_normalize_segments_assigns_stable_ids_and_millisecond_timestamps() -> None:
    segments = normalize_segments(
        [
            {"offsets": {"from": 0, "to": 950}, "text": "  你好  "},
            {"offsets": {"from": 950, "to": 1800}, "text": "世界"},
        ]
    )

    assert [segment.segment_id for segment in segments] == ["SEG-000001", "SEG-000002"]
    assert [(segment.start_ms, segment.end_ms, segment.text) for segment in segments] == [(0, 950, "你好"), (950, 1800, "世界")]


def test_normalize_segments_allows_silence_but_rejects_negative_or_overlapping_timestamps() -> None:
    assert normalize_segments([]) == []

    with pytest.raises(SegmentError):
        normalize_segments([{"offsets": {"from": -1, "to": 1000}, "text": "你好"}])
    with pytest.raises(SegmentError):
        normalize_segments(
            [
                {"offsets": {"from": 0, "to": 1000}, "text": "你好"},
                {"offsets": {"from": 900, "to": 1200}, "text": "世界"},
            ]
        )
