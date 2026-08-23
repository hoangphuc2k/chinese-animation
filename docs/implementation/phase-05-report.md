# Phase 5 — Localization and Preview Pipeline

Status: complete in-repository; provider, OCI media, and native D1 execution remain external runtime gates.

Implemented production path:

- The control plane projects strict, state-specific claims from sanitized D1 lineage and rejects missing or inconsistent artifacts.
- The processing runtime now advertises a continuous capability path: `PROBING` → `EXTRACTING_AUDIO` → `TRANSCRIBING` → `TRANSLATING` → `WAITING_SCRIPT_REVIEW` → `GENERATING_VOICE` → `MIXING` → `RENDERING` → `READY_FOR_PREVIEW` → `WAITING_PUBLISH_APPROVAL`.
- FFmpeg PCM extraction and pinned whisper.cpp transcription create immutable, hash-addressed audio and transcript artifacts.
- Gemini translation is reachable only when persisted rights metadata explicitly permits `GOOGLE_GEMINI`.
- Azure voice generation and FFmpeg rendering each re-fetch an active approval matching the job ID, script version, and canonical script SHA-256 immediately before provider or render work.
- Mixing emits deterministic narration and subtitle lineage. Rendering requires a pinned watermark and validates H.264/AAC streams and audio/video duration drift before completion.
- Preview registration verifies render and subtitle lineage, stores opaque job-bound tokens in a restart-safe local registry, limits expiry to five minutes, and atomically persists the descriptor with the lease-conditioned D1 completion.
- Provider or media exceptions do not advance state. The lease remains authoritative and makes unchanged work claimable again after its bounded expiry; timing failures advance only to `TIMING_REVIEW_REQUIRED`.

Safety properties:

- D1 and agent API payloads contain only opaque artifact references, hashes, byte counts, versions, and public-safe metadata—never OCI absolute paths, source URLs, credentials, rights-evidence text, or translated script bodies.
- Every completion is conditional on lease owner, hashed lease token, unexpired lease, expected state, and exact job version.
- The preview descriptor contains only an opaque token, expiry, and render hash. The OCI-relative file reference remains local to the preview registry.
- Missing source audio terminates at `FAILED`; stale script approval returns work to `WAITING_SCRIPT_REVIEW` without invoking Azure or rendering.

Verification passed:

- `rtk npm run verify`
- `rtk uv run --directory apps/processing-agent pytest -q`
- CodeGraph production-path trace from ingest completion through preview registration

Results: 69 control-plane tests, 19 contract tests, and 82 processing-agent tests passed. Lint, formatting, TypeScript type checks, generated-contract checks, and generated Python compilation also passed.

Remaining external runtime gates:

- Run migrations `0001`–`0006` against native D1 on a supported Workerd host; the current macOS 12 host cannot run the retained Wrangler runtime.
- Exercise the OCI ARM64 deployment with pinned FFmpeg/ffprobe and whisper.cpp binaries plus the configured model and watermark checksums.
- Run credentialed Gemini and Azure Speech integration checks using explicitly authorized non-production fixtures.
- Validate the Cloudflare Access and Nginx preview authorization subrequest end to end; the repository currently provides the opaque registry and internal Nginx location, not a claim that the external delivery path was exercised.
