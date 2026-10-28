# Product Audit Report: Chinese Animation Localization MVP

**Author role:** Senior Product Manager (feature ownership review)
**Date:** 2026-10-26
**Baseline:** `main` at `a4ac82d` plus uncommitted Facebook publishing work
**Sources reviewed:**
- Product spec `2026-07-24-chinese-animation-localization-mvp-design.md`
- UI spec `docs/superpowers/specs/2026-07-24-operations-console-ui-design.md` and the 3 mockups
- Master plan and phase 01–08 plans
- `docs/implementation/status.md`
- PoC gate artifacts in `artifacts/pocs/`
- All control-plane, contracts and processing-agent source and tests

---

## 1. Executive summary

The project has strong engineering foundations but no working product yet.

The strong parts are these:
- atomic job leasing;
- hashed lease tokens;
- version-bound approvals;
- an append-only audit trail;
- SSRF-safe HTTPS ingest;
- a rigorous YouTube resumable-upload adapter.

None of this adds up to a usable end-to-end workflow for the single administrator:

1. **The pipeline cannot run end to end.** A job is created, but:
   - its translation claim always fails the rights check;
   - script approval never advances the job to voice generation;
   - publishing is never wired into the runtime.
2. **The administrator has no product surface.** There is no UI code at all, and no API for:
   - listing or reading jobs;
   - editing transcripts or translations;
   - a glossary;
   - settings;
   - alerts;
   - sources or rights.
3. **The core differentiator of the spec is unbuilt.** That is rights governance: creator sources, permission records with scopes and expiry, and revocation. Today, "rights" is a hash of a free-text string.
4. **The risk-gate process was bypassed.** All 9 mandatory PoC gates are `BLOCKED`, and 0 are `PASS`. The master plan forbids starting a later phase before the previous exit gate is `PASS`, yet phases 03–06 were built. Unknowns remain about the 60-minute OCI benchmark, Vietnamese Azure voice availability, the Gemini free tier and YouTube audit status. They could invalidate parts of what was built.

**PM verdict:** Pause new provider integrations (Facebook, OCR, adapters). Sequence the next work as:
1. Unblock the critical path.
2. Build the minimum admin product surface: rights, jobs, review and preview.
3. Execute the guaranteed-path PoCs on real infrastructure.
4. Only then resume publishing.

---

## 2. How this was verified

| Check | Result |
|---|---|
| Control-plane unit/integration tests (vitest, mocked D1) | **69 / 69 pass** |
| Contract tests | **19 / 19 pass** |
| Processing-agent tests (`uv run --offline pytest -q`) | **201 pass, 1 fail**: `test_facebook.py::test_explicit_start_rate_limit_retries_within_bound`, in the uncommitted Facebook work |
| PoC gates (`artifacts/pocs/*.json`) | **0 PASS / 9 BLOCKED** (access-lease, azure-speech, facebook, gemini, local-fixtures, manual-ingest, oci-media, preview, youtube) |
| Native D1 / Workerd / OCI ARM64 / real providers | **Never exercised**: local macOS 12.6 can't run Wrangler, and no credentials or hosts are available |
| Frontend (Vite/Preact) | **No code exists** |

**Caveat:** The tests prove each unit works in isolation against mocks. They do not prove the product works. Several of the critical defects below pass every existing test, because no test exercises the full job lifecycle across both planes.

---

## 3. Feature scorecard

Legend: ✅ Implemented · 🟡 Partial · ❌ Missing · 🔴 Implemented but broken or wrongly designed

