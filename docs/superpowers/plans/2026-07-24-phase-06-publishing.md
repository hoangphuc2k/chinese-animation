# Phase 06 — Publishing Integrations

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish an exact approved render and metadata version to YouTube and Facebook without duplicate uploads or premature visibility.

**Architecture:** Destination adapters share a strict interface but maintain independent state machines. The control plane authorizes a version-bound publication; the OCI agent reads Vault credentials and performs resumable provider operations.

**Tech Stack:** Python, Google/YouTube Data API, Meta Graph API, OCI Vault, pytest, Worker integration tests.

## Exclusive file ownership

Phase 06 owns `agent/publishing`, destination integration tests and publishing
runbooks. Worker changes are limited to additive provider fields required by an
approved contract change. UI files remain untouched.

### Task 1: Define provider-neutral publication commands

**Files:**
- Create: `apps/processing-agent/agent/publishing/base.py`
- Create: `apps/processing-agent/agent/publishing/models.py`
- Create: `apps/processing-agent/agent/publishing/metadata.py`
- Test: `apps/processing-agent/tests/publishing/test_metadata.py`

- [ ] Test required title/description, platform limits, Unicode, disallowed
  control characters, destination identity and immutable metadata hash.
- [ ] Require publication ID, destination, render ID/hash, metadata version/hash
  and approval ID in every command.
- [ ] Re-fetch and verify all bound values immediately before reading media.
- [ ] Derive stable provider idempotency correlation without placing secrets in
  metadata.
- [ ] Run focused publishing model tests.

### Task 2: Implement resumable private YouTube upload

**Files:**
- Create: `apps/processing-agent/agent/publishing/youtube.py`
- Create: `apps/processing-agent/agent/publishing/youtube_auth.py`
- Test: `apps/processing-agent/tests/publishing/test_youtube.py`

- [ ] Test session creation/resume, chunk retry, expired session, duplicate
  completion, quota/auth errors and provider processing failure.
- [ ] Load refresh credential from Vault; store only provider asset ID, resumable
  session reference if safe, offset and non-secret error data in checkpoints.
- [ ] Create every upload as `private`; setting scheduled/public visibility is
  outside the MVP and must not exist in code.
- [ ] Poll processing with bounded backoff and report a review-ready Studio link
  derived from validated provider ID.
- [ ] Run focused YouTube tests against fakes, then the PoC account smoke test.

### Task 3: Implement Facebook’s approved boundary

**Files:**
- Create: `apps/processing-agent/agent/publishing/facebook.py`
- Create: `apps/processing-agent/agent/publishing/facebook_auth.py`
- Test: `apps/processing-agent/tests/publishing/test_facebook.py`

- [ ] Test the Phase 02 decision: use staged upload only if proven for the target
  account/API version; otherwise assert zero provider calls before approval.
- [ ] Test resumable transfer where supported, duplicate detection, token expiry,
  rate limits, provider processing and partial failure.
- [ ] Load token from Vault and record account/page ID plus API version without
  recording tokens.
- [ ] Before the first byte, revalidate exact render, metadata and approval.
- [ ] Run focused Facebook tests and an approved non-public test-account smoke
  test.

### Task 4: Coordinate destination-specific states and retries

**Files:**
- Create: `apps/processing-agent/agent/publishing/coordinator.py`
- Test: `apps/processing-agent/tests/publishing/test_coordinator.py`
- Modify: `apps/control-plane/test/publication-integration.test.ts`

- [ ] Test YouTube success/Facebook failure, inverse failure, cancellation,
  retry after process restart, duplicate command and rights revocation.
- [ ] Never collapse two destinations into one aggregate success. Report
  independent progress, provider ID and terminal reason.
- [ ] Retry only the failed destination and retain successful provider IDs.
- [ ] Block new attempts after rights revocation; flag already uploaded assets
  for manual provider removal and audit the response.
- [ ] Run both Python coordinator and Worker publication integration tests.

### Task 5: Add operator recovery runbooks and audit checks

**Files:**
- Create: `docs/runbooks/youtube-upload-recovery.md`
- Create: `docs/runbooks/facebook-upload-recovery.md`
- Create: `docs/runbooks/rights-revocation.md`
- Create: `apps/processing-agent/tests/integration/test_publish_flow.py`
- Modify: `docs/implementation/status.md`

- [ ] Document safe actions for expired OAuth, quota exhaustion, abandoned
  session, provider processing failure, duplicate suspicion and manual removal.
- [ ] Make runbooks use opaque IDs and Vault references, never copy credentials.
- [ ] Run E2E with a validated short render: YouTube private upload and the
  Phase 02-approved Facebook path.
- [ ] Verify audit sequence: approval → claim → upload start → provider result;
  every event references exact hashes and actor/service identity.
- [ ] Run full Worker and agent test suites and mark Phase 06 `PASS`.

## Phase exit criteria

- YouTube test content exists only as private and survives an interrupted upload.
- Facebook sends no bytes before approval unless the exact staged path passed.
- One destination can retry without duplicating or altering the other.
- Rights revocation, quota and expired-credential behavior are documented and
  tested.

