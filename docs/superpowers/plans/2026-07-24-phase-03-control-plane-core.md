# Phase 03 — Control-Plane Core

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the authenticated, version-safe workflow API and D1 state model that coordinate processing without carrying media bytes.

**Architecture:** A Hono Worker exposes `/api/v1`, serves the Preact shell as a static asset, validates canonical Zod contracts, and stores workflow metadata in D1. Services own state transitions; repositories own SQL; routes contain no business logic.

**Tech Stack:** TypeScript, Hono, Zod, Cloudflare Workers, D1, Vitest, Miniflare/Wrangler.

## Exclusive file ownership

Phase 03 owns `apps/control-plane/src/api`, `domain`, `repositories`,
`middleware`, `db/migrations`, and Worker integration tests. It must not add UI
routes/components or Python agent code. Contract changes follow the master
protocol.

### Task 1: Bootstrap the Worker and D1 test harness

**Files:**
- Create: `apps/control-plane/package.json`
- Create: `apps/control-plane/wrangler.jsonc`
- Create: `apps/control-plane/tsconfig.json`
- Create: `apps/control-plane/src/index.ts`
- Create: `apps/control-plane/src/env.ts`
- Create: `apps/control-plane/test/worker-harness.ts`
- Test: `apps/control-plane/test/health.test.ts`

- [ ] Write a failing `/api/v1/health` test with request ID and deployment
  version.
- [ ] Add typed bindings for D1, Access audience/issuer, preview origin and
  environment; secrets are bindings, never config literals.
- [ ] Return JSON problem details for unknown API routes and serve no media.
- [ ] Run `pnpm --filter control-plane test -- health`; expect all tests pass.

### Task 2: Add monotonic schema and repository invariants

**Files:**
- Create: `apps/control-plane/db/migrations/0001_initial.sql`
- Create: `apps/control-plane/src/repositories/*.ts`
- Create: `apps/control-plane/test/migrations.test.ts`
- Create: `apps/control-plane/test/repositories.test.ts`

- [ ] Test tables for projects, source assets, jobs, job events, script
  versions, render versions, destination publications, approvals, idempotency
  records and audit events.
- [ ] Add unique constraints for external IDs, `(job_id, version)`,
  `(actor_id, idempotency_key)`, and destination/provider asset identity.
- [ ] Store hashes, byte counts, rights metadata and expiry timestamps, not
  source bodies, preview tokens, OAuth tokens or filesystem paths.
- [ ] Add indexes for runnable jobs, expiring leases, project activity and
  retention scans.
- [ ] Test migration from an empty D1 and twice-applied failure behavior.
- [ ] Run `pnpm --filter control-plane db:migrate:local` then
  `pnpm --filter control-plane test -- migrations repositories`.

### Task 3: Enforce Access identity, role policy and CSRF

**Files:**
- Create: `apps/control-plane/src/middleware/access-auth.ts`
- Create: `apps/control-plane/src/middleware/authorize.ts`
- Create: `apps/control-plane/src/middleware/csrf.ts`
- Test: `apps/control-plane/test/auth.test.ts`

- [ ] Test valid operator/reviewer/admin identities plus missing, forged,
  expired, wrong-audience and wrong-issuer JWTs.
- [ ] Cache JWKS only within its HTTP cache policy and fail closed on unknown
  keys.
- [ ] Require same-origin plus synchronizer token for browser mutations; exempt
  authenticated agent endpoints that use a separate service credential.
- [ ] Define explicit action policies: create/import, edit script, approve
  script, render, approve publish, retry/cancel and admin configuration.
- [ ] Ensure audit actors derive from verified identity, never request JSON.
- [ ] Run `pnpm --filter control-plane test -- auth`.

### Task 4: Implement idempotent workflow transitions