| # | Feature area | Spec § | Control plane | Agent | UI | PM status |
|---|---|---|---|---|---|---|
| 1 | Creator sources & rights records | 6 | ❌ | ❌ | ❌ | **Missing (core)** |
| 2 | Rights scope checks (destination, monetization, AI processor, expiry) | 6, 18 | 🔴 | 🟡 | ❌ | **Redesign** |
| 3 | Rights revocation effect | 6 | 🔴 | – | ❌ | **Redesign** |
| 4 | Manual import: creator HTTPS URL | 5.4 | 🟡 | ✅ (SSRF safe; redirects rejected) | ❌ | Partial |
| 5 | Manual import: OCI inbox file | 5.4 | 🟡 (no way to list inbox files) | 🟡 | ❌ | **Redesign** |
| 6 | Import guards (2/day, duration, disk 80%, benchmark switch) | 10, 17 | ❌ | ❌ (5 GB floor, not 20 GB) | ❌ | **Missing** |
| 7 | Discovery / inbox / Bilibili / Douyin adapters | 5.3, 7.1 | ❌ | ❌ | ❌ | Missing (optional gate) |
| 8 | Inbox metadata translation | 4.1, 7.1 | ❌ | ❌ | ❌ | Missing |
| 9 | Job lifecycle & state machine | 7.2 | 🔴 (stalls after script approval) | 🟡 | ❌ | **Redesign** |
| 10 | Review-state resolution (TIMING / MANUAL review) | 7.2, 9.3 | ❌ | – | ❌ | **Missing** |
| 11 | Job list / job detail / timeline | UI 4 | ❌ (only `/events`) | – | ❌ | **Missing** |
| 12 | Transcription (whisper.cpp) | 9.1 | – | ✅ | – | Done (unbenchmarked) |
| 13 | OCR (optional) | 9.2 | – | ❌ | – | Missing (deferrable) |
| 14 | Translation (Gemini) | 8 | – | 🔴 (no chunking, no glossary, no schema) | – | **Redesign** |
| 15 | Glossary | 4.1, 8 | ❌ | ❌ (not passed to Gemini) | ❌ | **Missing (core)** |
| 16 | Transcript/translation editing & script versions | 7.2, UI 4 | 🔴 (hashes only, no content) | – | ❌ | **Redesign** |
| 17 | Metadata generation (YT title/desc/tags, FB caption, hashtags, attribution) | 8 | ❌ | ❌ (validators only) | ❌ | **Missing** |
| 18 | Voice (Azure TTS) | 9.3 | ❌ (quota service unused) | 🔴 (pitch-shifting speed-up, 16 kHz) | ❌ | **Redesign** |
| 19 | Voice settings & voice selection | 9.3 | ❌ | ❌ (single env voice) | ❌ | Missing |
| 20 | Mixing options (mute / low gain / clean M&E) | 9.4 | ❌ | ❌ (hard-coded 0.18) | ❌ | Missing |
| 21 | Subtitles | 9.5 | – | 🟡 (SRT ok; no ASS, no 9:16 safe area) | – | Partial |
| 22 | Watermark | 9.5 | – | 🟡 (fixed 30,30; no scaling or opacity) | – | Partial |
| 23 | Render versions | 7.2 | 🔴 (browser can create a render; not linked to agent artifact) | ✅ | ❌ | **Redesign** |
| 24 | Preview playback | 10 | 🔴 (one 5-min token, never renewable) | 🔴 (no auth_request, path mismatch) | ❌ | **Redesign** |
| 25 | Publish approval | 7.2, 7.4 | 🔴 (trusts client hash, overwrites destination state, no TZ handling) | – | ❌ | **Redesign** |
| 26 | YouTube publishing | 11.1 | ❌ (no coordinator) | 🟡 (upload solid; no thumbnail/captions/publishAt/quota) | ❌ | Partial, not wired |
| 27 | Facebook publishing | 11.2 | ❌ | 🔴 (uncommitted; 1 failing test, retry bugs) | ❌ | **Fix before commit** |
| 28 | Retention & cleanup (day-5 warn, day-7 expire) | 10 | 🔴 (`retention_deadline` never written) | ❌ | ❌ | **Redesign** |
| 29 | Error taxonomy & retry policy | 14 | ❌ | ❌ | – | Missing |
| 30 | Alerts / notifications / agent heartbeat | 14, 16 | ❌ | ❌ | ❌ | **Missing** |
| 31 | Quotas (Azure, YouTube, Gemini, D1) | 17 | 🟡 (service exists, unused) | ❌ | ❌ | Missing |
| 32 | Settings (voice, watermark, brand, gain, limits) | 12 | ❌ | ❌ | ❌ | Missing |
| 33 | Auth (Access JWT, service token, CSRF) | 15 | 🔴 (roles claim, CSRF origin) | – | – | **Redesign** |
| 34 | Secrets in OCI Vault | 5.2, 15 | – | ❌ (env file) | – | Missing |
| 35 | Audit trail / idempotency / leasing | 7.4, 12, 13 | ✅ (idempotency not atomic) | ✅ | – | Done (minor fix) |

