# Phase 01 Repository Foundation and Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a reproducible monorepo, test harness and canonical API contract before any product behavior is implemented.

**Architecture:** Root npm workspace scripts orchestrate TypeScript packages and the Python agent without mixing dependency managers. Zod schemas in `packages/contracts/src` are canonical; generated OpenAPI, TypeScript client types and Python models are committed and checked for drift.

**Tech Stack:** Git, Node.js LTS, Corepack/pnpm, TypeScript strict mode, ESLint, Prettier, Vitest, Zod, `@asteasolutions/zod-to-openapi`, `openapi-typescript`, Python 3.12+, uv, Pydantic and pytest.

## Global Constraints

- Execute after the master plan and both design specifications are approved.
- Use current stable package versions at execution time, then commit
  `pnpm-lock.yaml` and `uv.lock`; do not use preview releases.
- Generated files contain a header naming their generator and are never edited.
- This phase defines wire names and IDs; later phases consume them unchanged.

---

### Task 1: Initialize version control and repository hygiene

**Files:**
- Create: `.gitignore`
- Create: `.editorconfig`
- Create: `.gitattributes`
- Create: `README.md`
- Create: `docs/implementation/status.md`

**Interfaces:**
- Produces: clean Git repository and phase-status format required by all plans.

- [ ] **Step 1: Verify the precondition**

Run: `git rev-parse --is-inside-work-tree`

Expected: exit non-zero with `not a git repository`. If it succeeds, do not
reinitialize; inspect the current branch and continue safely.

- [ ] **Step 2: Initialize Git when absent**

Run: `git init -b main`

Expected: `.git/` created and `git branch --show-current` prints `main`.

- [ ] **Step 3: Add repository hygiene**

Use this `.gitignore` baseline:

```gitignore
node_modules/
dist/
.wrangler/
.dev.vars
.env
.env.*
!.env.example
.venv/
__pycache__/
.pytest_cache/
.mypy_cache/
.ruff_cache/
*.pyc
coverage/
playwright-report/
test-results/
tmp/
artifacts/
*.mp4
*.mkv
*.webm
*.wav
*.mp3
*.srt
*.ass
cookies.txt
client_secret*.json
token*.json
```

Set UTF-8, LF, final newline and indentation in `.editorconfig`; mark PNG files
binary in `.gitattributes`. `README.md` must link the product design, UI spec,
mockups and master plan and state that no production import is enabled yet.

- [ ] **Step 4: Scan for accidental secrets/media**

Run:

```bash
git status --short
rg -n --hidden -g '!.git/**' \
  '(BEGIN (RSA|OPENSSH) PRIVATE KEY|client_secret|refresh_token|CF-Access-Client-Secret)'
```

Expected: only intentional documentation labels, no credential values.

- [ ] **Step 5: Commit**

```bash
git add .gitignore .editorconfig .gitattributes README.md docs
git commit -m "chore: initialize repository documentation"
```

### Task 2: Create root workspace and quality scripts

**Files:**
- Create: `package.json`
- Create: `pnpm-workspace.yaml`
- Create: `tsconfig.base.json`
- Create: `eslint.config.js`
- Create: `.prettierrc.json`
- Create: `.prettierignore`

**Interfaces:**
- Produces: root commands `lint`, `format:check`, `typecheck`, `test`,
  `contracts:generate`, `contracts:check`, `verify`.

- [ ] **Step 1: Write a failing root-script check**

Run: `corepack pnpm run verify`

Expected: FAIL because `package.json` does not exist.

- [ ] **Step 2: Create the workspace**

`package.json` must be private and expose deterministic orchestration:

```json
{
  "name": "chinese-animation-localizer",
  "private": true,
  "packageManager": "pnpm@10",
  "engines": { "node": ">=24" },
  "scripts": {
    "lint": "pnpm -r --if-present lint",
    "format:check": "prettier --check .",
    "typecheck": "pnpm -r --if-present typecheck",
    "test": "pnpm -r --if-present test",
    "contracts:generate": "pnpm --filter @localizer/contracts generate",
    "contracts:check": "pnpm --filter @localizer/contracts check",
    "verify": "pnpm lint && pnpm format:check && pnpm typecheck && pnpm test && pnpm contracts:check"
  },
  "devDependencies": {
    "@eslint/js": "latest",
    "eslint": "latest",
    "prettier": "latest",
    "typescript": "latest",
    "typescript-eslint": "latest"
  }
}
```

