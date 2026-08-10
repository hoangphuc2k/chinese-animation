# Phase 05 — Localization and Media Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the OCI processing agent from safe ingest to an approval-gated localization, Vietnamese voice, subtitle, render, and preview workflow.

**Architecture:** The existing `src/localizer_agent` package runs one lease-bound stage at a time. It reads and writes hash-addressed OCI workspace artifacts, while the Worker persists only sanitized lineage and exact script/render approval references. A versioned agent claim carries only the inputs needed for the claimed state; the agent rechecks the exact script approval before it calls Azure or renders.

**Tech Stack:** Python 3.12, Pydantic, standard-library HTTP, whisper.cpp, Gemini Developer API, Azure Speech, FFmpeg/ffprobe, WebVTT/SRT, Cloudflare Worker/D1, pytest and Vitest.

## Global Constraints

- Keep all agent production modules under `apps/processing-agent/src/localizer_agent/`; do not create the obsolete `agent/` tree.
- Media bodies and OCI paths stay in the agent boundary. D1 holds only IDs, hashes, byte counts, public-safe metadata and opaque artifact references.
- Never send source text to Gemini unless the claimed rights metadata explicitly permits Gemini/Google processing.
- Preserve segment IDs and millisecond timestamps across transcript, translation, TTS, subtitle and render artifacts.
- The first approval binds `jobId`, `scriptVersion`, and canonical script SHA-256. A stale, revoked, superseded, or hash-mismatched approval never reaches TTS or rendering.
- Azure TTS may adjust tempo only within `0.90`–`1.10`; an overrun above 250 ms produces `TIMING_REVIEW_REQUIRED`.
- Use command argument vectors with `shell=False`; capture only redacted stderr and enforce tool timeouts.
- Generate UTF-8 SRT and WebVTT with at most two 42-grapheme lines, at least one second per cue and target reading speed at most 20 graphemes/second.
- Retain temporary artifacts only on OCI and make previews job-bound and expiring (maximum five minutes).
- Existing local native D1 migration remains an environment gate on macOS 12. Do not downgrade the retained Wrangler version.

## File Structure

| Path | Responsibility |
| --- | --- |
| `packages/contracts/src/jobs.ts` | Versioned state-specific agent claim/completion payloads and artifact lineage schema. |
| `apps/control-plane/db/migrations/0005_pipeline_lineage.sql` | Persist sanitized per-stage artifact lineage and script payload/approval lookup data. |
| `apps/control-plane/src/services/lease-service.ts` | Atomically expose state input, record completion lineage, and enforce lease/state/version conditions. |
| `apps/control-plane/src/api/agent-routes.ts` | Parse versioned agent completions and expose the exact approval lookup endpoint. |
| `apps/processing-agent/src/localizer_agent/pipeline/` | Idempotent stage keys, atomic artifacts, lease/timeout guard and exact approval check. |
| `apps/processing-agent/src/localizer_agent/transcription/` | PCM extraction, pinned whisper.cpp execution and immutable timed segments. |
| `apps/processing-agent/src/localizer_agent/localization/` | Safe Gemini request/response handling and Vietnamese script validation. |
| `apps/processing-agent/src/localizer_agent/speech/` | Azure request handling, TTS quota/timing validation and audio fitting. |
| `apps/processing-agent/src/localizer_agent/subtitles/` | Deterministic SRT/WebVTT creation. |
| `apps/processing-agent/src/localizer_agent/render/` | FFmpeg render command construction and media validation. |
| `apps/processing-agent/src/localizer_agent/preview/` | Opaque, expiring OCI preview registry. |
| `apps/processing-agent/tests/` | Focused unit tests plus a provider-free localization-flow integration test. |

### Task 1: Add state-aware agent contracts and sanitized lineage

**Files:**
- Modify: `packages/contracts/src/jobs.ts`
- Modify: `packages/contracts/scripts/generate.ts`
- Modify: `packages/contracts/scripts/check-generated.ts`
- Modify: `apps/control-plane/src/api/agent-routes.ts`
- Modify: `apps/control-plane/src/services/lease-service.ts`
- Create: `apps/control-plane/db/migrations/0005_pipeline_lineage.sql`
- Test: `packages/contracts/src/jobs-pipeline.test.ts`
- Test: `apps/control-plane/test/agent-api.test.ts`
- Test: `apps/control-plane/test/lease-concurrency.test.ts`

**Interfaces:**
- Consumes: the existing lease token/version and `ProcessingState` state machine.
- Produces: `PipelineClaimInput`, `PipelineArtifact`, and `PipelineCompletion` schemas. Claims include the current state and only an opaque artifact ID plus hash/bytes/metadata; completions can advance exactly one legal state and append sanitized lineage.

- [ ] **Step 1: Write failing contract and API tests**