---

## 4. Critical defects (P0 — block any end-to-end run)

These are not missing features. Built code here does not work as a product, even though its unit tests pass. Each was confirmed in source.

| ID | Defect | Evidence | User impact |
|---|---|---|---|
| **P0-1** | **The pipeline stalls after script approval.** Approving a script records the approval but never moves the job `WAITING_SCRIPT_REVIEW → GENERATING_VOICE`. The agent has no handler for `WAITING_SCRIPT_REVIEW`. | `approval-service.ts` (no transition); `__main__.py` handlers; no writer of `GENERATING_VOICE` in `apps/control-plane/src` | The admin clicks "Approve script" and nothing happens, forever. |
| **P0-2** | **Translation always fails the rights check.** The TRANSLATING claim requires `rights_metadata_json.allowedProcessors ∋ "GOOGLE_GEMINI"`, but import only writes `{evidenceHash}`. The throw happens after the claim UPDATE, so the job re-leases in a loop. | `lease-service.ts:154-163`; `job-service.ts:98,112` | Every job dies at translation and silently loops. |
| **P0-3** | **CSRF blocks all browser mutations.** The Origin is checked against `PREVIEW_ORIGIN` (the OCI preview host), not the admin app origin, and no endpoint issues CSRF tokens. | `index.ts:62-70`; `csrf.ts` `.issue()` only in tests | Once a UI exists, no button that changes data will work. |
| **P0-4** | **Access auth requires a custom `roles` claim.** Standard Cloudflare Access JWTs don't carry one. The agent check also expects `sub`, which may be empty for service tokens. This also introduces a roles model the spec excludes. | `access-auth.ts:126-133, 231` | The admin may be locked out on first deploy. |
| **P0-5** | **Preview is unusable.** A single 5-minute token is minted when the render completes. There is no endpoint to mint a fresh one, Nginx has no `auth_request`, and Nginx serves `/mnt/localizer/workspaces/` while the agent writes to `/var/lib/localizer-agent/workspace`. | `stages.py:479`; `localizer-preview.conf`; `.env.example:3` | The admin can never watch the render they are asked to approve. |
| **P0-6** | **Retention never fires.** `retention_deadline` is read by the sweep but never written. There is also no agent handler for `DELETE_EXPIRED`. | `retention.ts:126`; `jobs.ts:112`; no writer | The disk fills, and the 7-day promise in the UI is false. |
| **P0-7** | **Publishing is not wired.** No PUBLISH handler, no destination coordinator, no Vault secret reader, and `transitionDestination` has no caller. | `__main__.py`; `job-service.ts:195` | "Approve and publish" does nothing. |
| **P0-8** | **Re-import crashes.** `UNIQUE(source_provider, external_id)` returns an unhandled 500. The spec requires "new import" to reprocess an expired job. | `0001_initial.sql:22` | An expired or failed video can never be retried from scratch. |

---

## 5. Features to ADD (missing from the product)

Priority: **MVP-P1** is required for the first usable release, **MVP-P2** is required before production, and **Post-MVP** comes after the MVP.

### 5.1 MVP-P1: the minimum usable product

