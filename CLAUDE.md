# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-admin system that takes **creator-authorized** Chinese animation videos and produces Vietnamese versions. The pipeline is: transcribe (whisper.cpp) → translate (Gemini) → voice (Azure TTS) → render with subtitles and watermark (FFmpeg) → preview → publish to YouTube (private first) and Facebook (only after approval).

The spec comes first and is authoritative. Read it before making architectural changes, and update it when a decision changes:
- `2026-07-24-chinese-animation-localization-mvp-design.md` covers scope, states, rights, limits and PoC gates.
- `docs/superpowers/specs/2026-07-24-operations-console-ui-design.md` and `docs/ui-mockups/*.png` cover the UI.
- `docs/superpowers/plans/` holds the phase plans. `docs/implementation/status.md` records phase status; never infer completion from the presence of files.
- `claudedocs/2026-10-26-product-audit-report.md` is the latest gap and defect audit.

The repo path contains a **trailing space** (`.../chinese-nimation /`). Quote paths.

## Commands

Toolchain: Node 24+ with **npm workspaces**. pnpm commands in older plans are superseded by the npm equivalents. Python 3.12+ with uv.

```sh
npm install
npm run verify              # lint + prettier + typecheck + all TS tests + contracts:check + py_compile generated models
npm test                    # all workspace vitest suites
npm run contracts:generate  # regenerate OpenAPI + TS types + Python models
npm run contracts:check     # fails if generated output is stale

# control plane
npm test --workspace=@localizer/control-plane -- test/lease-concurrency.test.ts   # single file
npm test --workspace=@localizer/control-plane -- -t "name of test"                # single test
npm run db:migrate:local --workspace=@localizer/control-plane                     # needs Wrangler (macOS 13.5+/Linux)
npm run dev --workspace=@localizer/control-plane                                  # wrangler dev

# processing agent
uv sync --directory apps/processing-agent
uv run --directory apps/processing-agent pytest -q
uv run --directory apps/processing-agent pytest tests/publishing/test_youtube.py::test_name

# PoC gate artifacts
node scripts/run-poc-gates.mjs artifacts/pocs

# agent image (needs Docker Buildx; CI alternative: the agent-image workflow)
REGISTRY=registry.example.com/localizer scripts/build-agent-image.sh
docker compose -f infra/processing-agent/docker-compose.yml --env-file infra/processing-agent/stack.env.example config --quiet
```

CI (`.github/workflows/verify.yml`, ubuntu) runs `npm ci`, the local D1 migration, `npm run verify`, then pytest. The local macOS 12 host cannot run Wrangler or Workerd, so CI is the only place native D1 runs.

## Architecture

There are two execution planes. They talk only through the versioned `/api/v1` HTTP contract.

**Control plane:** `apps/control-plane/`, a TypeScript Cloudflare Worker built on Hono with D1.
- `src/index.ts` is the router.
  - `/api/v1/agent/*` serves the agent. Access service-token JWTs carry the client ID in `common_name` with an empty `sub`; the Worker matches it to `AGENT_SERVICE_CLIENT_ID`. Routes: claim, heartbeat, complete (with optional `failureCode`), and script-approval lookup.
  - Browser routes serve the single admin (Access JWT email = `ADMIN_EMAIL`, no roles claim; CSRF origin = `ADMIN_ORIGIN`, token from `GET /api/v1/csrf-token`): projects, imports, `GET /jobs/:id`, cancel/retry/events, scripts, renders, publish-approval.
  - The daily cron runs `scheduled/retention.ts`.
- Layers: `api/` (routes, Zod-validated) → `services/` (job, lease, approval, idempotency, quota) → `repositories/` (raw SQL over D1). `domain/` holds the job and destination state machines.
- **Leasing** (`services/lease-service.ts`) is one atomic `UPDATE … RETURNING` CTE. Only a hash of the lease token is stored. Each claim is projected into a state-specific `pipelineInput` built from `jobs.pipeline_lineage_json`; the lineage holds artifact hashes and opaque refs, never paths, URLs or script text. Completion is conditional on lease token, owner, expiry, expected state and job version.
- All mutations need an `Idempotency-Key`; the key is reserved before the mutation runs (`idempotency-service.ts`). Approvals bind an exact script or render version plus a hash. Edits create a new version and invalidate the old approval.
- Job lifecycle rules that span files:
  - Import stores `allowedProcessors` in `source_assets.rights_metadata_json`; without `GOOGLE_GEMINI`, `TRANSCRIBING → TRANSLATING` is redirected to `MANUAL_REVIEW_REQUIRED`.
  - Completing `TRANSLATING → WAITING_SCRIPT_REVIEW` creates `script_versions` v1 from the agent's SCRIPT artifact hash. `approveScript` (latest version, review state only) moves the job to `GENERATING_VOICE`.
  - `READY_FOR_PREVIEW` and `FAILED` set `retention_deadline` (+7 days). The cron expires or cleans up past it by inserting a `DELETE_EXPIRED` job keyed by `target_job_id`.
  - Claims stop at `attempt_count >= 5` per step (reset on each completion and on retry); the cron moves those jobs to `FAILED` (retryable, with a retention window). Retry also clears the deadline and cancels a pending cleanup; cleanup is only claimed for a terminal target.
  - Publish approval recomputes the metadata hash with `publishMetadataHash` (`packages/contracts/src/publishing.ts`), which must stay byte-identical to the agent's `metadata_sha256`.
