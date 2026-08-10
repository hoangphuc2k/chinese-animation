# Phase 02 — Mandatory Risk-Gate PoCs

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove every external dependency and operational constraint before production architecture depends on it.

**Architecture:** Each PoC is an isolated executable under `pocs/`, uses synthetic or rights-cleared fixtures, writes a redacted JSON result, and maps to a deterministic `PASS`, `FAIL_FALLBACK`, or `BLOCKED` decision. Production modules must not import PoC code.

**Tech Stack:** Bash, Python 3.12/uv, TypeScript/pnpm, Wrangler, OCI CLI, FFmpeg/ffprobe, curl, pytest, Vitest.

## Ownership and execution order

One owner may work in each PoC directory, but the gate runner and report schema
are single-owner files. Execute gates in this order: local fixtures, Access and
lease, manual ingest, OCI benchmark, Azure, Gemini, YouTube, preview delivery,
Facebook. Optional source adapters run only after all mandatory gates.

### Task 1: Create the auditable gate harness

**Files:**
- Create: `pocs/README.md`
- Create: `pocs/schema/gate-result.schema.json`
- Create: `pocs/fixtures/manifest.json`
- Create: `scripts/run-poc-gates.mjs`
- Test: `scripts/run-poc-gates.test.ts`
- Create: `artifacts/pocs/.gitkeep`

- [ ] Write a failing test requiring gate ID, timestamp, environment, fixture
  hash, metrics, redactions, result, reason and fallback.
- [ ] Implement schema validation and reject unknown gate IDs, missing fixture
  hashes, secrets, access tokens and absolute local paths.
- [ ] Add fixture entries for 60-second and 15/30/60-minute synthetic media.
- [ ] Make the runner summarize mandatory failures with a non-zero exit code;
  `FAIL_FALLBACK` passes only when the named fallback matches the design.
- [ ] Run `pnpm vitest run scripts/run-poc-gates.test.ts`; expect all tests pass.

### Task 2: Prove auth, idempotency and atomic lease semantics

**Files:**
- Create: `pocs/access-lease/worker.ts`
- Create: `pocs/access-lease/schema.sql`
- Create: `pocs/access-lease/access-lease.test.ts`
- Create: `pocs/access-lease/run.mjs`

- [ ] First test invalid/expired Access JWTs, missing CSRF proof on browser
  mutations, duplicate idempotency keys and two simultaneous claimants.
- [ ] Verify JWT issuer, audience and signature against a pinned Access JWKS;
  never accept identity headers without JWT verification.
- [ ] Claim through one conditional `UPDATE ... WHERE lease_expires_at < now
  RETURNING`; store only a lease-token hash.
- [ ] Assert exactly one claimant succeeds, replay returns the original result,
  and an expired lease can be reclaimed.
- [ ] Run `pnpm vitest run pocs/access-lease`; expect zero failures and emit
  `artifacts/pocs/access-lease.json`.

### Task 3: Prove both guaranteed ingest paths

**Files:**
- Create: `pocs/manual-ingest/run.py`
- Create: `pocs/manual-ingest/test_run.py`
- Create: `pocs/manual-ingest/README.md`

- [ ] Test HTTPS rejection for HTTP, credentials in URL, loopback/private/link
  local addresses, DNS rebinding, unsafe redirects, oversized bodies and hash
  mismatch.
- [ ] Download a rights-cleared fixture with byte limit, timeout, redirect limit,
  streaming SHA-256 and `ffprobe` validation.
- [ ] Scan only `/mnt/localizer/inbox`, ignore symlinks/partials, stabilize file
  size across two scans and return an opaque source ID rather than a path.
- [ ] Prove Bastion/SCP upload instructions and least-privilege filesystem
  ownership on the OCI test host.
- [ ] Run `uv run pytest pocs/manual-ingest -q`; expect all tests pass and emit
  `artifacts/pocs/manual-ingest.json`.

### Task 4: Benchmark the OCI media envelope