**A1. Rights & creator source management (spec §6) — the core differentiator**
- Add tables: `creator_sources`, `source_permissions`, with the states `PENDING_PERMISSION / ACTIVE / PAUSED / EXPIRED / REVOKED / BLOCKED`.
- Add a structured permission form for the admin, with:
  - evidence type and location;
  - effective and expiry dates;
  - allowed destinations;
  - allowed transformations (translation/dubbing, subtitles, watermark);
  - monetization;
  - download permission;
  - named AI processors (Gemini, Azure);
  - attribution text.
- Imports must select a source. The rights snapshot is copied onto the job, which fixes P0-2 structurally.
- Add an endpoint to revoke or pause a source. Revocation must take effect immediately at claim time for all job types, not via a daily sweep. Published content is flagged for manual review, not excluded (the current sweep excludes `PUBLISHED`, the opposite of the spec).
- Add a daily expiry reminder: "Permission for X expires in 14 days".
- *Acceptance:* the spec §18 "Source and rights" criteria are automated end-to-end.

**A2. Job read model and Jobs screen**
- Add `GET /jobs` (filter by state, paginated) and `GET /jobs/:id`. The detail must include:
  - state, step and version;
  - script and render versions;
  - preview availability;
  - destinations;
  - retention deadline;
  - last error code.
- Without these, the admin cannot even get the `expectedVersion` that cancel and retry require.

**A3. Script review content model**
- Add tables: `transcript_segments`, `translation_segments`, `metadata_drafts`. The agent writes segments; the admin edits Vietnamese text per immutable segment ID.
- Saving creates a new script version computed **server-side**. The client never supplies the content hash; today it does.
- Approval moves the job to `GENERATING_VOICE` (fixes P0-1).

**A4. Glossary**
- Add a `glossary_terms` table with CRUD. Each term has: Chinese term, Vietnamese rendering, category (name / cultivation level / place / form of address) and notes.
- Pass the glossary to Gemini, and build a consistency validator. The mockup already shows "Inconsistent term: 武艺, 2 occurrences".
- See also R3: scope the glossary per series.

**A5. Metadata generation**
- Generate:
  - a faithful Vietnamese title and description;
  - a YouTube title (≤100 chars), description (≤5000 bytes) and tags;
  - a Facebook caption and hashtags;
  - the **attribution block from the rights record**, which is mandatory and locked.
- Keep three versions: `source_*`, `translated_*`, `approved_*`.

**A6. Review-state resolution actions**
- For `TIMING_REVIEW_REQUIRED`: show the offending groups (source window vs synthesized duration). Let the admin shorten or resegment, then save a new script version and approve. The job re-enters voice generation.
- For `MANUAL_REVIEW_REQUIRED`: show the reason, and offer "Resume step", "Restart from step X" or "Cancel".
- Retry must **resume the failed step**, as the spec requires. Today it restarts from `QUEUED` and bypasses the state machine.

**A7. Renewable preview access**
- Add `POST /jobs/:id/renders/:v/preview-token`. It mints a fresh 5-minute, render-bound token on demand, with silent renewal while the review page is open.
- Wire Nginx `auth_request` to an agent-local authorizer, and unify the workspace path.

**A8. Publish coordinator**
- Add an agent `DESTINATION_UPLOAD` handler that reads the approved destination set and runs each adapter independently.
- Add per-destination status reporting, and retry that targets one failed destination.

**A9. Import guards and the production switch**
- Enforce:
  - 2 imports per day;
  - the maximum duration (from the benchmark);
  - disk ≥80% rejects imports;
  - ≥20 GB free kept for processing;
  - a global "production imports enabled" flag that stays off until the benchmark passes.
- In the UI, the `Import` button is disabled and shows the reason (UI spec §4 already requires this).

**A10. Alerts and agent health**
- Add an agent liveness heartbeat. The dashboard already designs "OCI agent heartbeat missed — 6 min ago".
- Add a persistent alert table with acknowledgement. Alerts are raised for:
  - quota warnings;
  - disk pressure;
  - rights expiry;
  - failed destinations;
  - adapter blocked.

