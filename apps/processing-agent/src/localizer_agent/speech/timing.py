"""Conservative timing fitting that escalates instead of over-compressing speech."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TimingFit:
    requires_review: bool
    silence_after_ms: int
    tempo: float


def fit_timing(duration_ms: int, window_ms: int) -> TimingFit:
    if duration_ms <= 0 or window_ms <= 0:
        raise ValueError("Speech duration and source timing window must be positive.")
    if duration_ms <= window_ms:
        return TimingFit(requires_review=False, silence_after_ms=window_ms - duration_ms, tempo=1.0)
    overrun_ms = duration_ms - window_ms
    required_tempo = duration_ms / window_ms
    if overrun_ms > 250 or required_tempo > 1.10:
        return TimingFit(requires_review=True, silence_after_ms=0, tempo=1.0)
    return TimingFit(requires_review=False, silence_after_ms=0, tempo=round(required_tempo, 4))