`pnpm-workspace.yaml` includes `apps/*` and `packages/*`. TypeScript base config
uses `strict`, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`,
`noImplicitOverride`, `useUnknownInCatchVariables`, `verbatimModuleSyntax` and
`moduleResolution: Bundler`.

- [ ] **Step 3: Install and lock**

Run: `corepack enable && pnpm install`

Expected: `pnpm-lock.yaml` exists and install exits 0.

- [ ] **Step 4: Verify format and lint discovery**

Run: `pnpm lint && pnpm format:check`

Expected: exit 0; no source packages exist yet.

- [ ] **Step 5: Commit**

```bash
git add package.json pnpm-workspace.yaml pnpm-lock.yaml tsconfig.base.json eslint.config.js .prettierrc.json .prettierignore
git commit -m "chore: add monorepo quality tooling"
```

### Task 3: Define canonical identifiers, states and error envelopes

**Files:**
- Create: `packages/contracts/package.json`
- Create: `packages/contracts/tsconfig.json`
- Create: `packages/contracts/vitest.config.ts`
- Create: `packages/contracts/src/ids.ts`
- Create: `packages/contracts/src/states.ts`
- Create: `packages/contracts/src/errors.ts`
- Create: `packages/contracts/src/index.ts`
- Test: `packages/contracts/src/states.test.ts`

**Interfaces:**
- Produces: branded ID schemas, all workflow enums and `ApiError`.
- Consumes: exact states from product design section 7 and errors from section 14.

- [ ] **Step 1: Write failing state tests**

Test exact valid/invalid values:

```ts
import { describe, expect, it } from "vitest";
import { ProcessingState, DestinationState } from "./states";

describe("workflow states", () => {
  it("accepts terminal and review states", () => {
    expect(ProcessingState.parse("TIMING_REVIEW_REQUIRED")).toBe(
      "TIMING_REVIEW_REQUIRED",
    );
    expect(ProcessingState.parse("EXPIRED")).toBe("EXPIRED");
    expect(DestinationState.parse("STAGED_HIDDEN")).toBe("STAGED_HIDDEN");
  });

  it("rejects uncontracted states", () => {
    expect(() => ProcessingState.parse("DONE")).toThrow();
  });
});
```

Run: `pnpm --filter @localizer/contracts test`

Expected: FAIL because modules do not exist.

- [ ] **Step 2: Implement exact schemas**

Use Zod string enums for `SourceState`, `DiscoveryState`, `ProcessingState`,
`DestinationState`, `AdapterHealth`, `JobType`, `ErrorCode`, `ApprovalType` and
`IngestKind`. IDs are UUID strings with branded TypeScript output:

```ts
export const JobId = z.string().uuid().brand<"JobId">();
export const RenderId = z.string().uuid().brand<"RenderId">();
export const SegmentId = z.string().regex(/^SEG-[0-9]{6}$/).brand<"SegmentId">();
```

`ApiError` is:

```ts
export const ApiError = z.object({
  error: z.object({
    code: ErrorCode,
    message: z.string().min(1),
    requestId: z.string().uuid(),
    retryable: z.boolean(),
    details: z.record(z.string(), z.unknown()).optional()
  })
});
```

- [ ] **Step 3: Run focused tests**

Run: `pnpm --filter @localizer/contracts test`

Expected: all contract state tests PASS.

- [ ] **Step 4: Commit**

```bash
git add packages/contracts
git commit -m "feat: define canonical workflow contracts"
```

### Task 4: Define request and response contracts

**Files:**
- Create: `packages/contracts/src/ingest.ts`
- Create: `packages/contracts/src/jobs.ts`
- Create: `packages/contracts/src/review.ts`
- Create: `packages/contracts/src/publishing.ts`
- Create: `packages/contracts/src/operations.ts`
- Test: `packages/contracts/src/contracts.test.ts`

**Interfaces:**
- Produces: `/api/v1` payload schemas used by Worker, frontend and agent.

- [ ] **Step 1: Write failing representative payload tests**

Cover:

- `CreateImportRequest` with `CREATOR_HTTPS_URL` or `OCI_INBOX_FILE`.
- `ApproveScriptRequest` with `scriptVersion` and 64-char SHA-256.
- `ApprovePublishRequest` with render version, metadata hash, destinations and
  nullable UTC schedule.
- `ClaimJobResponse`, `HeartbeatRequest`, `CompleteStepRequest`.
- dashboard/inbox, segment review and render review responses.

The approval test must reject a stale/malformed hash:

```ts
expect(() =>
  ApproveScriptRequest.parse({ scriptVersion: 4, contentHash: "abc" }),
).toThrow();
```

- [ ] **Step 2: Implement schemas with strict objects**

Every external object calls `.strict()`. Dates are ISO-8601 UTC strings ending
in `Z`; byte counts are non-negative safe integers; percentages are `0..100`.
Use discriminated unions for ingest locator and destination metadata. Never use
`z.any()`.

- [ ] **Step 3: Add compatibility fixtures**

Create JSON fixtures under `packages/contracts/fixtures/` for one valid value of
every top-level request and response. The test loads each fixture and parses it
through its schema.

- [ ] **Step 4: Run tests**

Run: `pnpm --filter @localizer/contracts test`

Expected: all fixtures and rejection cases PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/contracts
git commit -m "feat: define API payload contracts"
```