```ts
it("rejects a transcript artifact for an ingest completion", () => {
  expect(() => CompleteStepRequest.parse({
    expectedState: "INGESTING", state: "PROBING", step: "ingested",
    jobVersion: 1, leaseToken: token, artifacts: [{ kind: "TRANSCRIPT", sha256: hash, byteCount: 1 }],
  })).toThrow();
});

it("returns only the input required by the claimed processing state", async () => {
  const response = await app.request("/jobs/claim", claimRequest);
  expect(await response.json()).toMatchObject({ state: "TRANSCRIBING", pipelineInput: { normalizedMedia: { sha256: hash } } });
});
```

- [ ] **Step 2: Run the focused tests and confirm they fail because the pipeline schemas/claim fields do not exist**

Run: `rtk npm test --workspace=@localizer/contracts -- jobs-pipeline.test.ts` and `rtk npm test --workspace=@localizer/control-plane -- agent-api.test.ts`

- [ ] **Step 3: Implement the additive schemas, migration, Worker claim projection, and conditional atomic lineage append**

```ts
const PipelineArtifact = z.object({
  kind: z.enum(["NORMALIZED_MEDIA", "TRANSCRIPT", "SCRIPT", "VOICE", "SUBTITLES", "RENDER"]),
  sha256: sha256,
  byteCount: z.number().int().nonnegative(),
  toolVersion: z.string().min(1).max(160),
}).strict();

if (input.pipelineCompletion) completion.pipelineCompletion = input.pipelineCompletion;
```

Store the JSON only on the successful `UPDATE jobs ... WHERE lease_owner ... AND status ... AND version ...` path. The claim projection must omit rights evidence text, URLs and filesystem paths.

- [ ] **Step 4: Regenerate contracts and run focused tests**

Run: `rtk npm run generate --workspace=@localizer/contracts && rtk npm test --workspace=@localizer/contracts -- jobs-pipeline.test.ts && rtk npm test --workspace=@localizer/control-plane -- agent-api.test.ts lease-concurrency.test.ts`

Expected: green tests and generated TypeScript/Python clients contain the new schemas.

### Task 2: Build the idempotent lease-bound stage foundation

**Files:**
- Create: `apps/processing-agent/src/localizer_agent/pipeline/stage.py`
- Create: `apps/processing-agent/src/localizer_agent/pipeline/runner.py`
- Create: `apps/processing-agent/src/localizer_agent/pipeline/lineage.py`
- Modify: `apps/processing-agent/src/localizer_agent/workspace.py`
- Test: `apps/processing-agent/tests/pipeline/test_runner.py`
- Test: `apps/processing-agent/tests/pipeline/test_lineage.py`

**Interfaces:**
- Consumes: `LeaseHeartbeatGuard`, `Workspace`, and a generated pipeline claim.
- Produces: `StageKey.for_input(job_id, job_version, stage, input_hash, config_version)`, `ArtifactRecord`, and `StageRunner.run(...)`.

- [ ] **Step 1: Write failing runner tests**

```python
def test_stage_key_changes_when_the_input_hash_changes() -> None:
    assert StageKey.for_input("job", 3, "TRANSCRIBING", "a" * 64, "v1") != StageKey.for_input("job", 3, "TRANSCRIBING", "b" * 64, "v1")

def test_runner_reuses_only_a_validated_completed_artifact(tmp_path: Path) -> None:
    runner = StageRunner(workspace(tmp_path), successful_tool)
    assert runner.run(stage_input).replayed is False
    assert runner.run(stage_input).replayed is True
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run: `rtk uv run --directory apps/processing-agent pytest -q tests/pipeline/test_runner.py tests/pipeline/test_lineage.py`

- [ ] **Step 3: Implement atomic artifact publishing and bounded stage execution**

```python
with workspace.atomic_output(stage_key, suffix=".json") as candidate:
    result = execute(candidate)
    artifact = ArtifactRecord.from_path(candidate, kind=kind, tool_version=tool_version)
    artifact.validate_expected_hash()
return workspace.publish(stage_key, artifact)
```

The runner must delete only its uncommitted temporary candidate on failure, surface lease loss/cancellation before completion, and retain redacted tool stderr in the local manifest.

- [ ] **Step 4: Run the focused tests and agent suite**

Run: `rtk uv run --directory apps/processing-agent pytest -q tests/pipeline/test_runner.py tests/pipeline/test_lineage.py`

### Task 3: Transcribe a normalized source into immutable Chinese segments

**Files:**
- Create: `apps/processing-agent/src/localizer_agent/transcription/whisper_cpp.py`
- Create: `apps/processing-agent/src/localizer_agent/transcription/segments.py`
- Test: `apps/processing-agent/tests/transcription/test_whisper_cpp.py`
- Test: `apps/processing-agent/tests/transcription/test_segments.py`

**Interfaces:**
- Consumes: a normalized-media `ArtifactRecord` and configured whisper binary/model paths.
- Produces: `TranscriptArtifact(sha256, segments, model_sha256, binary_version)`.

- [ ] **Step 1: Write failing tests for command vectors and immutable segment validation**

```python
def test_command_forces_chinese_and_json_output() -> None:
    assert WhisperCpp(model, binary).command(audio)[-4:] == ["-l", "zh", "-oj", "-of"]