### 5.2 MVP-P2: required before production

**A11. Error taxonomy and retry policy (§14).** Add the 12 error categories to the completion contract. The agent reports failures instead of crashing the loop and waiting for lease expiry. Retries use backoff with jitter, honor `Retry-After`, cap at 5 attempts per step, and never retry auth/CAPTCHA/rights errors. Today, a deterministic error such as `SubtitleError` crash-loops.

**A12. Quota wiring.**
- Azure character counting before submission, with a warning at 80% and a block at 95%.
- The YouTube 100 uploads/day bucket.
- Gemini rate limits.
- D1 reads and writes.
- Show all of these as real meters in the dashboard rail.

**A13. Settings.** Voice selection (from a cached Azure `list voices`), SSML rate/pitch defaults, watermark asset/position/opacity, brand name, the default mix mode, and limits.

**A14. Mixing modes.** Mute, low gain (configurable) and a creator-supplied clean M&E track (§9.4).

**A15. YouTube completeness.** Add thumbnail, caption track, `publishAt` scheduling while private, language, the made-for-kids flag and quota tracking. Make processing polling last for hours, not about 10 minutes.

**A16. Day-5 retention warning plus an "approaching expiry" filter** on the dashboard.

**A17. Secrets via OCI Vault** with an instance principal. Today they live in a systemd EnvironmentFile.

### 5.3 New product ideas (proposed; need owner approval)

| Idea | Why it matters | Scope |
|---|---|---|
| **Pre-import cost & time estimate.** "24 min episode → ~18k Azure chars (3.6% of month), ~N Gemini requests, est. processing window" | Free-tier quotas are the real budget. The admin should see the cost before committing an import slot. | MVP-P2 |
| **Subtitle-only mode** (skip TTS) | It avoids Azure quota, timing review and pitch problems. Many Chinese-animation audiences in Vietnam accept vietsub. The job is cheaper, faster and has less to go wrong. | MVP-P2, high value |
| **Uploaded caption track instead of only burned-in subtitles** | YouTube CC is searchable, editable after publish and avoids a re-render. | MVP-P2 |
| **Series / episode model.** Repurpose the unused "Projects" concept as *Series*. | Per-series glossary, character list, metadata template ("{Series} – Tập {N}"), default voice and attribution. This is how the admin actually works: donghua are episodic. | Post-MVP (design the data model now) |
| **Metadata templates** | A consistent title format, hashtags and attribution across episodes. | MVP-P2 |
| **Publishing calendar view** | One view of scheduled YouTube/Facebook releases across jobs. | Post-MVP |
| **Telegram/Zalo or email notification channel** | The spec allows an "optional email". Telegram or Zalo is more practical for a Vietnam-based single operator. | Post-MVP |
| **Rights evidence vault link and contract-expiry calendar** | Proactive renewal before content must be pulled. | Post-MVP |
| **Post-publish health check** (claims, takedown, visibility per destination) | Closes the loop after publishing. Today the system forgets a video once it is uploaded. | Post-MVP |

---

## 6. Features to REDESIGN

### R1. Rights model: from "evidence hash" to a structured permission record
- **Problem:** Rights are a hash of a free-text string (`job-service.ts:98`). They cannot be checked for destinations, monetization, AI processors or expiry. `rights_expires_at` is always NULL, and nothing sets revocation.
- **Redesign:** Use the A1 data model. Each job carries an immutable **rights snapshot**, and every claim re-validates against the *current* source state. That gives instant revocation and makes the AI-processor check structural.

### R2. Job lifecycle orchestration
- **Problems:**
  - No owner advances human-gated states (P0-1).
  - Retry restarts from scratch.
  - There is no lost-lease classification (§13 requires unsafe steps to become `MANUAL_REVIEW_REQUIRED`).
  - The job types diverge from the spec. `IMPORT/LOCALIZE/RENDER/PUBLISH/DELETE_EXPIRED` exist, and `LOCALIZE/RENDER/PUBLISH/DISCOVERY` are dead types that nothing ever creates.
