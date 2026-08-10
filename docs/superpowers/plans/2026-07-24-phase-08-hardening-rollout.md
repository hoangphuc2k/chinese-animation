# Phase 08 — End-to-End Hardening and Rollout

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the complete system is secure, recoverable, observable and safe to enable for its measured production envelope.

**Architecture:** Cross-system tests exercise the deployed boundaries with
rights-cleared fixtures. Infrastructure is reproducible; rollout starts
disabled, enables one duration tier and one operator group, then expands only
after monitored acceptance.

**Tech Stack:** Wrangler, Cloudflare D1/Access/Tunnel, OCI IAM/Vault/Bastion,
systemd, pytest, Vitest, Playwright, shell security checks.

## Ownership

Phase 08 owns `infra`, cross-system tests, release/runbook documentation and
deployment configuration. Product behavior changes return to the owning phase
plan and repeat its tests; hardening must not patch around broken contracts.

### Task 1: Codify least-privilege environments

**Files:**
- Create: `infra/cloudflare/README.md`
- Create: `infra/cloudflare/wrangler.production.jsonc`
- Create: `infra/oci/README.md`
- Create: `infra/oci/policies.md`
- Create: `scripts/validate-deployment-config.mjs`
- Test: `scripts/validate-deployment-config.test.ts`

- [ ] Test distinct local/staging/production bindings, disabled-by-default
  imports, Access audience, D1 binding and non-secret preview origin.
- [ ] Document exact OCI dynamic-group/Vault/Bastion/volume/network policies
  with least privilege and no credential values.
- [ ] Prohibit public inbound agent ports; preview uses the proven tunnel and
  origin authentication.
- [ ] Run deployment config tests and review the generated binding inventory.

### Task 2: Build full acceptance and failure-injection suites

**Files:**
- Create: `tests/e2e/test_manual_https_flow.py`
- Create: `tests/e2e/test_oci_inbox_flow.py`
- Create: `tests/e2e/test_publish_flow.py`
- Create: `tests/e2e/test_failure_recovery.py`
- Create: `tests/e2e/conftest.py`

- [ ] Test both ingest paths through transcript, script edit/approval, render
  review and destination approval with rights-cleared fixtures.
- [ ] Inject Worker restart, agent restart, expired lease, duplicate callback,
  network timeout, disk floor, quota exhaustion, expired preview and provider
  partial failure.
- [ ] Assert no duplicated render/upload, no skipped approval and deterministic
  recovery to a visible state.
- [ ] Run `uv run pytest tests/e2e -q` against staging.

### Task 3: Perform security and privacy validation

**Files:**
- Create: `scripts/scan-sensitive-output.mjs`
- Create: `tests/security/ssrf.test.ts`
- Create: `tests/security/authorization.test.ts`
- Create: `docs/security/threat-model.md`
- Create: `docs/security/data-inventory.md`

- [ ] Threat-model source URLs/files, Access identity, lease/preview tokens,
  provider credentials, prompt injection, media parser inputs and publishing.
- [ ] Re-run SSRF, traversal, forged JWT, CSRF, role escalation, replay,
  cross-job preview and malicious subtitle/media cases.
- [ ] Scan Git-tracked files, test artifacts, logs and screenshots using
  synthetic canary secrets; fail CI when a canary is found.
- [ ] Inventory every stored field, purpose, owner and retention rule; confirm
  media expires after seven days and Vault owns external credentials.
- [ ] Run security suites and retain only redacted reports.

### Task 4: Verify observability, alerts and recovery runbooks

**Files:**
- Create: `docs/runbooks/agent-offline.md`
- Create: `docs/runbooks/disk-pressure.md`
- Create: `docs/runbooks/d1-recovery.md`
- Create: `docs/runbooks/provider-outage.md`
- Create: `docs/runbooks/preview-tunnel.md`
- Create: `docs/operations/alerts.md`

- [ ] Define actionable alerts for queue age, lease churn, stage failure rate,
  disk floor, agent heartbeat, provider quota and retention backlog.
- [ ] Ensure dashboards/log queries join by opaque request/job/publication IDs
  and contain no secrets or raw source URLs.
- [ ] Rehearse D1 Time Travel/export restore, agent workspace recovery, tunnel
  outage and disk-pressure cleanup in staging.
- [ ] Record recovery time, data loss boundary, operator decision and evidence
  for each drill.

### Task 5: Re-run the production-shape duration benchmark

**Files:**
- Create: `artifacts/release/.gitkeep`
- Create: `docs/operations/capacity-envelope.md`
- Modify: `docs/implementation/poc-decisions.md`

- [ ] Run the Phase 02 workflow on the final OCI image and production volume for
  each duration tier up to the previously passing maximum.
- [ ] Confirm at most 12 hours elapsed, below 10 GB peak RAM and at least 20 GB
  free disk throughout; reduce the import limit when final measurements regress.
- [ ] Set the server-advertised duration/file limits from the measured envelope,
  not frontend constants.
- [ ] Save redacted tool/image versions, metrics and output hashes.

### Task 6: Execute staged rollout and final acceptance

**Files:**
- Create: `docs/release/production-checklist.md`
- Create: `docs/release/rollback-plan.md`
- Create: `docs/release/acceptance-matrix.md`
- Modify: `docs/implementation/status.md`

- [ ] Map every design acceptance criterion to an automated test, PoC artifact
  or named manual check with owner and date.
- [ ] Deploy schema/reader support, Worker, agent and UI in compatible order;
  smoke test while imports remain disabled.
- [ ] Enable only the shortest passing duration and named operator group; process
  one rights-cleared job and inspect audit/provider state.
- [ ] Expand to the measured maximum only after queue, error, disk and quota
  signals remain within documented thresholds.
- [ ] Roll back by disabling new imports first, preserving in-flight evidence,
  then restoring the prior Worker/agent versions according to the runbook.
- [ ] Run `pnpm install --frozen-lockfile`, `pnpm lint`, `pnpm typecheck`,
  `pnpm test`, `pnpm e2e`, Python lint/type/tests and sensitive-output scan.

## Phase exit criteria

- Acceptance matrix has no unmapped mandatory requirement.
- Security, recovery, E2E, visual and production-shape capacity checks pass.
- A rights-cleared canary completes without duplicate or premature publishing.
- Rollback is rehearsed, imports have an explicit enable flag, and Phase 08 is
  recorded `PASS`.