def test_segments_reject_overlaps_beyond_tolerance() -> None:
    with pytest.raises(SegmentError):
        normalize_segments([raw(0, 1000), raw(900, 1200)])
```

- [ ] **Step 2: Run RED, then implement PCM extraction, checksum checks, tool execution and JSON normalization**

The FFmpeg vector creates bounded mono 16 kHz PCM; whisper receives `-l zh`, timeout and a pinned model path. Normalize to stable `segment_id`, `start_ms`, `end_ms`, source text and optional confidence. Reject negative timestamps, malformed output and overlap greater than 50 ms.

- [ ] **Step 3: Run focused transcription tests**

Run: `rtk uv run --directory apps/processing-agent pytest -q tests/transcription`

### Task 4: Translate an immutable transcript and create a reviewable script

**Files:**
- Create: `apps/processing-agent/src/localizer_agent/localization/gemini_client.py`
- Create: `apps/processing-agent/src/localizer_agent/localization/prompt.py`
- Create: `apps/processing-agent/src/localizer_agent/localization/validator.py`
- Modify: `apps/processing-agent/src/localizer_agent/control_plane.py`
- Test: `apps/processing-agent/tests/localization/test_gemini_client.py`
- Test: `apps/processing-agent/tests/localization/test_validator.py`
- Test: `apps/processing-agent/tests/test_control_plane_client.py`

**Interfaces:**
- Consumes: immutable transcript segments, glossary version, provider configuration and a claim stating Gemini processing is permitted.
- Produces: canonical Vietnamese `ScriptDraft` and a Worker draft-script creation call carrying the source artifact hash, model ID and prompt/glossary versions.

- [ ] **Step 1: Write failing validator and request tests**

```python
def test_validator_preserves_order_ids_and_milliseconds() -> None:
    with pytest.raises(ScriptValidationError):
        validate_script(source_segments, translated_with_reordered_ids)

def test_prompt_quotes_untrusted_source_text() -> None:
    assert 'UNTRUSTED_SOURCE_SEGMENTS_JSON' in build_prompt(source_segments, glossary)
```

- [ ] **Step 2: Run RED, then implement single-retry structurally repairable JSON handling**

Require exact IDs, exact timestamps, Vietnamese Unicode text, source-order preservation and a documented spoken-length envelope. Treat source text as JSON data, never instruction text. Classify provider rate/quota/transport errors without logging prompts or source text.

- [ ] **Step 3: Run focused localization/client tests**

Run: `rtk uv run --directory apps/processing-agent pytest -q tests/localization tests/test_control_plane_client.py`

### Task 5: Require exact approval, synthesize Vietnamese speech and flag timing exceptions

**Files:**
- Create: `apps/processing-agent/src/localizer_agent/pipeline/approval_gate.py`
- Create: `apps/processing-agent/src/localizer_agent/speech/azure_client.py`
- Create: `apps/processing-agent/src/localizer_agent/speech/timing.py`
- Create: `apps/processing-agent/src/localizer_agent/speech/audio.py`
- Test: `apps/processing-agent/tests/pipeline/test_approval_gate.py`
- Test: `apps/processing-agent/tests/speech/test_azure_client.py`
- Test: `apps/processing-agent/tests/speech/test_timing.py`

**Interfaces:**
- Consumes: a claim's `scriptVersion` and `scriptSha256`, fresh Worker approval lookup, allowed Azure voice configuration, and approved script segments.
- Produces: per-segment `VoiceArtifact` hashes or `TimingReviewRequired`.

- [ ] **Step 1: Write failing approval, SSML and timing tests**

```python
def test_gate_rejects_a_superseded_script_hash() -> None:
    with pytest.raises(StaleApprovalError):
        require_exact_approval(job_id, version=2, sha256="a" * 64, approval=approved(version=3))

def test_overrun_beyond_250_ms_needs_human_review() -> None:
    assert fit_timing(duration_ms=1_251, window_ms=1_000).requires_review is True
