# Processing Agent and Guaranteed Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a recoverable OCI processing agent that claims only `IMPORT`
jobs, ingests guaranteed HTTPS and OCI inbox sources safely, normalizes media,
and reports metadata without ever moving media bytes through the Worker.

**Architecture:** Extend the authenticated control-plane agent protocol with an
import-only claim payload and typed ingest result. The Python supervisor in
`src/localizer_agent` keeps the lease token only in memory, writes non-secret
job manifests under a restrictive workspace, invokes hardened source adapters,
then reports opaque IDs, hashes, byte counts, and sanitized technical metadata.

**Tech Stack:** Python 3.12+, uv, Pydantic, httpx, pytest, Ruff, Pyright,
FFmpeg/ffprobe, Cloudflare Workers/D1, OCI instance principal and Vault.

## Global Constraints

- All Python production code remains under `apps/processing-agent/src/localizer_agent`; use four-space indentation and typed public boundaries.
- Every new behaviour follows red, green, refactor: run the focused test and observe its expected failure before its implementation.
- The Worker stores coordination metadata only. It never receives media bytes, filesystem paths, raw lease tokens, credentials, or unredacted source URLs in audit/event details.
- The Phase 04 agent sends `supportedJobTypes: ["IMPORT"]` on every claim and must not process other job types.
- The raw lease token stays only in the live lease object; it is excluded from manifest, checkpoint, exceptions, and structured logs.
- HTTP calls use bounded timeouts, retry only classified transient failures, respect `Retry-After`, and stop processing if the safe heartbeat window is lost.
- Files are created below configured absolute inbox/work roots, with no arbitrary user-supplied paths, shell interpolation, symlink following, or whole-media buffering.
- Do not commit credentials, fixtures containing creator media, OCI identifiers, or absolute host paths. Do not commit while this initial repository worktree contains unrelated staged user changes.
- Prefix all repository shell commands with `rtk`.

---

## File Structure

- `packages/contracts/src/jobs.ts` — typed agent claim/complete protocol.
- `packages/contracts/scripts/generate.ts` — deterministic Python models for the agent protocol.
- `apps/control-plane/db/migrations/0004_ingest_results.sql` — source ingest result metadata, separate from locator metadata.
- `apps/control-plane/src/services/lease-service.ts` — `IMPORT`-filtered claim and atomic ingest-result completion.
- `apps/control-plane/src/api/agent-routes.ts` — validated supported-job-type claim body and protocol response.
- `apps/processing-agent/src/localizer_agent/config.py` — immutable runtime configuration and safe root validation.
- `apps/processing-agent/src/localizer_agent/logging.py` — redacted JSON log records.
- `apps/processing-agent/src/localizer_agent/control_plane.py` — bounded typed HTTP client and provider error classification.
- `apps/processing-agent/src/localizer_agent/lease.py` — in-memory lease lifecycle and heartbeat guard.
- `apps/processing-agent/src/localizer_agent/workspace.py`, `manifest.py`, `recovery.py` — durable restrictive workspace and restart recovery.
- `apps/processing-agent/src/localizer_agent/ingest/https_source.py` and `inbox.py` — hardened source acquisition.
- `apps/processing-agent/src/localizer_agent/security/network_policy.py` — URL, DNS, and peer-address validation.
- `apps/processing-agent/src/localizer_agent/media/probe.py` and `normalize.py` — bounded FFprobe/FFmpeg wrappers.
- `apps/processing-agent/src/localizer_agent/main.py` — single-concurrency supervisor and stage dispatch.
- `apps/processing-agent/deploy/systemd/*` and `apps/processing-agent/docs/oci-install.md` — non-root OCI operation.

## Task 1: Extend the import agent protocol and persist ingest results

