# Phase 05 — Localization and Media Pipeline

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert validated Chinese animation media into reviewable Vietnamese scripts, timed speech, subtitles and immutable preview renders.

**Architecture:** The agent executes one idempotent stage per lease. Every stage consumes hash-addressed inputs, emits hash-addressed outputs and records provider/tool versions. Human script approval separates language generation from TTS/rendering.

**Tech Stack:** Python, whisper.cpp, Gemini, Azure Speech, FFmpeg/ffprobe, WebVTT/SRT, pytest.

## Exclusive file ownership

Phase 05 owns agent modules under `transcription`, `localization`, `speech`,
`subtitles`, `render`, their tests and media fixtures. It may call existing
Worker APIs but cannot change approval semantics or publishing clients.

### Task 1: Define the stage runner and artifact lineage

**Files:**
- Create: `apps/processing-agent/agent/pipeline/stage.py`
- Create: `apps/processing-agent/agent/pipeline/runner.py`
- Create: `apps/processing-agent/agent/pipeline/lineage.py`
- Test: `apps/processing-agent/tests/pipeline/test_runner.py`

- [ ] Test deterministic stage keys from job/version/input hashes/config version,
  completed-stage replay, cancellation, timeout, lease loss and corrupt output.
- [ ] Execute tools without a shell, cap runtime/resources and capture redacted
  stderr plus tool version.
- [ ] Atomically publish artifacts only after validation; send progress at
  coarse bounded intervals.
- [ ] Run `uv run --project apps/processing-agent pytest -q tests/pipeline/test_runner.py`.

### Task 2: Transcribe Chinese speech into timed segments

**Files:**
- Create: `apps/processing-agent/agent/transcription/whisper_cpp.py`
- Create: `apps/processing-agent/agent/transcription/segments.py`
- Test: `apps/processing-agent/tests/transcription/test_whisper_cpp.py`
- Test: `apps/processing-agent/tests/transcription/test_segments.py`

- [ ] Test command construction, model checksum, language selection, malformed
  output, overlapping/negative timestamps, silence and timeout.
- [ ] Extract bounded mono PCM audio; run pinned whisper.cpp model and record its
  binary/model hashes.
- [ ] Normalize segments to stable IDs with start/end milliseconds and source
  text; reject overlap beyond the documented tolerance.
- [ ] Preserve source transcription as an immutable artifact and report its
  hash/version.
- [ ] Run focused transcription tests with short Chinese fixtures.

### Task 3: Translate into schema-safe Vietnamese script versions

**Files:**
- Create: `apps/processing-agent/agent/localization/gemini_client.py`
- Create: `apps/processing-agent/agent/localization/prompt.py`
- Create: `apps/processing-agent/agent/localization/validator.py`
- Test: `apps/processing-agent/tests/localization/test_gemini_client.py`
- Test: `apps/processing-agent/tests/localization/test_validator.py`

- [ ] Test exact segment-ID preservation, timestamp preservation, JSON schema,
  empty/unsafe output, prompt injection in source text, hallucinated facts and
  provider quota/error classification.
- [ ] Treat source text as quoted data, request Vietnamese suitable for spoken
  delivery, and prohibit added story facts or changed names without glossary.
- [ ] Validate segment count/order, length envelope and Unicode; retry one repair
  request only for structurally repairable output.
- [ ] Create an immutable draft script through the control-plane API with source
  artifact hash, model ID, prompt version and glossary version.
- [ ] Run focused localization tests.

### Task 4: Gate downstream work on exact script approval

**Files:**
- Create: `apps/processing-agent/agent/pipeline/approval_gate.py`
- Test: `apps/processing-agent/tests/pipeline/test_approval_gate.py`

- [ ] Test unapproved, approved, superseded, hash-mismatched, revoked and
  concurrently edited script versions.
- [ ] Fetch approval immediately before TTS and require job ID, script version
  and script SHA-256 to match the claimed stage.
- [ ] On mismatch, report a non-retryable stale-input result so the Worker can
  enqueue the current version instead of processing obsolete text.
- [ ] Run the focused approval-gate tests.

### Task 5: Synthesize and fit Vietnamese speech

**Files:**
- Create: `apps/processing-agent/agent/speech/azure_client.py`
- Create: `apps/processing-agent/agent/speech/timing.py`
- Create: `apps/processing-agent/agent/speech/audio.py`
- Test: `apps/processing-agent/tests/speech/test_timing.py`
- Test: `apps/processing-agent/tests/speech/test_azure_client.py`

- [ ] Test SSML escaping, voice allowlist, retry-after, invalid audio, segment
  overflow, time-stretch bounds and unresolvable timing.
- [ ] Synthesize per segment with stable request IDs; cache only by approved
  script hash, voice/config version and provider model.
- [ ] Fit speech using silence placement and bounded tempo adjustment. Flag
  segments outside the approved timing threshold for human revision rather than
  applying extreme speed changes.
- [ ] Loudness-normalize and validate duration/codec without clipping; preserve
  segment-level hashes and provider request IDs.
- [ ] Run focused speech tests.

### Task 6: Build subtitles and immutable render

**Files:**
- Create: `apps/processing-agent/agent/subtitles/writer.py`
- Create: `apps/processing-agent/agent/render/ffmpeg.py`
- Create: `apps/processing-agent/agent/render/validator.py`
- Test: `apps/processing-agent/tests/subtitles/test_writer.py`
- Test: `apps/processing-agent/tests/render/test_ffmpeg.py`
- Test: `apps/processing-agent/tests/render/test_validator.py`

- [ ] Test cue escaping, line wrapping, monotonic timing, render arguments,
  missing streams, A/V drift, duration mismatch and output corruption.
- [ ] Generate UTF-8 SRT and WebVTT from the approved script and fitted timing.
- [ ] Mix Vietnamese speech with allowed original ambience under documented
  gain/ducking settings; burn subtitles using code-controlled styling.
- [ ] Render to the PoC-proven H.264/AAC profile, write metadata without source
  secrets and calculate SHA-256/bytes/duration.
- [ ] Reject A/V drift and duration difference beyond design thresholds.
- [ ] Run focused subtitle/render tests.

### Task 7: Expose expiring preview through the existing boundary

**Files:**
- Create: `apps/processing-agent/agent/preview/registry.py`
- Create: `apps/processing-agent/deploy/nginx/localizer-preview.conf`
- Test: `apps/processing-agent/tests/preview/test_registry.py`
- Create: `apps/processing-agent/tests/integration/test_localization_flow.py`

- [ ] Test opaque job-bound preview IDs, expiry, revocation, cross-job access,
  Range requests and absent-file behavior.
- [ ] Register only validated render/subtitle artifacts; keep real paths inside
  the agent/Nginx boundary and use the PoC-proven tunnel.
- [ ] Report an immutable render version and preview descriptor; never mark it
  publish-approved automatically.
- [ ] Run the integration flow: normalized media → transcript → script draft →
  simulated exact approval → TTS → subtitles → render → preview.
- [ ] Run full agent lint, type and test commands and record fixture output hash.

## Phase exit criteria

- An exact approved script produces a validated immutable render and expiring
  preview.
- Script edits invalidate downstream approval and stale work cannot complete.
- Timing exceptions become reviewable issues, not silently degraded audio.
- All artifacts contain lineage, hashes and reproducible tool/provider versions.

