"""Safe, readable SRT and WebVTT generation from immutable script timing."""

from __future__ import annotations

import os
import tempfile
import unicodedata
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
    if len(lines) > 2:
        raise SubtitleError("Subtitle cue cannot fit within two readable lines.")
    return lines


def _timecode(milliseconds: int, separator: str) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, remainder = divmod(remainder, 1_000)
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{remainder:03}"


def _validate(segments: Sequence[ScriptSegment]) -> list[list[str]]:
    previous_end = 0
    wrapped: list[list[str]] = []
    for segment in segments:
        duration = segment.end_ms - segment.start_ms
        if segment.start_ms < previous_end or duration < 1_000:
            raise SubtitleError("Subtitle cues must be monotonic and visible for at least one second.")
        lines = _wrap(segment.text)
        reading_length = sum(len(_graphemes(line)) for line in lines)
        if reading_length > duration / 1_000 * 20:
            raise SubtitleError("Subtitle cue exceeds the 20-grapheme-per-second reading limit.")
        wrapped.append(lines)
        previous_end = segment.end_ms
    return wrapped


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
    wrapped = _validate(segments)
    cues = []
    for index, (segment, lines) in enumerate(zip(segments, wrapped, strict=True), start=1):
        cue_text = "\n".join(lines)
        cues.append(f"{index}\n{_timecode(segment.start_ms, ',')} --> {_timecode(segment.end_ms, ',')}\n{cue_text}")
    return _atomic_write(destination, "\n\n".join(cues) + ("\n" if cues else ""))


def write_webvtt(segments: Sequence[ScriptSegment], destination: Path) -> Path:
    wrapped = _validate(segments)
    cues = []
    for segment, lines in zip(segments, wrapped, strict=True):
        cue_text = "\n".join(lines)
        cues.append(f"{_timecode(segment.start_ms, '.')} --> {_timecode(segment.end_ms, '.')}\n{cue_text}")
    return _atomic_write(destination, "WEBVTT\n\n" + "\n\n".join(cues) + ("\n" if cues else ""))