### Task 5: Generate OpenAPI and both client model sets

**Files:**
- Create: `packages/contracts/scripts/generate.ts`
- Create: `packages/contracts/scripts/check-generated.ts`
- Generate: `packages/contracts/generated/openapi.json`
- Generate: `packages/contracts/generated/typescript/api.d.ts`
- Generate: `apps/processing-agent/src/localizer_agent/generated/models.py`
- Create: `apps/processing-agent/pyproject.toml`
- Create: `apps/processing-agent/src/localizer_agent/__init__.py`
- Test: `apps/processing-agent/tests/test_generated_models.py`

**Interfaces:**
- Produces: OpenAPI 3.1 document and synchronized TS/Python models.
- Consumes: Task 3–4 Zod schemas.

- [ ] **Step 1: Add generator dependencies**

Install stable `@asteasolutions/zod-to-openapi`, `openapi-typescript`,
`tsx`, `zod`, `vitest`, and Python `pydantic`, `pytest`, `datamodel-code-generator`
with pnpm/uv. Commit both lockfiles.

- [ ] **Step 2: Implement deterministic generation**

The script registers all schemas, emits sorted OpenAPI JSON with
`info.version: 0.1.0`, runs `openapi-typescript`, then invokes
`datamodel-codegen --output-model-type pydantic_v2.BaseModel`. Generated output
starts with:

```text
# Generated from packages/contracts/generated/openapi.json. DO NOT EDIT.
```

- [ ] **Step 3: Write cross-language parse tests**

Python test loads the same approval and job fixtures used by TypeScript and
validates the generated Pydantic models. Invalid hash and unknown fields must
fail.

- [ ] **Step 4: Generate and test**

Run:

```bash
pnpm contracts:generate
pnpm --filter @localizer/contracts test
cd apps/processing-agent && uv run pytest tests/test_generated_models.py -q
```

Expected: generated files exist and both language suites PASS.

- [ ] **Step 5: Implement drift check**

`contracts:check` generates into a temporary directory and exits non-zero if
any committed generated file differs. It must not rewrite the working tree.

Run: `pnpm contracts:check`

Expected: `Generated contracts are current`.

- [ ] **Step 6: Commit**

```bash
git add packages/contracts apps/processing-agent pnpm-lock.yaml uv.lock
git commit -m "feat: generate cross-language API contracts"
```

### Task 6: Add CI and phase status gate

**Files:**
- Create: `.github/workflows/verify.yml`
- Modify: `docs/implementation/status.md`

**Interfaces:**
- Produces: required verification workflow and recorded Phase 01 result.

- [ ] **Step 1: Create CI**

CI runs on pull requests and pushes, installs Node via Corepack and Python via
uv, restores caches, then runs:

```bash
pnpm install --frozen-lockfile
pnpm verify
cd apps/processing-agent && uv sync --frozen && uv run pytest -q
```

The workflow has read-only repository permissions and no production secrets.

- [ ] **Step 2: Run the complete phase suite locally**

Run:

```bash
pnpm install --frozen-lockfile
pnpm verify
cd apps/processing-agent && uv sync --frozen && uv run pytest -q
git status --short
```

Expected: all commands exit 0; status contains only the intended status-file
update before commit.

- [ ] **Step 3: Record the phase gate**

Set Phase 01 to `PASS`, record commands, contract version `0.1.0`, migration
`none`, and limitations `No product behavior or external integration`.

- [ ] **Step 4: Commit**

```bash
git add .github docs/implementation/status.md
git commit -m "ci: verify contracts and workspace"
```

## Phase 01 exit criteria

- [ ] Fresh clone installs using frozen locks.
- [ ] TypeScript and Python parse identical contract fixtures.
- [ ] Generated-contract drift fails CI.
- [ ] Strict compiler/linter/test commands pass.
- [ ] No product API, database migration or provider integration has been
  implemented prematurely.