- **Redesign:**
  - Make the control plane the sole owner of human-gated transitions: approvals move the state.
  - Add a per-step `safeToReplay` classification table.
  - Align job types to the spec, or update the spec. Either way, remove the dead enums.
  - Add a cross-plane **lifecycle integration test**: import → preview → publish against a fake provider. This test would have caught P0-1, P0-2, P0-6 and P0-7.

### R3. "Projects" layer
- **Problem:** "Projects" is an extra hierarchy that is not in the spec, carries no business meaning, and adds a required path segment for one admin.
- **Redesign:** Either remove it, or (recommended) **redefine it as Series**. Series becomes the scope of the glossary, metadata templates, default voice and the default rights source. Make the decision now, before UI work hard-codes it.

### R4. Script versioning trusts the client
- **Problem:** `POST /jobs/:id/scripts` accepts client-supplied hashes with no content. The agent's voice claim requires the latest script hash to equal its SCRIPT artifact SHA, which no admin can see. This is fragile and opaque.
- **Redesign:** The server stores segment content and computes the canonical hash. The agent gets the approved script by version. The client only ever echoes back a version and hash it received from the server.

### R5. Render and publish approval integrity
- **Problems:**
  - Render versions can be created by a browser `POST /renders` and are not linked to the agent's render artifact.
  - `metadataHash` is client-supplied and never checked.
  - The metadata body is validated and then discarded.
  - Approval upserts destinations to `READY_TO_UPLOAD` even if they are already `PUBLISHED` or `UPLOADING`.
  - There is no Asia/Ho_Chi_Minh → UTC conversion, and past schedules are not rejected.
  - Facebook never gets `BLOCKED_BY_GATE`.
- **Redesign:**
  - Only the agent creates render versions.
  - Metadata drafts are stored server-side and hashed server-side.
  - Approval goes through the destination state machine and refuses non-initial states.
  - Schedule input is local time plus the zone, converted server-side.
  - Metadata edits invalidate the publish approval.

### R6. Preview architecture
- **Problem:** The token is a one-shot mint at render time (P0-5), and the registry is a local JSON file with no resolver.
- **Redesign:** Tokens are minted on demand by the control plane: render-bound, 5 minutes, renewable while the page is open. Nginx `auth_request` goes to a tiny local authorizer, and the `Referrer-Policy` and log-redaction rules in §15 apply. Revoke all tokens on expiry or rejection.

### R7. Translation pipeline
- **Problems:**
  - The whole transcript is sent in one Gemini request, which will fail on 30–60 minute episodes.
  - No glossary.
  - No 3-segment context.
  - Only `responseMimeType` is set, with no `responseSchema`.
  - No retry or `Retry-After` handling.
- **Redesign:** Chunk at semantic boundaries (sentence/scene), add a context window and the glossary, use a structured schema, and retry with resume per chunk. Partial progress persists so one failed chunk doesn't redo the whole episode against a free-tier quota.

### R8. Voice timing and audio quality
- **Problems:**
  - Speed-up is nearest-sample decimation (`speech/audio.py:37`), which **raises pitch** (a chipmunk voice).
  - Narration is 16 kHz mono.
  - There is no 0.90 lower bound.
  - There is no 12 s sentence grouping.
  - The whole script is re-synthesized on any change.
- **Redesign:**
  - Apply the rate through SSML `<prosody rate>` (Azure-native, pitch-preserving), with FFmpeg `atempo` as a fallback.
  - Request 24 or 48 kHz output.
  - Group by sentence within 12 s.
  - Keep a per-segment synthesis cache, so editing one line only re-synthesizes that group (§9.3, and it saves quota).