**Files:**
- Modify: `packages/contracts/src/jobs.ts`
- Modify: `packages/contracts/src/contracts.test.ts`
- Modify: `packages/contracts/scripts/generate.ts`
- Modify: `apps/processing-agent/src/localizer_agent/generated/models.py`
- Create: `apps/control-plane/db/migrations/0004_ingest_results.sql`
- Modify: `apps/control-plane/src/services/lease-service.ts`
- Modify: `apps/control-plane/src/api/agent-routes.ts`
- Modify: `apps/control-plane/test/agent-api.test.ts`
- Modify: `apps/control-plane/test/lease-concurrency.test.ts`
- Modify: `apps/control-plane/test/migrations.test.ts`

**Consumes:** Existing `IngestLocator`, `JobType`, `ProcessingState`, leases,
source `metadata_json`, and hashed `rights_metadata_json` from Phase 03.

**Produces:**

```typescript
type ClaimJobRequest = { supportedJobTypes: ["IMPORT", ...string[]] };
type ImportInput = {
  sourceAssetId: string;
  locator: z.infer<typeof IngestLocator>;
  rightsEvidenceHash: string;
};
type IngestResult = {
  sourceHash: string;
  sourceByteCount: number;
  normalizedHash: string;
  normalizedByteCount: number;
  technicalMetadata: { durationSeconds: number; hasAudio: boolean; hasVideo: boolean; formatName: string };
};
```

- [ ] **Step 1: Write the failing contract and Worker tests**

```typescript
it("returns only IMPORT work with a typed locator and rights hash", async () => {
  const response = await app.request("/api/v1/agent/jobs/claim", {
    method: "POST",
    body: JSON.stringify({ supportedJobTypes: ["IMPORT"] }),
  }, env);
  expect(await response.json()).toMatchObject({
    jobType: "IMPORT",
    importInput: { locator: { kind: "OCI_INBOX_FILE", inboxFileId: "inbox-01" }, rightsEvidenceHash: expect.stringMatching(/^[a-f0-9]{64}$/) },
  });
});

it("persists an ingest result without storing its locator in events", async () => {
  await service.complete({ /* valid lease */ ingestResult, nextState: "PROBING" });
  expect(ingestResultRow.normalized_hash).toBe(ingestResult.normalizedHash);
  expect(event.details_json).not.toContain("https://");
});
```

- [ ] **Step 2: Run the focused tests and confirm they fail because the request and result fields do not exist**

Run: `rtk npm run test --workspace=@localizer/control-plane -- agent-api lease-concurrency migrations`

Expected: FAIL with missing claim body validation, missing `importInput`, or missing ingest-result storage.

- [ ] **Step 3: Add the minimal protocol and storage implementation**

```typescript
export const ClaimJobRequest = z.object({ supportedJobTypes: z.array(JobType).min(1).max(6) }).strict();
export const ImportInput = z.object({ sourceAssetId: JobId, locator: IngestLocator, rightsEvidenceHash: Sha256 }).strict();
export const IngestResult = z.object({
  sourceHash: Sha256, sourceByteCount: z.number().int().nonnegative().safe(),
  normalizedHash: Sha256, normalizedByteCount: z.number().int().nonnegative().safe(),
  technicalMetadata: z.object({ durationSeconds: z.number().nonnegative().finite(), hasAudio: z.boolean(), hasVideo: z.boolean(), formatName: z.string().min(1).max(80) }).strict(),
}).strict();
```

Create an `ingest_results` table keyed by `source_asset_id`; include hashes,
byte counts, JSON technical metadata, and timestamps. Claim SQL must join
`source_assets`, filter `jobs.job_type IN (...)`, exclude a revoked source,
parse only the stored locator/evidence hash, and return no source metadata for
non-`IMPORT` jobs. Completion must condition on lease token, job version, and
expected state before updating job state and upserting the ingest result in the
same D1 batch. Generate matching strict Pydantic claim/request models.

- [ ] **Step 4: Run focused protocol verification**

Run: `rtk npm run test --workspace=@localizer/control-plane -- agent-api lease-concurrency migrations && rtk npm run contracts:check && rtk python3 -m py_compile apps/processing-agent/src/localizer_agent/generated/models.py`

Expected: PASS; a non-IMPORT job is not leased by the import agent, event/audit JSON has no locator, and generated models are current.

- [ ] **Step 5: Record a review checkpoint**

