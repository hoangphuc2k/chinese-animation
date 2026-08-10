from __future__ import annotations

from pathlib import Path
from unicodedata import combining

import pytest

from localizer_agent.localization.validator import ScriptSegment
from localizer_agent.subtitles.writer import SubtitleError, write_srt, write_webvtt


def grapheme_count(text: str) -> int:
    return sum(1 for character in text if not combining(character))


def test_writer_generates_utf8_srt_and_webvtt_with_safe_wrapped_cues(tmp_path: Path) -> None:
    segments = [ScriptSegment(segment_id="SEG-000001", start_ms=0, end_ms=4000, text="Đây là một câu tiếng Việt đủ dài để cần được chia thành hai dòng dễ đọc.")]

    srt = write_srt(segments, tmp_path / "captions.srt")
    vtt = write_webvtt(segments, tmp_path / "captions.vtt")

    assert srt.read_text(encoding="utf-8").startswith("1\n00:00:00,000 --> 00:00:04,000\n")
    assert vtt.read_text(encoding="utf-8").startswith("WEBVTT\n\n")
    assert all(grapheme_count(line) <= 42 for line in srt.read_text(encoding="utf-8").splitlines() if line and "-->" not in line and line != "1")


def test_writer_rejects_nonmonotonic_or_unreadably_fast_cues(tmp_path: Path) -> None:
    with pytest.raises(SubtitleError):
        write_srt(
            [
                ScriptSegment(segment_id="SEG-000001", start_ms=0, end_ms=1000, text="Xin chào"),
                ScriptSegment(segment_id="SEG-000002", start_ms=900, end_ms=1900, text="thế giới"),
            ],
            tmp_path / "captions.srt",
        )
    with pytest.raises(SubtitleError):
        write_srt([ScriptSegment(segment_id="SEG-000001", start_ms=0, end_ms=1000, text="a" * 30)], tmp_path / "fast.srt")