### R9. Subtitle rule violations fail the job too late
- **Problem:** Subtitle rule breaches (line length, reading speed) are detected in MIXING, *after* script approval, and fail the job.
- **Redesign:** Run subtitle validation during script review as blocking validation issues. The mockup already has a "Segment too long" issue. Never fail a job for something the admin could have fixed before approving.

### R10. Inbox-file import UX
- **Problem:** The admin must type an opaque `inboxFileId`, but nothing lists inbox files. The spec's `sha256` / `expectedSha256` were also dropped from the contract, so a hash is computed but never compared.
- **Redesign:** The agent scans the inbox and registers files with ID, name, size, hash and probe duration. The import dialog shows a picker. An expected hash, when given, is verified.

### R11. HTTPS ingest rejects redirects
- **Problem:** Any 3xx is rejected (`ingest.py:126`). Creator links from Google Drive, Dropbox and CDNs redirect, so the guaranteed ingest path will fail in practice.
- **Redesign:** Follow up to N redirects, and re-run the full SSRF check on each hop, as the spec already requires.

### R12. Facebook adapter (uncommitted). Fix before committing.
1. A rate limit at upload start is classified as "ambiguous creation". It leaves `creation_pending=True`, so the publication is permanently stuck. This is the failing test.
2. Transient 5xx and network errors during transfer are not retried.
3. If the provider returns the same offset, the loop can run forever.
4. Non-retryable 4xx errors at finish are mislabelled "ambiguous".
5. Unknown `video_status` values, such as `expired`, raise an error.
6. Hashtag validation requires ASCII (`metadata.py:55`), which **rejects Vietnamese hashtags** such as `#hoạthình`. That is a product bug that also affects YouTube tags.

### R13. Auth model
- Drop the `roles` claim requirement and the operator/reviewer/admin roles. The spec is single-admin; validate the email instead.
- Verify the service-token claim shape against current Cloudflare docs (`common_name`).
- Fix the CSRF origin to the admin app origin, and add a token-issuing endpoint.
- Make idempotency atomic (insert-first), so concurrent duplicate clicks don't both mutate and then return a 500.

### R14. Spec and mockup inconsistencies to resolve. These are PM decisions, not code bugs.

| # | Inconsistency | Recommendation |
|---|---|---|
| a | Mockup 02 paginates transcript and translation **independently** (8 rows each). The UI spec says "never paginate the two sides independently". | Use one virtualized, row-synchronized grid. Update the mockup. |
| b | Mockup 01 lists a "YouTube Adapter" and a "Vimeo Adapter" as source health. Neither is in scope. A youtube.com creator URL implies re-uploading YouTube content. | Remove both. Limit source health to Creator URL, OCI Inbox and Bilibili/Douyin (when their gates pass). |
| c | Mockup 03 has "Request extension" for artifact expiry. The spec has a hard 7-day limit with no extension. | Decide. Recommended: allow **one** audited 7-day extension if disk ≥20 GB free, and add it to the spec. |
| d | Mockup 03 has "Download MP4 / captions" links. The spec has no download feature, and media must not go through the Worker. | Allow only via the same Access + token preview path, and record an audit event. |
| e | Mockup 03 shows a Privacy dropdown. The spec forces private staging. | Make it read-only "Private (staging)" with the target visibility chosen at approval. |
| f | Mockup 03 "Reject render": the spec does not define what happens next. | Define a render-rejection loop: reject with a reason, return to script review (new script version) or re-render with new settings. Add an audit event. |
| g | Mockup 02 "Request retranscription" and an editable "Confidence" dropdown: neither is in the spec. Confidence is not even stored by the agent. | Keep retranscription (with a model or language option). Make confidence read-only and agent-sourced, or remove it. |
| h | Mockup 03 shows "Audio mix: Loudness −16.1 LUFS". There is no loudness measurement or normalization in the spec or code. | Add EBU R128 loudness normalization (`loudnorm`) to render. It is cheap and improves quality. |
| i | The step rail has no state for Timing review or Manual review. | Add an inline "Needs your action" state to the rail. |
| j | Mockup 01 shows disk as 2 TB. The real volume is 150 GB (already noted in the UI spec). | Use runtime data only. |
| k | The spec's §12 table names differ from the implementation (`processing_jobs` → `jobs`, `approval_events` → `approvals`, …). | Update the spec to match the code (AGENTS.md requires it). |

