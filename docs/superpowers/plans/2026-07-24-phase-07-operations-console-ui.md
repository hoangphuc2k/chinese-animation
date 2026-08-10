# Phase 07 — Operations Console UI

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved English operations console faithfully, with safe version-aware review and publishing actions.

**Architecture:** Preact routes consume the generated API client through query
and mutation adapters. Code-native components implement the approved white,
cobalt and neutral visual system; server state is never duplicated into an
independent frontend workflow state machine.

**Tech Stack:** Preact, TypeScript, Vite, TanStack Query, CSS modules/tokens,
Vitest, Testing Library, Playwright.

## Required references and ownership

Before editing UI, inspect:

- `docs/ui-mockups/01-dashboard-inbox.png`
- `docs/ui-mockups/02-localization-review.png`
- `docs/ui-mockups/03-render-publish-review.png`
- `docs/superpowers/specs/2026-07-24-operations-console-ui-design.md`

Phase 07 exclusively owns `apps/control-plane/src/ui` and browser tests.
Backend changes require a separately reviewed contract task. Do not replace the
approved layout with a component-library default.

### Task 1: Bootstrap UI, tokens and visual test harness

**Files:**
- Create: `apps/control-plane/src/ui/main.tsx`
- Create: `apps/control-plane/src/ui/app.tsx`
- Create: `apps/control-plane/src/ui/styles/tokens.css`
- Create: `apps/control-plane/src/ui/styles/global.css`
- Create: `apps/control-plane/src/ui/test/render.tsx`
- Create: `apps/control-plane/playwright.config.ts`
- Test: `apps/control-plane/src/ui/app.test.tsx`

- [ ] Write a failing shell test for skip link, landmark structure, route outlet,
  navigation labels and authenticated user menu.
- [ ] Implement the spec’s exact colors, typography scale, spacing, radii,
  shadows, focus ring and reduced-motion behavior as CSS variables.
- [ ] Add API error boundary, session-expired state and route-level loading
  skeletons without layout shift.
- [ ] Configure screenshots at 1536×1024, 1280×800 and 1024×768.
- [ ] Run `pnpm --filter control-plane test -- src/ui/app.test.tsx`.

### Task 2: Build the shared application shell

**Files:**
- Create: `apps/control-plane/src/ui/components/AppShell.tsx`
- Create: `apps/control-plane/src/ui/components/Sidebar.tsx`
- Create: `apps/control-plane/src/ui/components/TopBar.tsx`
- Create: `apps/control-plane/src/ui/components/StatusBadge.tsx`
- Create: `apps/control-plane/src/ui/components/AsyncBoundary.tsx`
- Test: `apps/control-plane/src/ui/components/AppShell.test.tsx`

- [ ] Test desktop sidebar, tablet compact behavior, mobile drawer, active route,
  keyboard navigation, focus return and status text independent of color.
- [ ] Match the approved restrained cobalt navigation, true-white content area,
  density and border hierarchy.
- [ ] Use inline code-native SVG icons with accessible names only when labels
  are absent.
- [ ] Run shared component tests and keyboard checks.

### Task 3: Implement Dashboard and New Import dialog

**Files:**
- Create: `apps/control-plane/src/ui/routes/DashboardPage.tsx`
- Create: `apps/control-plane/src/ui/features/import/NewImportDialog.tsx`
- Create: `apps/control-plane/src/ui/features/jobs/JobTable.tsx`
- Create: `apps/control-plane/src/ui/features/jobs/JobFilters.tsx`
- Test: `apps/control-plane/src/ui/routes/DashboardPage.test.tsx`

- [ ] Test loading, empty, populated, filter, pagination, API failure and
  permission-denied states.
- [ ] Support only `Creator HTTPS URL` and `OCI inbox file` as guaranteed source
  choices; optional adapters render only when server capability flags enable
  them.
- [ ] Capture rights basis, expected hash where available, source choice and
  opaque inbox ID; show validation errors adjacent to fields.
- [ ] Require explicit confirmation before import and attach one
  `Idempotency-Key` per user intent.
- [ ] Match `01-dashboard-inbox.png`, including metrics, workflow table and
  compact actionable status.

### Task 4: Implement Localization Review