- No UI code exists yet. Phase 07 will add Vite + Preact served as Worker static assets.

**Shared contracts:** `packages/contracts/`.
- The canonical Zod schemas are in `src/`: states, jobs, ingest, review, publishing, operations, errors.
- `scripts/generate.ts` writes `generated/openapi.json`, `generated/typescript/api.d.ts` and `apps/processing-agent/src/localizer_agent/generated/models.py`.
- **Gotcha:** the Python Pydantic models are a hand-written template plus string `.replace()` patches inside `generate.ts`, not derived from the OpenAPI. When you change a schema the agent consumes, update that template too. Never hand-edit generated files.
- Breaking changes require `/api/v2`.

**Processing agent:** `apps/processing-agent/`, Python on OCI ARM64. Its only dependency is pydantic; it uses the stdlib for HTTP.
- `__main__.py` wires `config.AgentConfig` (env vars, see `.env.example`) into the stage handlers. Each handler is keyed by **job state**: QUEUED, INGESTING, PROBING, EXTRACTING_AUDIO, TRANSCRIBING, TRANSLATING, GENERATING_VOICE, MIXING, RENDERING, READY_FOR_PREVIEW. QUEUED uses a `JobTypeRouter`: IMPORT goes to the import supervisor, DELETE_EXPIRED to `cleanup.py`.
- `pipeline/supervisor.py` polls claims per state and reports deterministic errors (`_DETERMINISTIC_FAILURES`) as FAILED or MANUAL_REVIEW_REQUIRED with a failure code; other errors are left to lease expiry and the attempt cap. `lease.py` `LeaseGuard` heartbeats and asserts the lease before completing.
- The agent authenticates with `CF-Access-Client-Id` / `CF-Access-Client-Secret` headers (`AGENT_ACCESS_CLIENT_ID`, `AGENT_ACCESS_CLIENT_SECRET`).
- Stages are in `pipeline/stages.py` and `pipeline/transcription_stage.py`. Provider and tool wrappers live in `ingest.py` (SSRF-safe HTTPS + inbox), `media.py` (ffprobe/ffmpeg), `transcription/`, `localization/` (Gemini + validator), `speech/` (Azure + timing), `subtitles/`, `render/` and `preview/registry.py`.
- `publishing/` holds the YouTube resumable private upload and the Facebook after-approval upload, both behind `base.py`'s approval check. They are **not yet wired** into `__main__.py`.
- Media lives only on the OCI workspace volume. The Worker never proxies media. Preview is meant to be Nginx (`deploy/nginx/`) behind Cloudflare Tunnel + Access with 5-minute render-bound tokens.

**Other dirs:**
- `pocs/`: isolated risk-gate PoCs. Results go to `artifacts/pocs/*.json` as `PASS | FAIL_FALLBACK | BLOCKED`. All are currently BLOCKED.
- `infra/processing-agent/`: the Portainer stack (`docker-compose.yml`, `stack.env.example`, `README.md` deployment guide) and an alternative systemd unit. The image is `apps/processing-agent/Dockerfile` (whisper.cpp v1.7.4 built from source); `.github/workflows/agent-image.yml` pushes it. Media lives under `/mnt/localizer` (workspaces, inbox read-only, models, assets).
- `.superpowers/sdd/`: per-task brief and review ledgers from plan execution.

## Database (D1)

- Migrations are in `apps/control-plane/db/migrations/0001…0008`. They are monotonic: never edit a committed migration, add a new one plus a test in `test/migrations.test.ts`.
- Tables:
  - `projects`, `source_assets`
  - `jobs`, which has `status`, `version`, lease columns, `attempt_count`, `retention_deadline`, `pipeline_lineage_json` and `target_job_id` (cleanup jobs); partial unique indexes allow one active IMPORT per asset and one active cleanup per target
  - `job_events`, `script_versions`, `render_versions`
  - `destination_publications`, unique on `(job_id, destination)`; `rights_review_required_at` flags published content after revocation
  - `approvals`, `idempotency_records`
  - `audit_events`, append-only
  - `quota_usage`
- These names differ from the spec's §12 list (`processing_jobs` → `jobs`, etc.). Spec tables for sources, rights, segments, glossary and settings do not exist yet.
- Tests emulate D1 with `test/sqlite-d1.ts` (`createMigratedD1()` applies every migration). `test/lifecycle.test.ts` drives a job across both planes' contracts end to end. `test/worker-harness.ts` builds the test `WorkerEnv`.

## Rules that matter here

- Rights are a hard gate. Never send content to a provider, or import or publish, without an explicit rights scope. Gemini needs explicit processor consent.
- YouTube uploads are always `privacyStatus=private` until final approval. Facebook gets zero bytes before final approval, unless the hidden-staging PoC passes.
- Never mark a PoC gate PASS without a real credentialed run. A failed gate applies only the fallback listed in the spec (§20), and never a silent paid fallback.
- Secrets belong in OCI Vault or Cloudflare secrets. Never put them in D1, logs, CLI args or fixtures. Keep logs free of paths, tokens and script bodies.
- Style:
  - Indentation: 2 spaces for TS/JSON/YAML, 4 for Python.
  - Naming: `PascalCase` types, `camelCase` TS identifiers, `snake_case` Python.
  - Test names describe behavior.
  - Every bug fix ships with a regression test.
  - Mock external APIs in unit tests.
- Commits: short imperative, Conventional prefixes (`feat:`, `fix:`).