Review migration monotonicity, atomic completion, and absence of raw evidence/URL disclosure before starting Python runtime work. Do not create a commit in the current mixed staged worktree.

## Task 2: Bootstrap strict agent configuration and safe structured logging

**Files:**
- Modify: `apps/processing-agent/pyproject.toml`
- Create: `apps/processing-agent/src/localizer_agent/config.py`
- Create: `apps/processing-agent/src/localizer_agent/logging.py`
- Create: `apps/processing-agent/tests/test_config.py`
- Create: `apps/processing-agent/tests/test_logging.py`

**Consumes:** The package root and `IMPORT`-only scope from Task 1.

**Produces:**

```python
@dataclass(frozen=True)
class AgentConfig:
    control_plane_url: str
    agent_id: str
    inbox_root: Path
    work_root: Path
    allowed_roots: tuple[Path, ...]
    disk_floor_bytes: int
    request_timeout_seconds: float
    concurrency: int = 1

def load_config(environ: Mapping[str, str]) -> AgentConfig: ...
def redact_log_value(value: object, key: str | None = None) -> object: ...
```

- [ ] **Step 1: Write failing configuration and logging tests**

```python
def test_load_config_rejects_http_endpoint_and_escape_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        load_config({**valid_env(tmp_path), "CONTROL_PLANE_URL": "http://worker.test"})
    with pytest.raises(ValueError, match="allowed roots"):
        load_config({**valid_env(tmp_path), "WORK_ROOT": "/tmp/outside"})

def test_log_redaction_removes_tokens_from_nested_values() -> None:
    assert redact_log_value({"authorization": "Bearer secret", "url": "https://x/?token=secret"}) == {
        "authorization": "[REDACTED]", "url": "[REDACTED]"
    }
```

- [ ] **Step 2: Run the focused tests and observe the missing-module failure**

Run: `rtk uv run --project apps/processing-agent pytest -q tests/test_config.py tests/test_logging.py`

Expected: FAIL because `localizer_agent.config` and `localizer_agent.logging` do not exist.

- [ ] **Step 3: Implement immutable configuration and JSON log redaction**

Add `httpx`, `pytest-asyncio`, `ruff`, and `pyright` configuration to `pyproject.toml`.
Require absolute, resolved, non-symlink inbox/work roots beneath configured
allowed roots; require HTTPS outside explicit local test configuration; reject
zero/negative disk floor and concurrency other than one. Emit JSON objects with
request/job/project/stage/duration/error-code fields. Redact case-insensitive
authorization, lease, preview, cookie, query-token, secret, credential, and
URL fields recursively; do not include exception text until sanitized.

- [ ] **Step 4: Run focused verification**

Run: `rtk uv run --project apps/processing-agent pytest -q tests/test_config.py tests/test_logging.py && rtk uv run --project apps/processing-agent ruff check src tests`

Expected: PASS with no secret-bearing values in captured JSON.

- [ ] **Step 5: Record a review checkpoint**

Review every environment variable name and log field for secret or path
leakage. Keep OCI Vault access lazy; no Vault call occurs during configuration
parsing or tests.

## Task 3: Implement typed control-plane client and in-memory lease guard

**Files:**
- Create: `apps/processing-agent/src/localizer_agent/control_plane.py`
- Create: `apps/processing-agent/src/localizer_agent/lease.py`
- Create: `apps/processing-agent/tests/test_control_plane.py`
- Create: `apps/processing-agent/tests/test_lease.py`

**Consumes:** Task 1 generated models and Task 2 `AgentConfig`/redacted logger.

**Produces:**

```python
class ControlPlaneClient:
    async def claim_import(self) -> ClaimedImport | None: ...
    async def heartbeat(self, lease: ActiveLease) -> ActiveLease: ...
    async def complete_ingest(self, lease: ActiveLease, result: IngestResult) -> None: ...

@dataclass
class ActiveLease:
    job_id: str
    job_version: int
    expires_at: datetime
    _token: str

class LeaseGuard:
    async def run(self, lease: ActiveLease, stage: Awaitable[T]) -> T: ...
```

