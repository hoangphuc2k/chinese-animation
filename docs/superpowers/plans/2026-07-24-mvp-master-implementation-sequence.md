# Chinese Animation Localization MVP Master Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the approved manual-first localization and publishing MVP without parallel work corrupting shared contracts, schema, workflow states, or UI behavior.

**Architecture:** An npm-workspaces monorepo contains one Cloudflare Worker/Preact control plane, one Python OCI processing agent, generated API clients, infrastructure definitions, and cross-system tests. Work proceeds through strict phase gates: mockups and specs, repository/contracts, external PoCs, control plane, ingest agent, media pipeline, publishing, UI, then hardening.

**Tooling decision:** npm workspaces and `package-lock.json` are the active Node
toolchain. Any older phase command that says `pnpm` is superseded by the
equivalent npm workspace command in the repository root.

**Tech Stack:** Node.js LTS, pnpm, TypeScript, Vite, Preact, Hono, Zod/OpenAPI, Cloudflare Workers Static Assets, D1, Vitest, Playwright; Python 3.12+, uv, Pydantic, httpx, pytest; OCI ARM64, Vault, Bastion, block volume, Nginx, cloudflared, FFmpeg, whisper.cpp, Azure Speech, Gemini, YouTube Data API and Meta Graph API.

## Global Constraints

- Read `2026-07-24-chinese-animation-localization-mvp-design.md`, `docs/superpowers/specs/2026-07-24-operations-console-ui-design.md`, and all three `docs/ui-mockups/*.png` before implementation.
- The guaranteed ingest paths are creator HTTPS URL and OCI Bastion/SCP inbox file. Bilibili and Douyin automation are optional gates.
- Workers never proxy media bodies. Media remains on OCI temporary storage.
- Provider keys, source cookies and OAuth refresh tokens live in OCI Vault; they never enter D1, logs or frontend bundles.
- All mutations require `Idempotency-Key`. Script and publish approvals bind exact versions and hashes.
- Production imports stay disabled until the media benchmark establishes a passing maximum duration.
- UI copy is English. Chinese and Vietnamese appear only as content.
- Every task follows red-green-refactor, finishes with its listed focused tests, then runs the phase test suite.
- Never edit a committed migration or generated contract manually. Add a new migration or regenerate from the canonical contract.

---

## Dependency graph

```text
Approved product design + approved UI mockups
                  |
                  v
Phase 01 Repository foundation and canonical contracts
                  |
                  v
Phase 02 Mandatory risk-gate PoCs ----------------------+
                  |                                     |
                  v                                     |
Phase 03 Control-plane schema, auth, workflow and API   |
                  |                                     |
                  v                                     |
Phase 04 Processing agent and guaranteed ingest         |
                  |                                     |
                  v                                     |
Phase 05 Localization, TTS and render pipeline           |
                  |                                     |
                  v                                     |
Phase 06 YouTube/Facebook publishing integrations <-----+
                  |
                  +------------------+
                  |                  |
                  v                  v
Phase 07 Operations-console UI   API/agent contract frozen
                  \                  /
                   v                v
Phase 08 End-to-end hardening, recovery and rollout
```

Phases 03–07 may contain internally parallel tasks only when their file
ownership tables say they are independent. No later phase begins before the
previous phase exit gate is recorded as `PASS`.

## Plan index and ownership

| Phase | Plan | Exclusive ownership during phase | Exit gate |
|---|---|---|---|
| 01 | `2026-07-24-phase-01-foundation-contracts.md` | root tooling, canonical OpenAPI generation, shared naming | clean install, lint, types and contract generation |
| 02 | `2026-07-24-phase-02-risk-gate-pocs.md` | `pocs/`, OCI bootstrap, PoC reports | every mandatory gate has measured PASS or approved fallback |
| 03 | `2026-07-24-phase-03-control-plane-core.md` | D1 migrations, Worker domain states, auth/idempotency/lease API | local Worker integration suite passes |
| 04 | `2026-07-24-phase-04-agent-ingest.md` | Python agent runtime, inbox and HTTPS ingest | both guaranteed ingest paths complete through media probe |
| 05 | `2026-07-24-phase-05-localization-media.md` | transcription, translation, TTS, FFmpeg rendering | approved script produces previewable versioned render |
| 06 | `2026-07-24-phase-06-publishing.md` | provider clients and destination state transitions | private YouTube and post-approval Facebook flows pass |
| 07 | `2026-07-24-phase-07-operations-console-ui.md` | Preact routes/components/styles | functional and visual QA passes all three references |
| 08 | `2026-07-24-phase-08-hardening-rollout.md` | cross-system E2E, security, observability, recovery | release checklist and acceptance suite pass |

## Contract change protocol

1. Change Zod request/response/domain schemas in
   `packages/contracts/src/`, never generated output.
2. Add or update contract tests first.
3. Run `pnpm contracts:generate`; commit OpenAPI and both generated clients in
   the same change.
4. Update the Worker producer before or in the same commit as a new required
   response field.
5. Update agent/frontend consumers in the same task. Optional additive fields
   may roll out producer-first.
6. Run `pnpm contracts:check`; it must report no generated diff.
7. Breaking changes require `/api/v2`; no breaking `/api/v1` mutation is allowed
   during the MVP.

## Migration protocol

- D1 files are monotonic: `0001_*.sql`, `0002_*.sql`, and so on.
- Each migration has a matching migration test and rollback note. D1 rollback
  means restoring Time Travel/export and redeploying the prior Worker; down SQL
  is not assumed.
- State values are text constrained by application validation. New state values
  deploy reader support before writers emit them.
- Seed data exists only in local/test scripts, never production migrations.

## Agent handoff protocol

At each phase boundary, update `docs/implementation/status.md` with:

```markdown
## Phase NN
- Result: PASS | BLOCKED
- Commit: <sha or “workspace has no Git metadata”>
- Commands: <exact verification commands>
- Artifacts: <reports, screenshots, benchmark files>
- Contract version: <OpenAPI info.version>
- D1 migration: <highest migration number>
- Known limitations: <explicit list, or “None”>
```

An implementer starting a phase reads this status file and the phase plan. They
must not infer completion from the presence of files.

## Master acceptance

- [ ] All phase exit gates are `PASS`.
- [ ] Mandatory design acceptance criteria map to at least one automated test or
  a named manual PoC check.
- [ ] Optional Bilibili/Douyin and Meta hidden-staging failures do not block the
  guaranteed manual-first MVP.
- [ ] No secret, source media, rights evidence body or preview token is present
  in Git history, D1 fixtures, logs or screenshots.
- [ ] Browser screenshots at 1536×1024, 1280×800 and 1024×768 match the approved
  mockups and UI specification.
- [ ] A 15-, 30- or 60-minute configured duration has passed the 12-hour,
  10-GB-RAM and 20-GB-free-disk benchmark gate.
- [ ] YouTube remains private until exact render/metadata approval and manual
  Studio review; Facebook receives no bytes before approval unless its optional
  staging gate passed.
- [ ] Restart, expired lease, disk pressure, quota exhaustion, rights revocation
  and seven-day retention scenarios all pass.

## Execution rule

Execute one phase at a time. Within a phase, assign at most one agent to each
exclusive ownership area. A reviewer runs the entire phase suite and checks the
contract/migration protocols before the next phase begins. If a mandatory PoC
fails, stop and apply only the fallback already approved in the design; do not
expand architecture or silently add a paid service.