```

- [ ] **Step 2: Run RED, then implement the approval lookup and bounded voice path**

Escape SSML, allow only configured voices, include a stable provider request ID, count quota before sending, parse `Retry-After`, validate WAV/codec/duration and cache by exact script hash + voice config + provider model. Try silence placement and only a `0.90`–`1.10` tempo adjustment; do not silently compress an exception.

- [ ] **Step 3: Run focused approval and speech tests**

Run: `rtk uv run --directory apps/processing-agent pytest -q tests/pipeline/test_approval_gate.py tests/speech`

### Task 6: Produce readable subtitles and a validated immutable render

**Files:**
- Create: `apps/processing-agent/src/localizer_agent/subtitles/writer.py`
- Create: `apps/processing-agent/src/localizer_agent/render/ffmpeg.py`
- Create: `apps/processing-agent/src/localizer_agent/render/validator.py`
- Test: `apps/processing-agent/tests/subtitles/test_writer.py`
- Test: `apps/processing-agent/tests/render/test_ffmpeg.py`
- Test: `apps/processing-agent/tests/render/test_validator.py`

**Interfaces:**
- Consumes: exact approved script, fitted voice artifacts, normalized media and configured watermark asset.
- Produces: SRT/WebVTT artifacts and a validated H.264/AAC `RenderArtifact`.

- [ ] **Step 1: Write failing cue, FFmpeg-vector and render-validation tests**

```python
def test_writer_wraps_to_two_42_grapheme_lines() -> None:
    assert all(len(line) <= 42 for line in cue_lines(long_vietnamese_text))

def test_render_command_uses_no_shell_and_burns_subtitles() -> None:
    assert "subtitles=" in build_render_command(inputs).filter_graph

def test_validator_rejects_more_than_250_ms_av_drift() -> None:
    with pytest.raises(RenderValidationError):
        validate_render(probe(video_seconds=10.3, audio_seconds=10.0))
```

- [ ] **Step 2: Run RED, then implement deterministic writers and render execution**

Build SRT and VTT from millisecond times with HTML/SRT escaping, one-second minimum display and monotonic timing. Use FFmpeg vector arguments for H.264/AAC, optional source-audio ducking, code-controlled subtitle style, and top-left watermark scaling. Probe output, calculate SHA-256/bytes/duration and reject corruption, missing streams or drift/duration difference above 250 ms.

- [ ] **Step 3: Run focused subtitle/render tests**

Run: `rtk uv run --directory apps/processing-agent pytest -q tests/subtitles tests/render`

### Task 7: Register expiring previews and run an end-to-end provider-free flow

**Files:**
- Create: `apps/processing-agent/src/localizer_agent/preview/registry.py`
- Create: `apps/processing-agent/deploy/nginx/localizer-preview.conf`
- Modify: `apps/processing-agent/src/localizer_agent/supervisor.py`
- Modify: `apps/processing-agent/src/localizer_agent/__main__.py`
- Test: `apps/processing-agent/tests/preview/test_registry.py`
- Test: `apps/processing-agent/tests/integration/test_localization_flow.py`
- Modify: `docs/implementation/status.md`
- Create: `docs/implementation/phase-05-report.md`

**Interfaces:**
- Consumes: only a validated render/subtitle artifact pair.
- Produces: a job-bound opaque preview descriptor with maximum five-minute expiry and a supervisor that reports each legal state completion.

- [ ] **Step 1: Write failing registry and integration tests**

```python
def test_preview_token_cannot_be_used_for_another_job() -> None:
    token = registry.register("job-a", render)
    assert registry.resolve(token, "job-b", now) is None

def test_flow_never_runs_tts_before_exact_approval() -> None:
    assert run_flow(approval=None).state == "WAITING_SCRIPT_REVIEW"
```

- [ ] **Step 2: Run RED, then implement preview/runner integration**

Create URL-safe random opaque IDs, store only a relative render reference plus expiry/revocation metadata, and make Nginx support only bounded Range responses after its authorization subrequest. The supervisor claims only states it implements, reports artifact lineage, pauses at script review, and turns stale approval/timing failures into the corresponding legal Worker state.

- [ ] **Step 3: Run focused and full verification, then document exact evidence**

Run: `rtk npm run generate --workspace=@localizer/contracts && rtk npm run verify && rtk uv run --directory apps/processing-agent pytest -q`

Record passed test counts, generated contract check, fixture render hash, and any external-runtime gate in the Phase 5 report and status table. Do not claim a Docker, OCI, Gemini, Azure, or native D1 run unless its command actually passed.

## Plan Self-Review

- The plan maps each original Phase 5 requirement to an owned task: contract lineage (1), idempotence/recovery (2), Chinese transcript (3), schema-safe Vietnamese script (4), exact approval and TTS (5), subtitles/render (6), preview and end-to-end integration (7).
- The plan uses the actual `src/localizer_agent` layout and extends the present IMPORT-only claim API additively; it does not alter approval binding semantics.
- No provider credential is required by unit/integration tests; HTTP, tool and media boundaries use fakes.
- Native D1 remains explicitly recorded as an environment-only validation gap.