---

## 7. Process and delivery issues

1. **Phase gates were bypassed.**
   - The master plan says no phase begins until the previous phase's exit gate is `PASS`.
   - Phases 01–05 are all `BLOCKED`, and 0 of 9 PoCs have passed. Phase 06 is in progress anyway.
   - The highest-risk unknowns are still open:
     - the OCI benchmark can reduce the duration limit or stop the media build entirely (§16);
     - Vietnamese voice availability is unconfirmed;
     - the Gemini free-tier data-use terms are unconfirmed.
   - **Recommendation:** freeze new provider work and run gates 1, 2, 3, 4, 5 and 7 on a real OCI host and in CI.
2. **The blocked environment is a self-inflicted constraint.** Status says D1 cannot run on macOS 12.6. But `.github/workflows/verify.yml` already runs `npm ci`, `db:migrate:local` and pytest on `ubuntu-latest`. Make CI the source of truth for gate status, and record its run ID in `status.md`.
3. **There are no cross-plane tests.** Each side passes its own mocks while the joined system is broken (P0-1, P0-2). Add a contract-level lifecycle test suite as part of the Definition of Done.
4. **Work is effectively on `main`.** Phase work is committed directly to `main`, and Facebook work sits uncommitted on `main`. Move it to `feature/phase-06-facebook`.
5. **Repository hygiene.** `.DS_Store` files are tracked and modified. Add them to `.gitignore` and remove them from the index.
6. **The spec is drifting from the code.** Job types, table names and the "Projects" layer differ from the spec. AGENTS.md requires spec updates when architecture changes.

---

## 8. Recommended roadmap (priority order, no date estimates)

**Milestone 0: Stabilize (unblock the critical path)**
- Fix P0-1 to P0-4, P0-6 and P0-8.
- Fix the Facebook failing test, move it to a feature branch, and don't merge until R12 is fixed.
- Add the cross-plane lifecycle test.
- Make CI the gate-status authority.

**Milestone 1: Run the guaranteed-path PoCs**
- Gates 1 (manual ingest), 2 (Access/lease), 3 (OCI benchmark), 4 (Azure VI voice), 5 (Gemini) and 7 (preview), on real infrastructure.
- Apply only the approved fallbacks for any gate that fails.

**Milestone 2: Minimum usable admin product**
- A1 Rights, A2 Jobs, A3 Script review, A4 Glossary, A5 Metadata, A6 Review resolution, A7 Preview, A9 Guards, A10 Alerts.
- Phase 07 UI for Dashboard, Jobs, Localization Review and Render Review, after the R14 mockup decisions.
- Redesigns R1–R11.

**Milestone 3: Publishing**
- A8 coordinator, A15 YouTube completeness, Facebook after-approval (gate 8), A11 errors/retry, A12 quotas.

**Milestone 4: Production hardening (Phase 08)**
- Vault secrets, recovery runbooks, the 7-day retention scenario, and the security review.

**Deferred (optional gates):** Bilibili/Douyin discovery and the inbox, OCR, and Facebook hidden staging/Reels.

---

## 9. Decisions needed from the product owner

1. Approve freezing provider work until Milestones 0 and 1 are complete?
2. "Projects": remove it, or redefine it as **Series** (recommended)?
3. Retention extension: allow one audited 7-day extension (R14c)?
4. Add **subtitle-only mode** to the MVP scope?
5. Allow audited download of the render and captions (R14d)?
6. Notification channel: email (as in the spec), or Telegram/Zalo?
7. Render-rejection loop semantics (R14f): back to script review, or re-render with new settings?