**Files:**
- Create: `apps/control-plane/src/ui/routes/LocalizationReviewPage.tsx`
- Create: `apps/control-plane/src/ui/features/review/MediaPreview.tsx`
- Create: `apps/control-plane/src/ui/features/review/SegmentEditor.tsx`
- Create: `apps/control-plane/src/ui/features/review/ApprovalPanel.tsx`
- Test: `apps/control-plane/src/ui/routes/LocalizationReviewPage.test.tsx`

- [ ] Test script load/edit/save, unsaved navigation, stale version conflict,
  timing warning, approval, permission failure and preview expiry.
- [ ] Keep Chinese source read-only and Vietnamese editable per stable segment;
  expose timestamps and validation without changing segment IDs.
- [ ] Save a new immutable version, then approve only the returned version/hash.
  Disable approval while local edits are unsaved or server data is stale.
- [ ] Renew expired preview descriptors through the API and preserve the current
  review position where possible.
- [ ] Match `02-localization-review.png` at all three viewport targets.

### Task 5: Implement Render and Publish Review

**Files:**
- Create: `apps/control-plane/src/ui/routes/RenderPublishPage.tsx`
- Create: `apps/control-plane/src/ui/features/publish/RenderSummary.tsx`
- Create: `apps/control-plane/src/ui/features/publish/MetadataEditor.tsx`
- Create: `apps/control-plane/src/ui/features/publish/DestinationCard.tsx`
- Create: `apps/control-plane/src/ui/features/publish/PublishApprovalDialog.tsx`
- Test: `apps/control-plane/src/ui/routes/RenderPublishPage.test.tsx`

- [ ] Test render status/preview, metadata versions, independent destinations,
  approval invalidation, partial success, retry, quota and rights-revoked states.
- [ ] Present render hash/version and metadata hash/version in the confirmation;
  require typed confirmation or explicit checkbox specified by the UI design.
- [ ] State clearly that YouTube uploads private and Facebook follows the
  configured approved boundary.
- [ ] Never infer aggregate success when one destination fails; make retry
  destination-specific.
- [ ] Match `03-render-publish-review.png`.

### Task 6: Add timeline, settings surfaces and resilient feedback

**Files:**
- Create: `apps/control-plane/src/ui/features/activity/ActivityTimeline.tsx`
- Create: `apps/control-plane/src/ui/routes/SettingsPage.tsx`
- Create: `apps/control-plane/src/ui/components/ToastRegion.tsx`
- Test: `apps/control-plane/src/ui/features/activity/ActivityTimeline.test.tsx`
- Test: `apps/control-plane/src/ui/routes/SettingsPage.test.tsx`

- [ ] Render actor, timestamp, action, version and safe error codes without
  exposing tokens, source URLs or host paths.
- [ ] Settings is read-only for capability/limit/provider health unless the
  backend exposes an authorized mutation; do not create browser secret fields.
- [ ] Announce mutation progress/results through an ARIA live region; keep error
  messages actionable and stable long enough to read.
- [ ] Test retry/cancel confirmation and duplicate-click idempotency.

### Task 7: Perform functional, accessibility and visual QA

**Files:**
- Create: `apps/control-plane/e2e/dashboard.spec.ts`
- Create: `apps/control-plane/e2e/localization-review.spec.ts`
- Create: `apps/control-plane/e2e/render-publish.spec.ts`
- Create: `apps/control-plane/e2e/accessibility.spec.ts`
- Create: `apps/control-plane/e2e/visual.spec.ts`
- Create: `artifacts/ui/.gitkeep`
- Modify: `docs/implementation/status.md`

- [ ] Run browser flows against deterministic API fixtures plus one local Worker
  integration environment.
- [ ] Verify keyboard-only use, visible focus, correct labels, contrast, zoom at
  200%, reduced motion and no horizontal page scroll at 1024 px.
- [ ] Capture all three screens at all three target viewports and compare with
  approved references; document intentional spec-driven differences.
- [ ] Check console/network for errors, failed requests and media routed through
  the Worker.
- [ ] Run `pnpm --filter control-plane test`,
  `pnpm --filter control-plane typecheck`, and
  `pnpm --filter control-plane e2e`.

## Phase exit criteria

- All three approved screens are implemented in English and visually verified.
- Version-bound approval and independent destination behavior are clear and
  tested.
- Accessibility and responsive suites pass at every target viewport.
- No UI bundle, log, screenshot or form contains a provider secret or host path.

