"""Safe, readable SRT and WebVTT generation from immutable script timing."""

from __future__ import annotations

import os
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from localizer_agent.localization.validator import ScriptSegment


class SubtitleError(ValueError):
    """Raised when script timing cannot safely become readable subtitles."""


def _graphemes(text: str) -> list[str]:
    clusters: list[str] = []
    for character in text:
        if unicodedata.combining(character) and clusters:
            clusters[-1] += character
        else:
            clusters.append(character)
    return clusters


def _wrap(text: str) -> list[str]:
    words = text.replace("-->", "→").split()
    if not words:
        raise SubtitleError("Subtitle text must not be empty.")
    lines: list[str] = []
    current = ""
    for word in words:
        if len(_graphemes(word)) > 42:
            raise SubtitleError("A subtitle word exceeds the 42-grapheme line limit.")
        candidate = word if not current else f"{current} {word}"
        if len(_graphemes(candidate)) <= 42:
            current = candidate
            continue
        lines.append(current)
        current = word
    lines.append(current)
    return lines


@dataclass(frozen=True)
class _Cue:
    start_ms: int
    end_ms: int
    lines: list[str]


def _timecode(milliseconds: int, separator: str) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, remainder = divmod(remainder, 1_000)
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{remainder:03}"


def _segment_cues(segment: ScriptSegment) -> list[_Cue]:
    """Split one segment into consecutive cues of at most two lines, timed by their share of the text."""
    lines = _wrap(segment.text)
    groups = [lines[index : index + 2] for index in range(0, len(lines), 2)]
    weights = [sum(len(_graphemes(line)) for line in group) for group in groups]
    total = sum(weights)
    duration = segment.end_ms - segment.start_ms
    cues: list[_Cue] = []
    start = segment.start_ms
    consumed = 0
    for index, (group, weight) in enumerate(zip(groups, weights, strict=True)):
        consumed += weight
        end = segment.end_ms if index == len(groups) - 1 else segment.start_ms + round(duration * consumed / total)
        cues.append(_Cue(start_ms=start, end_ms=end, lines=group))
        start = end
    return cues


def _validate(segments: Sequence[ScriptSegment]) -> list[_Cue]:
    previous_end = 0
    cues: list[_Cue] = []
    for segment in segments:
        if segment.start_ms < previous_end or segment.end_ms - segment.start_ms < 1_000:
            raise SubtitleError("Subtitle cues must be monotonic and visible for at least one second.")
        for cue in _segment_cues(segment):
            duration = cue.end_ms - cue.start_ms
            if duration < 1_000:
                raise SubtitleError("Subtitle cues must be monotonic and visible for at least one second.")
            reading_length = sum(len(_graphemes(line)) for line in cue.lines)
            if reading_length > duration / 1_000 * 20:
                raise SubtitleError("Subtitle cue exceeds the 20-grapheme-per-second reading limit.")
            cues.append(cue)
        previous_end = segment.end_ms
    return cues


def _atomic_write(path: Path, content: str) -> Path:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".subtitle-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return path


def write_srt(segments: Sequence[ScriptSegment], destination: Path) -> Path:
    cues = [
        f"{index}\n{_timecode(cue.start_ms, ',')} --> {_timecode(cue.end_ms, ',')}\n" + "\n".join(cue.lines)
        for index, cue in enumerate(_validate(segments), start=1)
    ]
    return _atomic_write(destination, "\n\n".join(cues) + ("\n" if cues else ""))


def write_webvtt(segments: Sequence[ScriptSegment], destination: Path) -> Path:
    cues = [
        f"{_timecode(cue.start_ms, '.')} --> {_timecode(cue.end_ms, '.')}\n" + "\n".join(cue.lines)
        for cue in _validate(segments)
    ]
    return _atomic_write(destination, "WEBVTT\n\n" + "\n\n".join(cues) + ("\n" if cues else ""))
