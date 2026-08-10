# Phase 5 Production Wiring Remediation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the existing Phase 5 component modules into a reachable, state-aware production workflow whose control-plane claims contain sufficient sanitized inputs.

**Architecture:** D1 remains the authority for job state, exact approvals, and sanitized artifact lineage; OCI remains the authority for media and text artifact bodies. The Worker projects a strict state-specific claim from persisted lineage, and a general processing-agent supervisor dispatches one lease-bound stage per claim. Existing provider and media modules stay behind injected stage handlers so the runtime can enforce rights and approval gates before external calls.

**Tech Stack:** TypeScript/Zod/Hono/D1, generated Pydantic models, Python 3.12, standard-library HTTP, FFmpeg/ffprobe, whisper.cpp, Gemini, Azure Speech.

## Global Constraints

- Production call paths and persisted invariants are the primary review target; tests are regression evidence only.
- Claims and D1 records may contain opaque references, hashes, byte counts, versions, and public-safe metadata, but never OCI absolute paths, source URLs, credentials, or rights-evidence text.
- A claimed stage receives only the artifacts required by its current state.
- Every completion remains conditional on lease owner, token hash, unexpired lease, expected state, and exact job version.
- Gemini is unreachable unless the persisted rights policy explicitly permits Google processing.
- TTS and rendering are unreachable unless a fresh approval matches job ID, script version, and canonical script SHA-256.
- Existing uncommitted implementation work is preserved in place; no unrelated files are reverted or reformatted.

### Task 1: Make the control-plane claim contract state-aware

**Files:**
- Modify: `packages/contracts/src/jobs.ts`
- Modify: `apps/control-plane/db/migrations/0005_pipeline_lineage.sql`
- Modify: `apps/control-plane/src/services/lease-service.ts`
- Modify: generated TypeScript/Python contract artifacts
- Test: `packages/contracts/src/jobs-pipeline.test.ts`
- Test: `apps/control-plane/test/agent-api.test.ts`

**Interfaces:**
- Consumes: current job state, `ingest_result_json`, sanitized `pipeline_lineage_json`, script version/hash, active approval, and rights provider policy.
- Produces: a strict `pipelineInput` discriminated by `state`, containing only required `PipelineArtifact` records and exact script/approval metadata.

- [ ] Add a failing contract case proving a non-import pipeline claim without state-specific input is rejected.
- [ ] Add a failing lease/API case proving a `TRANSCRIBING` claim projects normalized-media identity without locators or paths.
- [ ] Extend the migration and schemas only with fields required for artifact/script projection and exact approval lookup.
- [ ] Parse and validate persisted JSON at the claim boundary; reject internally inconsistent lineage instead of issuing an unusable lease.
- [ ] Regenerate models and run focused contract/control-plane checks.

### Task 2: Generalize the processing-agent control-plane boundary

**Files:**
- Modify: `apps/processing-agent/src/localizer_agent/control_plane.py`
- Modify: `apps/processing-agent/src/localizer_agent/generated/models.py`
- Test: `apps/processing-agent/tests/test_control_plane_client.py`

**Interfaces:**
- Consumes: supported job types and strict generated `ClaimJobResponse`.
- Produces: `claim(supported_job_types)` plus exact script-approval lookup and completion calls that validate claim-bound version/token/state locally.

- [ ] Add a failing client case proving LOCALIZE/RENDER claims are accepted only with valid `pipelineInput`.
- [ ] Replace the IMPORT-only claim method with a general claim method while retaining a compatibility wrapper for the ingest supervisor.
- [ ] Add the agent-authenticated exact-approval read endpoint/client method without exposing browser-only script mutation APIs.
- [ ] Run focused client checks and inspect serialized requests for secret/text leakage.

### Task 3: Add a state dispatcher before provider orchestration

**Files:**
- Create: `apps/processing-agent/src/localizer_agent/pipeline/supervisor.py`
- Modify: `apps/processing-agent/src/localizer_agent/__main__.py`
- Modify: `apps/processing-agent/src/localizer_agent/config.py`
- Test: `apps/processing-agent/tests/pipeline/test_supervisor.py`

**Interfaces:**
- Consumes: general claims, `LeaseGuard`, `JobWorkspaceManager`, `StageRunner`, and injected stage handlers.
- Produces: exactly one legal completion per claim, or a deliberate review state; unsupported states are never claimed.

- [ ] Add a failing dispatcher case proving each advertised state has exactly one handler and that unhandled states are excluded from claims.
- [ ] Define the supported job-type/state capability map in one production module.
- [ ] Dispatch under the lease guard and assert lease activity immediately before every completion.
- [ ] Keep IMPORT behavior reachable through the same outer runtime loop without duplicating lease ownership.
- [ ] Run focused dispatcher checks and inspect the complete call graph from `__main__` to each handler.

### Task 4: Wire the localization stages with hard gates

**Files:**
- Create: `apps/processing-agent/src/localizer_agent/pipeline/stages.py`
- Modify: `apps/processing-agent/src/localizer_agent/config.py`
- Modify: `apps/processing-agent/src/localizer_agent/__main__.py`
- Modify: `apps/processing-agent/src/localizer_agent/localization/gemini_client.py`
- Modify: `apps/processing-agent/src/localizer_agent/speech/azure_client.py`
- Modify: `apps/processing-agent/src/localizer_agent/preview/registry.py`
- Test: focused pipeline integration checks

**Interfaces:**
- Consumes: state-specific claim inputs and configured provider/tool adapters.
- Produces: immutable transcript, script, voice, subtitle, render, and preview lineage plus legal state transitions.

- [ ] Wire normalized media to PCM extraction and immutable transcript creation.
- [ ] Enforce persisted Google-processing permission before constructing or calling Gemini; persist the canonical script artifact for review.
- [ ] Re-fetch and validate the exact active approval immediately before Azure or render work.
- [ ] Build fitted narration, deterministic subtitles, validated render, and a maximum-five-minute job-bound preview descriptor.
- [ ] Ensure retryable provider failures release or reschedule work without advancing state; timing failures advance only to `TIMING_REVIEW_REQUIRED`.
- [ ] Trace every Phase 5 production module from `__main__`; remove or document any remaining unreachable component.

### Task 5: Code-first completion review

**Files:**
- Modify: `docs/implementation/status.md`
- Create: `docs/implementation/phase-05-report.md`

- [ ] Re-run CodeGraph over the end-to-end production call paths and inspect every state transition, artifact boundary, rights check, and approval check.
- [ ] Run type/lint/generated-model verification, then focused and full suites as secondary regression evidence.
- [ ] Record which production paths are implemented, which external runtimes were actually exercised, and every remaining environment/provider gate.

## Self-Review

- The plan closes the observed unreachable-runtime and missing-claim-input defects before adding provider orchestration.
- Contract fields are introduced only when a downstream production stage consumes them.
- No task treats passing mocks as evidence that OCI, Gemini, Azure, FFmpeg, or native D1 ran successfully.
- Type names and state boundaries are shared through generated contracts rather than duplicated between TypeScript and Python.
