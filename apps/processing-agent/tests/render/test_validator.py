from __future__ import annotations

import pytest

from localizer_agent.render.validator import RenderProbe, RenderValidationError, validate_render


def test_validator_accepts_h264_aac_render_with_bounded_av_drift() -> None:
    validate_render(RenderProbe(audio_duration_ms=10_050, has_audio=True, has_video=True, video_duration_ms=10_000))


@pytest.mark.parametrize(
    "probe",
    [
        RenderProbe(audio_duration_ms=10_300, has_audio=True, has_video=True, video_duration_ms=10_000),
        RenderProbe(audio_duration_ms=10_000, has_audio=False, has_video=True, video_duration_ms=10_000),
        RenderProbe(audio_duration_ms=10_000, has_audio=True, has_video=False, video_duration_ms=10_000),
    ],
)
def test_validator_rejects_missing_streams_or_excessive_drift(probe: RenderProbe) -> None:
    with pytest.raises(RenderValidationError):
        validate_render(probe)
