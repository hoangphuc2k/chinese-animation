# Phase 04 — Processing Agent and Guaranteed Ingest

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run a recoverable OCI agent that safely claims jobs and ingests both guaranteed source types through media validation.

**Architecture:** A Python supervisor polls the Worker, claims one stage at a time, heartbeats with a lease token, and executes resumable stage handlers. Files live in job-scoped directories on OCI block storage; a manifest is the only mapping from opaque asset IDs to paths.

**Tech Stack:** Python 3.12+, uv, Pydantic, httpx, pytest, FFmpeg/ffprobe, OCI SDK/Vault, systemd.

## Exclusive file ownership

Phase 04 owns `apps/processing-agent/agent`, `tests`, `deploy/systemd`, and
ingest-specific operational docs. It consumes generated models and must not
edit Worker state machines, D1 migrations or media-localization stages.

### Task 1: Bootstrap configuration and safe logging

**Files:**
- Create: `apps/processing-agent/pyproject.toml`
- Create: `apps/processing-agent/agent/config.py`
- Create: `apps/processing-agent/agent/logging.py`
- Create: `apps/processing-agent/agent/main.py`
- Test: `apps/processing-agent/tests/test_config.py`
- Test: `apps/processing-agent/tests/test_logging.py`

- [ ] Test required control-plane URL, agent ID, inbox/work roots, disk floor,
  timeouts and concurrency default of one.
- [ ] Reject roots outside configured absolute prefixes and production HTTP
  endpoints.
- [ ] Load provider/service credentials lazily from OCI Vault via instance
  principal; never accept secrets as CLI flags.
- [ ] Redact authorization, lease, preview, query-token and provider credential
  values from structured logs and exceptions.
- [ ] Run `uv run --project apps/processing-agent pytest -q tests/test_config.py tests/test_logging.py`.

### Task 2: Implement control-plane client and lease lifecycle

**Files:**
- Create: `apps/processing-agent/agent/control_plane.py`
- Create: `apps/processing-agent/agent/lease.py`
- Test: `apps/processing-agent/tests/test_control_plane.py`
- Test: `apps/processing-agent/tests/test_lease.py`

- [ ] Test empty queue, successful claim, heartbeat, cancellation, duplicate
  result, expired lease, version conflict, transient network and auth failure.
- [ ] Use generated request/response models and a bounded timeout for every call.
- [ ] Keep the raw lease token in memory only; never serialize it into manifest,
  disk checkpoints or logs.
- [ ] Heartbeat before half the lease duration and stop stage work if renewal
  fails beyond the safe window.
- [ ] Retry only classified transient errors with jitter and server
  `Retry-After`; never retry schema/auth failures indefinitely.
- [ ] Run `uv run --project apps/processing-agent pytest -q tests/test_control_plane.py tests/test_lease.py`.

### Task 3: Create job workspace, manifest and recovery

**Files:**
- Create: `apps/processing-agent/agent/workspace.py`
- Create: `apps/processing-agent/agent/manifest.py`
- Create: `apps/processing-agent/agent/recovery.py`
- Test: `apps/processing-agent/tests/test_workspace.py`
- Test: `apps/processing-agent/tests/test_recovery.py`

- [ ] Test opaque job/asset mapping, traversal attempts, symlinks, atomic
  manifest writes, checksum mismatch, process restart and low-disk refusal.
- [ ] Create `work/<job-id>/<stage>/<attempt-id>` with restrictive permissions;
  use atomic rename only after checksum and fsync.
- [ ] Persist non-secret stage inputs, output hashes and completion markers so a
  restart can report completed work or safely repeat an idempotent stage.
- [ ] Quarantine corrupt/incomplete artifacts rather than accepting them.
- [ ] Run focused workspace/recovery tests.

### Task 4: Implement hardened creator HTTPS ingest

**Files:**
- Create: `apps/processing-agent/agent/ingest/https_source.py`
- Create: `apps/processing-agent/agent/security/network_policy.py`
- Test: `apps/processing-agent/tests/ingest/test_https_source.py`

- [ ] Start with tests for scheme/userinfo/port restrictions, IPv4/IPv6 private
  ranges, DNS changes, redirects, content length, streaming limit, timeout,
  MIME mismatch and expected SHA-256.
- [ ] Resolve and validate every redirect target; connect only to validated
  public addresses and fail if the peer address differs from the approved set.
- [ ] Stream to a partial file while hashing; never buffer full media in memory.
- [ ] Require recorded rights basis and reject duration above the PoC maximum
  before promoting the asset.
- [ ] Run `uv run --project apps/processing-agent pytest -q tests/ingest/test_https_source.py`.

### Task 5: Implement OCI inbox ingest

**Files:**
- Create: `apps/processing-agent/agent/ingest/inbox.py`
- Create: `apps/processing-agent/scripts/register_inbox_file.py`
- Test: `apps/processing-agent/tests/ingest/test_inbox.py`

- [ ] Test containment, regular-file requirement, allowed extensions, stable
  size, minimum age, symlink/hardlink rejection, duplicate hash and concurrent
  scan behavior.
- [ ] Scan only the configured inbox; registration accepts a filename, not an
  arbitrary path, and returns an opaque source ID plus SHA-256/size.
- [ ] Move accepted files atomically to the job workspace on the same volume;
  copy+verify+fsync when filesystems differ.
- [ ] Leave rejected files untouched and emit a reason without leaking paths to
  the frontend.
- [ ] Run the focused inbox tests.

### Task 6: Probe, normalize and report source metadata

**Files:**
- Create: `apps/processing-agent/agent/media/probe.py`
- Create: `apps/processing-agent/agent/media/normalize.py`
- Test: `apps/processing-agent/tests/media/test_probe.py`
- Test: `apps/processing-agent/tests/media/test_normalize.py`

- [ ] Test malformed media, missing audio/video, excessive duration, unsupported
  codecs, variable frame rate, rotation and dangerous metadata.
- [ ] Run `ffprobe` without shell interpolation, with time/resource limits, and
  parse only expected JSON fields.
- [ ] Normalize to the benchmarked mezzanine profile, preserve aspect ratio and
  record exact FFmpeg command/version and output hash.
- [ ] Report opaque asset ID, technical metadata and hash through the lease API.
- [ ] Run focused tests using tiny generated fixtures.

### Task 7: Package and exercise the OCI service

**Files:**
- Create: `apps/processing-agent/deploy/systemd/localizer-agent.service`
- Create: `apps/processing-agent/deploy/systemd/localizer-agent.env.example`
- Create: `apps/processing-agent/docs/oci-install.md`
- Create: `apps/processing-agent/tests/integration/test_ingest_flow.py`
- Modify: `docs/implementation/status.md`

- [ ] Configure a non-root service user, read-only application files, writable
  inbox/work roots, restart backoff and explicit resource limits.
- [ ] Document OCI Vault policy, instance-principal dynamic group, Bastion/SCP
  upload, volume permissions and log rotation without secret examples.
- [ ] Run integration tests against a local Worker for both ingest sources,
  cancellation, restart and duplicate delivery.
- [ ] Run `uv run --project apps/processing-agent ruff check .`,
  `uv run --project apps/processing-agent pyright`, and
  `uv run --project apps/processing-agent pytest -q`.

## Phase exit criteria

- HTTPS and OCI inbox sources reach validated normalized media.
- SSRF, traversal, symlink, oversized-media, low-disk and restart tests pass.
- No raw lease token or secret persists to disk/logs.
- Phase 04 status records the tested OCI image and FFmpeg version.