- [ ] **Step 1: Write failing HTTP and lease lifecycle tests**

```python
async def test_claim_import_sends_supported_type_and_parses_locator(httpx_mock) -> None:
    httpx_mock.add_response(json=claim_import_payload())
    claim = await client.claim_import()
    assert claim.locator.kind == "OCI_INBOX_FILE"
    assert "leaseToken" not in client.log_records[0]

async def test_lease_guard_stops_stage_after_safe_window_loss() -> None:
    with pytest.raises(LeaseLostError):
        await guard.run(expiring_lease(), never_finishing_stage())
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `rtk uv run --project apps/processing-agent pytest -q tests/test_control_plane.py tests/test_lease.py`

Expected: FAIL because the client and lease modules do not exist.

- [ ] **Step 3: Implement bounded requests and heartbeat policy**

Use one injected `httpx.AsyncClient` with connect/read/write/pool timeouts from
`AgentConfig`. Send the authenticated agent credential through a header only;
never persist it. Map 429/5xx/network timeout to a bounded retry class, parse
both `Retry-After` forms, and map 400/401/403/schema/version/lease responses to
terminal classes. Schedule heartbeat before half of remaining lease time and
cancel the supplied stage task when renewal cannot finish before the safe
window. Keep `_token` private and omit it from dataclass representation.

- [ ] **Step 4: Run focused verification**

Run: `rtk uv run --project apps/processing-agent pytest -q tests/test_control_plane.py tests/test_lease.py`

Expected: PASS for empty queue, claim, heartbeat, duplicate completion,
expired lease, version conflict, transient retry, auth failure, and token
non-persistence.

- [ ] **Step 5: Record a review checkpoint**

Verify every request has a timeout and every retry path has a bounded attempt
count and no raw-token logging.

## Task 4: Create restrictive workspace, manifest, and restart recovery

**Files:**
- Create: `apps/processing-agent/src/localizer_agent/workspace.py`
- Create: `apps/processing-agent/src/localizer_agent/manifest.py`
- Create: `apps/processing-agent/src/localizer_agent/recovery.py`
- Create: `apps/processing-agent/tests/test_workspace.py`
- Create: `apps/processing-agent/tests/test_recovery.py`

**Consumes:** Task 2 root policy and Task 3 `ActiveLease.job_id` only; no lease
token crosses this boundary.

**Produces:**

```python
@dataclass(frozen=True)
class AttemptWorkspace:
    root: Path
    partial_path: Path
    output_path: Path

