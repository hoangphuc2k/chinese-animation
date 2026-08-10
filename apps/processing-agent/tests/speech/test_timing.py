from __future__ import annotations

from localizer_agent.speech.timing import fit_timing


def test_timing_uses_silence_for_short_segments_and_bounded_tempo_for_small_overruns() -> None:
    short = fit_timing(duration_ms=800, window_ms=1000)
    adjusted = fit_timing(duration_ms=1100, window_ms=1000)

    assert short.silence_after_ms == 200
    assert short.tempo == 1.0
    assert adjusted.requires_review is False
    assert adjusted.tempo == 1.1


def test_timing_flags_large_or_unresolvable_overruns_for_review() -> None:
    assert fit_timing(duration_ms=1251, window_ms=1000).requires_review is True
    assert fit_timing(duration_ms=1200, window_ms=1000).requires_review is True
