"""Validation of final preview streams before immutable registration."""

from __future__ import annotations

from dataclasses import dataclass


class RenderValidationError(ValueError):
    """Raised when a candidate preview cannot be safely registered."""


@dataclass(frozen=True)
class RenderProbe:
    audio_codec: str | None
    audio_duration_ms: int
    has_audio: bool
    has_video: bool
    video_codec: str | None
    video_duration_ms: int


def validate_render(probe: RenderProbe, max_drift_ms: int = 250) -> None:
    if not probe.has_audio or not probe.has_video:
        raise RenderValidationError("Rendered preview must contain both video and Vietnamese audio streams.")
    if probe.audio_codec != "aac" or probe.video_codec != "h264":
        raise RenderValidationError("Rendered preview must use H.264 video and AAC audio.")
    if probe.audio_duration_ms <= 0 or probe.video_duration_ms <= 0:
        raise RenderValidationError("Rendered preview streams must have positive duration.")
    if abs(probe.audio_duration_ms - probe.video_duration_ms) > max_drift_ms:
        raise RenderValidationError("Rendered preview audio/video duration drift exceeds the approved limit.")