def create_attempt(work_root: Path, job_id: str, stage: str, attempt_id: str) -> AttemptWorkspace: ...
def require_disk_floor(root: Path, minimum_free_bytes: int) -> None: ...
def write_manifest_atomic(path: Path, manifest: StageManifest) -> None: ...
def recover_attempts(work_root: Path) -> list[RecoveryAction]: ...
```

- [ ] **Step 1: Write failing workspace/recovery tests**

```python
def test_workspace_rejects_traversal_and_symlinked_stage(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        create_attempt(tmp_path, "job-1", "../escape", "attempt-1")

def test_manifest_promotion_requires_fsync_and_matching_hash(tmp_path: Path) -> None:
    assert recover_attempts(tmp_path) == [RecoveryAction.QUARANTINE]
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `rtk uv run --project apps/processing-agent pytest -q tests/test_workspace.py tests/test_recovery.py`

Expected: FAIL because workspace, manifest, and recovery modules do not exist.

- [ ] **Step 3: Implement safe directories and atomic manifest handling**

Validate opaque IDs against `[A-Za-z0-9_-]+`, resolve every constructed path
under `work_root`, create directories with mode `0o700`, and refuse symlinks.
Check free space before acquisition, copy, and normalization; reject attempts
that would cross the configured disk floor.
Write a temporary JSON manifest, flush/fsync file and directory, then replace
atomically. The manifest includes input locator kind (not URL), hashes, byte
counts, and completion markers; it excludes tokens, credentials, and raw paths
outside the workspace. Quarantine partial/corrupt attempts under a controlled
`quarantine` directory after checksum mismatch or malformed JSON.

- [ ] **Step 4: Run focused verification**

Run: `rtk uv run --project apps/processing-agent pytest -q tests/test_workspace.py tests/test_recovery.py`

Expected: PASS for traversal, symlink, atomic write, restart, checksum mismatch,
and low-disk refusal paths.

- [ ] **Step 5: Record a review checkpoint**

Inspect generated manifests and quarantine names to confirm no raw lease token,
credential, arbitrary input path, or source URL is serialized.

## Task 5: Implement hardened creator HTTPS ingest

**Files:**
- Create: `apps/processing-agent/src/localizer_agent/security/network_policy.py`
- Create: `apps/processing-agent/src/localizer_agent/ingest/__init__.py`
- Create: `apps/processing-agent/src/localizer_agent/ingest/https_source.py`
- Create: `apps/processing-agent/tests/ingest/test_https_source.py`

**Consumes:** Task 2 limits/logging, Task 3 lease cancellation, and Task 4
partial-file workspace.

**Produces:**

```python
@dataclass(frozen=True)
class DownloadedSource:
    path: Path
    sha256: str
    byte_count: int
    content_type: str

async def download_https_source(locator: HttpsLocator, workspace: AttemptWorkspace, policy: NetworkPolicy) -> DownloadedSource: ...
```

- [ ] **Step 1: Write failing hostile-network tests**

```python
@pytest.mark.parametrize("url", ["http://host/a", "https://user@host/a", "https://host:8443/a"])
async def test_download_rejects_disallowed_url_forms(url: str) -> None: ...

async def test_download_rejects_private_redirect_and_peer_rebinding(fake_dns, fake_transport) -> None: ...
async def test_download_streams_with_limit_and_hash(tmp_path: Path, fake_transport) -> None: ...
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `rtk uv run --project apps/processing-agent pytest -q tests/ingest/test_https_source.py`

Expected: FAIL because network policy and HTTPS source adapter do not exist.

- [ ] **Step 3: Implement redirect-safe streaming**

Allow HTTPS only on port 443 with no userinfo. Resolve every target before each
connection and reject loopback, link-local, multicast, unspecified, private,
reserved, and documentation IPv4/IPv6 networks. Disable automatic redirects;
validate and limit each `Location` target. Require the connected peer address
to remain in the validated address set. Stream chunks into `partial_path`,
enforce content-length and actual-byte limits, hash incrementally, validate
allowed media MIME types, and atomically promote only after the stream hash is
recorded and a non-empty recorded-rights hash is present.

- [ ] **Step 4: Run focused verification**

Run: `rtk uv run --project apps/processing-agent pytest -q tests/ingest/test_https_source.py`

Expected: PASS for scheme/userinfo/port, private IPv4/IPv6, redirect, rebinding,
size, timeout, MIME, stream hashing, rights, and checksum cases.

- [ ] **Step 5: Record a review checkpoint**

Verify all tests use a fake resolver/transport and no test makes an external
network request or logs the full source URL.

## Task 6: Implement OCI inbox ingest and registration

**Files:**
- Create: `apps/processing-agent/src/localizer_agent/ingest/inbox.py`
- Create: `apps/processing-agent/scripts/register_inbox_file.py`
- Create: `apps/processing-agent/tests/ingest/test_inbox.py`

**Consumes:** Task 2 root rules and Task 4 promotion/manifest primitives.

**Produces:**

```python
@dataclass(frozen=True)
class RegisteredInboxFile:
    inbox_file_id: str
    sha256: str
    byte_count: int

def register_inbox_file(inbox_root: Path, filename: str) -> RegisteredInboxFile: ...
def acquire_inbox_file(inbox_root: Path, inbox_file_id: str, workspace: AttemptWorkspace) -> DownloadedSource: ...
```

- [ ] **Step 1: Write failing inbox containment tests**

```python
def test_registration_accepts_filename_not_path_and_rejects_links(tmp_path: Path) -> None: ...
def test_acquisition_requires_stable_old_regular_file_and_deduplicates_hash(tmp_path: Path) -> None: ...
def test_concurrent_scans_leave_rejected_file_untouched(tmp_path: Path) -> None: ...
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `rtk uv run --project apps/processing-agent pytest -q tests/ingest/test_inbox.py`

Expected: FAIL because inbox registration and acquisition modules do not exist.

- [ ] **Step 3: Implement opaque registration and safe acquisition**

Accept a basename matching the configured file-ID grammar; resolve it beneath
the inbox root and use `lstat` to reject symlink/hardlink/non-regular files.
Require an allowed extension, a minimum age, and stable size across two stats.
Compute hash once and record an opaque ID/hashes in the local registry without
returning the local path. On acquisition, atomically rename only on the same
device; otherwise copy to partial, fsync, hash-verify, then promote. Keep
rejected files in the inbox and return sanitized reason codes.

- [ ] **Step 4: Run focused verification**

Run: `rtk uv run --project apps/processing-agent pytest -q tests/ingest/test_inbox.py`

Expected: PASS for traversal, regular-file, extension, age, stability, link,
duplicate, concurrent-scan, move, and cross-device copy-verification cases.

- [ ] **Step 5: Record a review checkpoint**

Inspect CLI help and error output to ensure neither reveals absolute source
paths to the frontend/control-plane response.

## Task 7: Probe and normalize media without a shell

**Files:**
- Create: `apps/processing-agent/src/localizer_agent/media/__init__.py`
- Create: `apps/processing-agent/src/localizer_agent/media/probe.py`
- Create: `apps/processing-agent/src/localizer_agent/media/normalize.py`
- Create: `apps/processing-agent/tests/media/test_probe.py`
- Create: `apps/processing-agent/tests/media/test_normalize.py`

**Consumes:** Task 4 workspace and acquired source files from Tasks 5–6.

**Produces:**

```python
@dataclass(frozen=True)
class TechnicalMetadata:
    duration_seconds: float
    has_audio: bool
    has_video: bool
    format_name: str

def probe_media(path: Path, timeout_seconds: float) -> TechnicalMetadata: ...
def normalize_media(source: Path, workspace: AttemptWorkspace, timeout_seconds: float) -> NormalizedMedia: ...
```

- [ ] **Step 1: Write failing subprocess-boundary tests**

```python
def test_probe_rejects_missing_streams_duration_and_dangerous_metadata(fake_runner) -> None: ...
def test_normalize_uses_argument_vector_and_reports_hash(fake_runner, tmp_path: Path) -> None: ...
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `rtk uv run --project apps/processing-agent pytest -q tests/media/test_probe.py tests/media/test_normalize.py`

Expected: FAIL because media modules do not exist.

- [ ] **Step 3: Implement bounded FFprobe and FFmpeg wrappers**

Invoke `subprocess.run` with an argument list, `shell=False`, timeout, fixed
environment, and captured bounded output. Parse only expected FFprobe JSON;
require audio/video, finite duration beneath configuration limit, supported
codecs, safe rotation, and no dangerous metadata. Normalize to the documented
mezzanine profile while preserving aspect ratio; record sanitized command
arguments, FFmpeg version, technical metadata, byte count, and SHA-256 in the
returned result. Do not execute a shell or accept caller-supplied flags.

- [ ] **Step 4: Run focused verification**

Run: `rtk uv run --project apps/processing-agent pytest -q tests/media/test_probe.py tests/media/test_normalize.py`

Expected: PASS for malformed JSON, absent streams, duration/codecs/rotation,
argument-vector construction, output hash, and timeout behaviour.

- [ ] **Step 5: Record a review checkpoint**

Verify no media command contains a shell string, source URL, credential, or
unbounded output capture.

## Task 8: Compose the single-concurrency ingest supervisor

**Files:**
- Create: `apps/processing-agent/src/localizer_agent/main.py`
- Create: `apps/processing-agent/tests/integration/test_ingest_flow.py`

**Consumes:** Tasks 1–7.

**Produces:**

```python
async def run_once(config: AgentConfig, client: ControlPlaneClient) -> bool: ...
async def process_import(claim: ClaimedImport, dependencies: IngestDependencies) -> IngestResult: ...
```

- [ ] **Step 1: Write failing end-to-end orchestration tests**

```python
async def test_run_once_completes_https_and_inbox_imports(fake_client, dependencies) -> None: ...
async def test_run_once_stops_on_cancellation_or_restart_and_never_serializes_token(fake_client, dependencies) -> None: ...
```

- [ ] **Step 2: Run integration tests and verify failure**

Run: `rtk uv run --project apps/processing-agent pytest -q tests/integration/test_ingest_flow.py`

Expected: FAIL because the supervisor dispatch does not exist.

- [ ] **Step 3: Implement `IMPORT`-only stage dispatch**

Claim a single `IMPORT` job, create its workspace, select only the typed HTTPS
or inbox adapter, enforce disk floor before acquisition and normalization,
probe/normalize the result, persist a non-secret completion manifest, then call
typed completion through `LeaseGuard`. Recover an already-promoted manifest by
reporting the same idempotent result. Treat cancellation, revoked rights, lease
loss, and terminal policy errors as stop conditions; retry only the client’s
classified transient errors.

- [ ] **Step 4: Run integration verification**

Run: `rtk uv run --project apps/processing-agent pytest -q tests/integration/test_ingest_flow.py`

Expected: PASS for both source kinds, cancellation, restart, duplicate delivery,
low disk, and token non-persistence.

- [ ] **Step 5: Record a review checkpoint**

Check stage order and all completion payloads against Task 1 contract; confirm
the supervisor never sends a local path or raw locator in result/event fields.

## Task 9: Package OCI operation, status evidence, and full verification

**Files:**
- Create: `apps/processing-agent/deploy/systemd/localizer-agent.service`
- Create: `apps/processing-agent/deploy/systemd/localizer-agent.env.example`
- Create: `apps/processing-agent/docs/oci-install.md`
- Create: `apps/processing-agent/tests/test_deployment.py`
- Modify: `docs/implementation/status.md`
- Create: `docs/implementation/phase-04-report.md`

**Consumes:** Complete Task 8 runtime and all test suites.

- [ ] **Step 1: Write deployment-document assertions and status fixture checks**

```python
def test_systemd_unit_uses_non_root_user_and_explicit_writable_roots() -> None: ...
def test_install_guide_has_no_secret_values_or_creator_source_paths() -> None: ...
```

- [ ] **Step 2: Run the deployment checks and verify failure**

Run: `rtk uv run --project apps/processing-agent pytest -q tests/test_deployment.py`

Expected: FAIL because OCI deployment artifacts and their validation do not exist.

- [ ] **Step 3: Implement least-privilege deployment artifacts**

Create a systemd service with a dedicated non-root user, read-only application
tree, writable inbox/work roots only, `Restart=on-failure`, restart backoff,
memory/CPU/file limits, and no inline secret. Document OCI Vault policy and
instance-principal dynamic group, Bastion/SCP upload, volume permissions, log
rotation, and the exact verification commands. Add a Phase 04 report template
that records real OCI image digest and FFmpeg version only after the target host
runs the integration flow; keep status `BLOCKED (external OCI runtime)` until
that evidence exists.

- [ ] **Step 4: Run all Phase 04 and repository verification**

Run: `rtk uv run --project apps/processing-agent ruff check . && rtk uv run --project apps/processing-agent pyright && rtk uv run --project apps/processing-agent pytest -q && rtk npm run verify`

Expected: PASS for local static/unit/integration checks. The OCI image/FFmpeg
evidence command remains an explicit target-host gate and must not be fabricated.

- [ ] **Step 5: Final review checkpoint**

Compare the completed implementation against `2026-08-07-processing-agent-ingest-design.md`. Confirm the no-media-through-Worker, no-token-persistence,
SSRF, path-containment, recovery, and OCI-evidence constraints before proposing
Phase 04 status changes.
