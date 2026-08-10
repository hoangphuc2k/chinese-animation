from __future__ import annotations

import pytest

from localizer_agent.localization.validator import ScriptValidationError, validate_script
from localizer_agent.transcription.segments import TranscriptSegment


SOURCE = [
    TranscriptSegment(segment_id="SEG-000001", start_ms=0, end_ms=950, text="你好"),
    TranscriptSegment(segment_id="SEG-000002", start_ms=950, end_ms=1800, text="世界"),
]


def test_validator_preserves_source_segment_ids_order_and_milliseconds() -> None:
    script = validate_script(
        SOURCE,
        [
            {"endMs": 950, "segmentId": "SEG-000001", "startMs": 0, "text": "Xin chào"},
            {"endMs": 1800, "segmentId": "SEG-000002", "startMs": 950, "text": "thế giới"},
        ],
    )

    assert script[0].text == "Xin chào"
    assert script[1].segment_id == "SEG-000002"


def test_validator_rejects_reordered_or_empty_translations() -> None:
    with pytest.raises(ScriptValidationError):
        validate_script(
            SOURCE,
            [
                {"endMs": 1800, "segmentId": "SEG-000002", "startMs": 950, "text": "thế giới"},
                {"endMs": 950, "segmentId": "SEG-000001", "startMs": 0, "text": "Xin chào"},
            ],
        )
    with pytest.raises(ScriptValidationError):
        validate_script(
            SOURCE,
            [
                {"endMs": 950, "segmentId": "SEG-000001", "startMs": 0, "text": ""},
                {"endMs": 1800, "segmentId": "SEG-000002", "startMs": 950, "text": "thế giới"},
            ],
        )
