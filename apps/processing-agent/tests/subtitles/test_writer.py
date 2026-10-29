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


def cue_blocks(srt_text: str) -> list[list[str]]:
    return [block.splitlines() for block in srt_text.strip().split("\n\n")]


def test_long_segment_is_split_into_consecutive_two_line_cues_within_its_window(tmp_path: Path) -> None:
    # whisper.cpp can return one long segment for continuous speech; it must still become readable cues.
    text = " ".join(["Câu 1: thử nghiệm"] + ["thử nghiệm"] * 30)
    srt = write_srt([ScriptSegment(segment_id="SEG-000001", start_ms=0, end_ms=17_600, text=text)], tmp_path / "long.srt")

    blocks = cue_blocks(srt.read_text(encoding="utf-8"))
    assert len(blocks) > 1
    assert [block[0] for block in blocks] == [str(index) for index in range(1, len(blocks) + 1)]
    assert blocks[0][1].startswith("00:00:00,000 --> ") and blocks[-1][1].endswith(" --> 00:00:17,600")
    for previous, current in zip(blocks, blocks[1:]):
        assert previous[1].split(" --> ")[1] == current[1].split(" --> ")[0]
    for block in blocks:
        assert 1 <= len(block[2:]) <= 2 and all(grapheme_count(line) <= 42 for line in block[2:])
    assert " ".join(line for block in blocks for line in block[2:]) == text


def test_long_segment_that_cannot_be_read_in_its_window_still_stops_for_review(tmp_path: Path) -> None:
    text = " ".join(["thử nghiệm"] * 60)

    with pytest.raises(SubtitleError):
        write_srt([ScriptSegment(segment_id="SEG-000001", start_ms=0, end_ms=3_000, text=text)], tmp_path / "dense.srt")