**Files:**
- Create: `pocs/oci-media/benchmark.sh`
- Create: `pocs/oci-media/collect.py`
- Create: `pocs/oci-media/test_collect.py`
- Create: `pocs/oci-media/README.md`

- [ ] Record instance shape/image, tool versions, input duration/codec/size,
  elapsed time, peak RSS, peak disk, output hash and failure stage.
- [ ] Run the exact probe/transcode/subtitle/burn-in/audio-mix workflow for
  15-minute, then 30- and 60-minute fixtures while a shorter tier still passes.
- [ ] A tier passes only when elapsed time is at most 12 hours, peak RSS is below
  10 GB and free disk never falls below 20 GB.
- [ ] Persist the longest passing duration as `max_import_duration_seconds`;
  production import remains disabled if no tier passes.
- [ ] Run `uv run pytest pocs/oci-media -q` and
  `bash pocs/oci-media/benchmark.sh`; inspect the redacted benchmark JSON.

### Task 5: Prove Azure Speech and Gemini contracts

**Files:**
- Create: `pocs/azure-speech/run.py`
- Create: `pocs/azure-speech/test_contract.py`
- Create: `pocs/gemini/run.py`
- Create: `pocs/gemini/test_contract.py`

- [ ] Fetch credentials from OCI Vault through instance principal; verify they
  never appear in process arguments, reports or exception text.
- [ ] Azure: synthesize Vietnamese, validate codec, duration, request ID,
  throttling behavior and retry headers.
- [ ] Gemini: request schema-constrained Vietnamese segments, reject added facts,
  missing IDs, unsafe text and invalid JSON; record model identifier.
- [ ] Test bounded exponential backoff and terminal classification for auth,
  quota, validation and provider outages.
- [ ] Run `uv run pytest pocs/azure-speech pocs/gemini -q`; expect all tests pass
  or a documented design-approved fallback.

### Task 6: Prove YouTube, preview and Facebook boundaries

**Files:**
- Create: `pocs/youtube/run.py`
- Create: `pocs/youtube/test_contract.py`
- Create: `pocs/preview/run.py`
- Create: `pocs/preview/test_delivery.py`
- Create: `pocs/facebook/run.py`
- Create: `pocs/facebook/test_contract.py`

- [ ] YouTube: resumably upload a short fixture as `private`, persist provider
  ID/session state, resume an interrupted upload, and read processing status.
- [ ] Preview: serve via Nginx/cloudflared using an opaque, expiring, job-bound
  token; reject traversal, replay after expiry and cross-job use; verify Range
  requests without exposing filesystem paths.
- [ ] Facebook: first determine whether a hidden/staged upload is supported for
  the target account/API version. If not, record `FAIL_FALLBACK` and prove that
  no upload call occurs before exact render and metadata approval.
- [ ] Revoke test credentials and delete provider test objects after recording
  non-secret IDs.
- [ ] Run `uv run pytest pocs/youtube pocs/preview pocs/facebook -q`.

### Task 7: Record decisions and optional source-adapter outcomes

**Files:**
- Create: `pocs/source-adapters/README.md`
- Create: `docs/implementation/poc-decisions.md`
- Modify: `docs/implementation/status.md`

- [ ] Optionally test Bilibili and Douyin only with authorized content and
  accounts; rate limits, anti-bot or legal uncertainty resolve to “manual ingest
  only,” never credential workarounds.
- [ ] Run `node scripts/run-poc-gates.mjs artifacts/pocs`; expect exit 0.
- [ ] Write measured limits, provider API/model versions, fallbacks, expiry
  dates and owners into `poc-decisions.md`.
- [ ] Mark Phase 02 `PASS` only when every mandatory gate passes or has the
  explicit approved fallback.

## Phase exit criteria

- Every result validates against the gate schema and contains no secret.
- A maximum supported duration is measured and production-safe.
- YouTube private upload and the Facebook post-approval boundary are proven.
- `docs/implementation/status.md` records commands and artifact paths.