**Files:**
- Create: `apps/control-plane/src/domain/job-state-machine.ts`
- Create: `apps/control-plane/src/domain/destination-state-machine.ts`
- Create: `apps/control-plane/src/services/idempotency-service.ts`
- Create: `apps/control-plane/src/services/job-service.ts`
- Test: `apps/control-plane/test/state-machines.test.ts`
- Test: `apps/control-plane/test/idempotency.test.ts`

- [ ] Enumerate allowed transitions from canonical contract states and reject
  skipped, stale, terminal and cross-destination transitions.
- [ ] Require `Idempotency-Key` on every mutation; hash actor, route and
  canonical request body.
- [ ] Return the original status/body for an identical replay and `409` when a
  key is reused with different input.
- [ ] Append immutable job and audit events in the same D1 batch as state
  changes.
- [ ] Run `pnpm --filter control-plane test -- state-machines idempotency`.

### Task 5: Implement atomic claim, heartbeat and completion

**Files:**
- Create: `apps/control-plane/src/services/lease-service.ts`
- Create: `apps/control-plane/src/api/agent-routes.ts`
- Test: `apps/control-plane/test/lease-concurrency.test.ts`
- Test: `apps/control-plane/test/agent-api.test.ts`

- [ ] Test concurrent claims, raw-token non-persistence, token mismatch,
  heartbeat extension, expiry/reclaim, stale completion and cancellation.
- [ ] Claim with one conditional D1 statement and `RETURNING`; hash the random
  lease token before storage and return raw token only once.
- [ ] Require job version and lease token for heartbeat/progress/result/failure.
- [ ] Make result reporting idempotent and reject output hashes that do not
  match the job’s expected stage/version.
- [ ] Run `pnpm --filter control-plane test -- lease-concurrency agent-api`.

### Task 6: Implement projects, scripts, renders and approvals API

**Files:**
- Create: `apps/control-plane/src/api/project-routes.ts`
- Create: `apps/control-plane/src/api/script-routes.ts`
- Create: `apps/control-plane/src/api/render-routes.ts`
- Create: `apps/control-plane/src/api/publication-routes.ts`
- Create: `apps/control-plane/src/services/approval-service.ts`
- Test: `apps/control-plane/test/workflow-api.test.ts`

- [ ] Test create/list/detail/import/cancel/retry and event timeline endpoints.
- [ ] Save script edits as immutable versions; approving binds script version
  and SHA-256. Any edit invalidates the prior approval.
- [ ] Save renders as immutable versions; publish approval binds render hash,
  destination and metadata version. Any change invalidates approval.
- [ ] Return opaque preview descriptors only; the Worker never reads or proxies
  media.
- [ ] Use cursor pagination, bounded page size and stable sort on all lists.
- [ ] Run `pnpm --filter control-plane test -- workflow-api`.

### Task 7: Add retention, quotas and structured observability

**Files:**
- Create: `apps/control-plane/src/scheduled/retention.ts`
- Create: `apps/control-plane/src/services/quota-service.ts`
- Create: `apps/control-plane/src/observability/logger.ts`
- Test: `apps/control-plane/test/retention.test.ts`
- Test: `apps/control-plane/test/quota.test.ts`

- [ ] Test seven-day media-expiry commands, rights revocation, terminal metadata
  retention, provider quota budgets and retry-after scheduling.
- [ ] Emit structured logs with request/job/project IDs, stage, duration and
  error code; redact headers, URLs, source titles when marked sensitive and all
  token-like values.
- [ ] Retention creates agent deletion work and records an audit event; it does
  not pretend OCI files were deleted before acknowledgement.
- [ ] Run `pnpm --filter control-plane test` and
  `pnpm --filter control-plane typecheck`.

## Phase exit criteria

- Local migration and the complete Worker test suite pass from a clean D1.
- Concurrent claim tests prove one winner and safe lease recovery.
- Approval invalidation, idempotent replay, auth, quotas and retention pass.
- OpenAPI generation has no drift and Phase 03 status is `PASS`.

