# Chinese Animation Localization and Publishing MVP

**Status:** Approved risk-gated design specification (revision 2)  
**Date:** 2026-07-24  
**Owner:** Single administrator  
**Primary timezone:** Asia/Ho_Chi_Minh  

## 1. Purpose

Build a low-cost system that accepts creator-authorized HTTPS URLs or files,
shows them in an inbox, and lets one administrator choose which videos to
import. Optional Bilibili and Douyin adapters can discover authorized creator
metadata only after their proof-of-concept gates pass. Selected videos are
ingested by an Oracle Cloud Infrastructure (OCI) ARM worker, transcribed from
Chinese, translated into Vietnamese, voiced in Vietnamese, rendered with
subtitles and branding, and reviewed. YouTube is uploaded as private before
final approval. Facebook is uploaded only after final approval unless a
separate Meta proof of concept proves a non-public staging state.

The system must not treat public availability as permission to reproduce,
translate, or monetize a video. A source can be activated only after the
administrator records evidence that the creator or rights holder authorized the
intended use.

## 2. Decisions already approved

| Area | Decision |
|---|---|
| Users | One administrator |
| Authentication | Cloudflare Access |
| Web application | Vite + Preact + TypeScript served by Cloudflare Workers Static Assets |
| API | Cloudflare Worker written in TypeScript |
| Database | Cloudflare D1 |
| Processing server | OCI `VM.Standard.A1.Flex`, 2 OCPU and 12 GB RAM |
| OCI application | Python processing agent; no Spring Boot |
| Source architecture | Pluggable `SourceAdapter` interface |
| Guaranteed MVP ingest | Creator-provided HTTPS URL or file delivered through OCI Bastion/SCP |
| Conditional source adapters | Bilibili and Douyin, enabled independently only after PoC |
| Discovery behavior | Detect metadata only; never download automatically |
| Import behavior | Download only after the administrator selects `Import` |
| Speech recognition | Local `whisper.cpp` preferred; faster-whisper allowed only after ARM PoC |
| OCR | PaddleOCR only if speech transcription or available source text is insufficient |
| Translation | Gemini Developer API Free Tier |
| Vietnamese voice | Azure Speech Neural TTS Free F0 |
| Media processing | FFmpeg |
| Watermark | Transparent PNG logo plus brand name, top-left safe area |
| Persistent video storage | None in MVP |
| Preview storage | Temporary OCI filesystem |
| Temporary artifact retention | Seven days from `READY_FOR_PREVIEW`, with warning from day five |
| YouTube staging | Upload as `private` |
| Facebook staging | Unpublished/draft only if confirmed by the selected Meta endpoint and app permissions |
| Publication | Version-bound script approval and final publish approval are required |

## 3. Verified external capability matrix

This matrix distinguishes documented capabilities from PoC requirements. A
capability marked **PoC gate** must not be represented as guaranteed in the UI.

| External dependency | Verified capability | Status and consequence |
|---|---|---|
| Cloudflare Workers | Free plan documents 100,000 requests/day, 128 MB memory, 10 ms CPU for normal requests, 50 subrequests per invocation, and five Cron Triggers per account. Static assets are supported. | **Verified.** Workers perform orchestration and short API calls only; no media processing. |
| Cloudflare Cron Triggers | Scheduled handlers and UTC cron expressions are documented. Free-plan Cron wall time is bounded. | **Verified.** The Cron handler only performs an idempotent D1 enqueue. The OCI agent executes and checkpoints the scan. |
| Cloudflare D1 | Free tier documents 500 MB per database, up to 10 databases, 5 GB total account storage, seven-day Time Travel, 5 million rows read/day, and 100,000 rows written/day. | **Verified.** Store metadata and text only, never video/audio blobs. Index polling/inbox queries and monitor row usage. |
| Cloudflare Tunnel | `cloudflared` creates outbound-only connections, so OCI does not need a publicly routable inbound service. | **Verified.** OCI preview traffic stays behind Access and Tunnel; the outbound agent API is separately protected by Access service authentication. |
| Cloudflare Access | A Worker route can be restricted to selected email addresses. | **Verified.** Exact JWT validation and service-token configuration must follow the current Cloudflare Access guide during implementation. |
| OCI Ampere A1 | Always Free documentation equates the allowance to 2 OCPU and 12 GB RAM for `VM.Standard.A1.Flex`; 200 GB combined boot/block volume is included. Idle Always Free instances may be reclaimed. | **Verified for an MVP, not an SLA.** Use an eligible Linux ARM64 image, a 50 GB boot volume and a 150 GB media volume. Capacity and continued availability are not guaranteed. |
| yt-dlp | The maintained supported-sites list includes Bilibili and Douyin and explicitly warns that listed sites can break when websites change. Bilibili account/space extractors are listed. | **Verified as third-party behavior, not platform authorization.** Both adapters require PoC tests and graceful degradation. |
| Douyin Open Platform | Official APIs document video data for a user who authorized the application and granted the requested scope. The platform documents OAuth and application review. | **Verified but insufficient for the proposed arbitrary creator monitor.** No official download API was found. Monitoring/downloading other creator pages is a **PoC and legal/terms gate**. |
| Bilibili Open Platform | No official, generally available creator-page download API was located in the reviewed official documentation. | **Unverified.** MVP may use yt-dlp only for an authorized source after PoC; creator-supplied files remain the supported fallback. |
| Gemini Developer API | Current pricing documents free-tier input/output for selected Gemini models. Free-tier data may be used to improve Google products. | **Verified.** The exact model must be selected from the live pricing/model page at implementation time and kept configurable. |
| Azure Speech | Free F0 documents 0.5 million Neural TTS characters/month. | **Verified.** Quota is enforced locally before calling Azure. Vietnamese voice availability must be checked with `list voices` during PoC, not hard-coded from memory. |
| whisper.cpp | The project documents CPU-only inference, integer quantization, Linux and ARM-oriented platforms. | **Verified.** Real-time factor on OCI A1 is unknown until benchmarked. |
| PaddleOCR | Official deployment documentation includes ARM CPU deployment paths. | **Verified in principle; PoC required on OCI ARM64.** OCR is optional because it adds CPU and packaging risk. |
| FFmpeg | Official filter documentation supports overlays and filter graphs. | **Verified.** Use an ARM64 FFmpeg build and integration tests for watermark/subtitle safe areas. |
| YouTube Data API | `videos.insert` uploads media and metadata. `private`, `unlisted`, and `public` are documented privacy values. `publishAt` applies only to a never-published private video. OAuth 2.0 is required for writes. Current documentation gives `videos.insert` one unit from a separate 100-upload-calls/day bucket. | **Verified.** Initial upload is always `private`; upload usage is tracked independently. |
| YouTube API project audit | Official documentation states uploads from unverified API projects created after 2020-07-28 are restricted to private viewing. | **Verified blocker for automated public release.** Complete the API compliance audit before relying on automatic transition to public. |
| YouTube processing status | Video resources expose upload and processing states, including failure/rejection reasons. | **Verified.** Deleting the local render is permitted only after the required destinations confirm successful processing. |
| YouTube copyright checks | The Data API exposes some rejection information, but the reviewed public reference does not provide a complete equivalent of every YouTube Studio copyright-check result. | **Partial.** Final approval includes a manual YouTube Studio check; the system must not claim “copyright cleared” from processing success alone. |
| Meta Video API | Official documentation supports publishing video to a Facebook Page using a Page access token obtained by a person with the required Page task. | **Verified.** App review, token, Page task and permission requirements apply. |
| Facebook unpublished Page post | Page feed documentation describes unpublished Page posts that do not appear in the feed. | **Verified for applicable Page post endpoints.** Endpoint compatibility with the chosen video/Reel flow must be tested. |
| Facebook Reels Publishing API | Official documentation confirms a create/upload/publish flow for Page Reels. The reviewed documentation did not establish an indefinite private/draft state equivalent to YouTube Private. | **Blocker/feature gate.** Do not delete the OCI render or promise hidden Facebook staging until PoC confirms the exact Graph API version and endpoint. |

## 4. Scope

### 4.1 Included

- Register creator-provided HTTPS URLs and OCI inbox files.
- Optionally register authorized Bilibili and Douyin creator sources after each
  adapter passes its PoC.
- Store permission evidence metadata and prevent import when permission is
  missing, expired, or outside destination, monetization, transformation or
  third-party-processing scope.
- Periodically discover video metadata for adapters whose PoC gate passed.
- Deduplicate discovered videos by platform and stable source video ID.
- Translate source title and description for the inbox without downloading the
  video.
- Import only after explicit administrator selection.
- Ingest an authorized creator-provided URL/file or download through an enabled
  adapter.
- Transcribe Chinese speech with timestamps.
- Optionally OCR burned-in Chinese text.
- Translate transcript, title, and description into Vietnamese.
- Generate separate YouTube title/description/tags and Facebook caption.
- Maintain a glossary for names, cultivation levels, locations, forms of
  address, and recurring terms.
- Review and edit transcript, translation, metadata, and voice script.
- Generate Vietnamese narration.
- Render Vietnamese subtitles, narration, retained background audio where
  technically feasible, and a top-left watermark.
- Securely preview the temporary render from OCI.
- Upload YouTube privately before final approval.
- Upload Facebook only after final approval unless its hidden-staging gate passes.
- Final approval to publish immediately or schedule.
- Delete temporary media according to confirmed state and retention policy.
- Record an append-only audit trail for approvals and publication actions.

### 4.2 Excluded from MVP

- Multiple users, roles, invitations, or team review.
- SaaS tenancy and billing.
- Automatic download of every newly discovered video.
- Automatic publication without explicit script and publish approvals.
- Browser automation for YouTube, Facebook, Bilibili, or Douyin.
- Circumvention of DRM, access controls, CAPTCHA, or platform restrictions.
- Downloading content without documented rights.
- Kuaishou.
- Instagram and TikTok publishing.
- Voice cloning.
- Multi-actor lip-synchronized dubbing.
- Long-term video/audio storage.
- Browser-based large-file upload.
- GPU processing.
- Guaranteed support for videos longer than 60 minutes before benchmark results.

## 5. System architecture

### 5.1 Cloudflare control plane

The control plane is one Cloudflare Workers project:

- Preact single-page application compiled by Vite and served as static assets.
- TypeScript REST API.
- D1 binding.
- Cron Trigger for source discovery.
- Cloudflare Access identity headers/JWT verification.
- Worker secrets only for internal signing/encryption material that the control
  plane itself uses.

Workers must not proxy full media files. Preview playback goes through a
separate Access-protected hostname routed by Cloudflare Tunnel to Nginx on OCI.
Use three Access applications/policies:

- the administrator application, restricted to the configured email;
- an agent API hostname with a `Service Auth` policy and renewable service token;
- the preview hostname, restricted to the administrator and protected again by
  render-bound preview authorization at the origin.

The Worker validates the Access JWT signature and the expected `iss`, `aud`,
`exp` and administrator email on every administrator request. Agent routes
accept only the service identity and expose no administrator or publication
approval operation.

### 5.2 OCI processing plane

OCI runs ARM64 Linux with:

1. `processing-agent`: Python service that leases jobs, executes the pipeline,
   updates progress, and invokes external APIs.
2. `preview-server`: Nginx configured for HTTP Range requests and internal
   authorization.
3. `cloudflared`: outbound-only tunnel connector.

The agent exposes no public port. It polls or calls a narrowly scoped Worker API
using a rotating service credential. The service credential cannot access
administrator endpoints or publication secrets not required by the agent.

The agent owns calls to yt-dlp, Gemini, Azure, YouTube and Meta because it owns
the media pipeline and upload stream. Provider API keys, source cookies, OAuth
refresh tokens and the Access service token are stored in OCI Vault and accessed
with an instance principal. They are never stored in D1 or copied into the
frontend. OAuth connection is a one-time administrator bootstrap operation in
the MVP; token refresh and rotation are performed by the agent.

The media workspace is a dedicated 150 GB block volume mounted below
`/mnt/localizer`. The 50 GB boot volume contains no job artifacts. Deployment
configuration and systemd units must be reproducible because an idle Always
Free instance may be reclaimed; D1 and OCI Vault remain the recovery sources.

### 5.3 Source adapters

```typescript
interface SourceAdapter {
  validateCreator(input: CreatorSourceInput): Promise<CreatorProfile>;
  listRecentVideos(
    creator: CreatorSource,
    cursor?: string
  ): Promise<SourceVideoPage>;
  getVideoMetadata(sourceVideoId: string): Promise<SourceVideoMetadata>;
  download(
    sourceVideo: SourceVideo,
    destination: string
  ): Promise<DownloadedMedia>;
  healthCheck(): Promise<AdapterHealth>;
}
```

`SourceAdapter` is an optional automation boundary, not the guaranteed ingest
path. Each adapter is enabled independently after its platform PoC passes.

Adapters return normalized data and platform-specific evidence:

- source video ID;
- canonical URL;
- creator ID and display name;
- original title and description;
- thumbnail URL;
- publication timestamp;
- duration when available;
- adapter version;
- raw metadata checksum.

`BilibiliSourceAdapter` and `DouyinSourceAdapter` run in the OCI agent because
yt-dlp, cookies, media probing, and platform-specific libraries do not belong in
the Worker runtime. The Worker Cron creates `DISCOVERY_SCAN` jobs; the OCI agent
performs the scan and writes normalized results through the control API.

An adapter can be `HEALTHY`, `DEGRADED`, `AUTH_REQUIRED`, `BLOCKED`, or
`DISABLED`. CAPTCHA, HTTP 403/429, changed extraction logic, or invalid cookies
must stop automatic retries and surface an administrator alert.

### 5.4 Guaranteed manual ingest

Manual ingest accepts one of these locators:

```typescript
type IngestLocator =
  | {
      kind: "CREATOR_HTTPS_URL";
      url: string;
      expectedSha256?: string;
    }
  | {
      kind: "OCI_INBOX_FILE";
      opaqueFileId: string;
      sha256: string;
    };
```

The URL must be HTTPS, must not resolve to loopback, link-local or private
network addresses, and is fetched by the agent only after the rights check. URL
redirects are revalidated to prevent SSRF.

For local files, the administrator uses OCI Bastion with SCP/SFTP to place a
file in `/mnt/localizer/inbox`. The agent hashes the file and registers an
opaque inbox ID. API clients never submit or receive an arbitrary filesystem
path. Browser upload is excluded because Cloudflare Free limits a request body
to 100 MB and typical source videos exceed that limit.

## 6. Rights and source governance

Each source has:

- rights holder/creator name;
- canonical creator URL;
- evidence type and evidence location;
- permission effective and expiry dates;
- allowed territories;
- allowed destinations;
- translation/dubbing right;
- right to add subtitles and watermark;
- monetization right;
- permission to download or receive source files;
- permission to send transcript and metadata to named third-party AI providers;
- attribution requirements;
- revocation/contact notes.

Allowed source states:

```text
PENDING_PERMISSION
ACTIVE
PAUSED
EXPIRED
REVOKED
BLOCKED
```

Only `ACTIVE` can scan and import. A rights change immediately disables new
imports and publication. Previously uploaded content is flagged for manual
review; it is not automatically deleted.

Gemini Free Tier may use submitted content to improve Google products. A job
cannot send content to Gemini unless the permission record explicitly allows
that processing and identifies Gemini/Google as a processor. Otherwise the job
stops at manual translation; it does not silently send content to another
provider.

The preferred production path remains creator-supplied clean files. If an
adapter fails or platform terms do not permit downloading, the source changes
to manual file delivery without changing the downstream pipeline.

## 7. Workflow and state model

### 7.1 Source discovery

```text
DISCOVERED
TRANSLATING_METADATA
INBOX_READY
IGNORED
IMPORT_SELECTED
```

Discovery stores metadata only. Inbox actions:

- `Import`: verify permission, duration and disk capacity; create processing job.
- `Ignore`: suppress the same stable source video ID.
- `Open source`: open canonical source.
- `Refresh metadata`: re-fetch without downloading.

### 7.2 Processing

```text
QUEUED
INGESTING
PROBING
EXTRACTING_AUDIO
TRANSCRIBING
OCR_OPTIONAL
TRANSLATING
WAITING_SCRIPT_REVIEW
GENERATING_VOICE
MIXING
RENDERING
READY_FOR_PREVIEW
WAITING_PUBLISH_APPROVAL
TIMING_REVIEW_REQUIRED
MANUAL_REVIEW_REQUIRED
COMPLETED
FAILED
CANCELLED
EXPIRED
RIGHTS_BLOCKED
```

The first approval confirms transcript, translation, title, description and
voice script before expensive voice generation/rendering. It records
`script_version` and a canonical content hash. Any edit to approved transcript,
translation, metadata, glossary application or voice settings increments the
version and invalidates the active script approval.

The final approval records the exact `render_version`, render checksum,
destination metadata hash, destination set and optional UTC schedule. Any
render-affecting or destination-metadata edit invalidates that approval.
Selecting `Import` is an audited intent event, not one of the two approvals.

### 7.3 Destination state

YouTube and Facebook are tracked independently:

```text
NOT_REQUESTED
BLOCKED_BY_GATE
READY_TO_UPLOAD
UPLOAD_QUEUED
UPLOADING
PROCESSING
STAGED_HIDDEN
SCHEDULED
PUBLISHED
FAILED
REJECTED
MANUAL_ACTION_REQUIRED
CANCELLED
```

Overall publication proceeds only after the administrator approves the exact
render and destination metadata.

`STAGED_HIDDEN` is always valid for a successfully processed YouTube private
upload. It is invalid for Facebook until the current Meta endpoint, permission
set, API version and an external visibility test pass the hidden-staging PoC.

### 7.4 Control and agent API invariants

- Every import, approve, publish, schedule, retry and delete mutation requires
  an `Idempotency-Key`; the result is stored and replayed for duplicate keys.
- Script approval input includes `scriptVersion` and `contentHash`.
- Publish approval input includes `renderVersion`, `metadataHash`,
  `destinations` and `scheduleAtUtc`.
- Schedule input is converted from `Asia/Ho_Chi_Minh` to UTC, stored in UTC and
  rejected if it is not in the future.
- Agent claim, heartbeat and completion calls carry the current lease token.
  Only its hash is stored; a stale token cannot update a job.
- External upload identifiers and resumable session identifiers are persisted
  before retry so an ambiguous network failure does not create a duplicate
  publication.

## 8. Translation and metadata requirements

The system preserves three versions:

- `source_*`: exact original metadata;
- `translated_*`: literal/faithful Vietnamese draft;
- `approved_*`: administrator-approved publication text.

Required outputs:

- Vietnamese faithful title;
- Vietnamese faithful description;
- YouTube title, maximum 100 characters;
- YouTube description, within the documented 5,000-byte limit;
- YouTube tags, within the documented aggregate limit;
- Facebook caption;
- hashtags;
- attribution block required by the rights record.

Transcript translation is chunked at semantic boundaries, not arbitrary byte
boundaries. Each request includes the glossary, recurring characters and a
previous-context window of up to three segments. Timestamps and segment IDs are
immutable across translation.

The provider response must be structured JSON and schema-validated. Invalid or
missing segment IDs fail the chunk; they do not silently shift subtitles.

The model identifier is configuration, not a hard-coded design constant. Setup
selects a generally available model that has a live Free Tier and structured
output support, records its exact model/version, and reruns the translation PoC
before changing it. Preview/experimental models are not production defaults.

## 9. Audio and video processing

### 9.1 Speech recognition

- Normalize source audio to a whisper-compatible PCM format with FFmpeg.
- Prefer `whisper.cpp` with a quantized small model for ARM64.
- Select Chinese explicitly when confidence/autodetection is unreliable.
- Store segment start/end times, original text and confidence where available.
- Benchmark 10-, 30-, and 60-minute representative clips before setting SLA.

### 9.2 OCR

OCR is disabled by default. It is enabled when:

- the source contains important text not spoken aloud;
- speech is missing or unusable; or
- the administrator explicitly requests it.

OCR results remain a separate track and are never merged blindly into the
speech transcript.

### 9.3 Voice

- Azure voice list is queried during setup and cached.
- The administrator selects one Vietnamese Neural voice.
- SSML controls rate, pitch, pauses and pronunciation.
- The system counts billable characters before submission.
- At 80% of the configured quota it warns; at 95% it blocks new synthesis.
- Voice audio is generated by segment to permit targeted correction.

Speech segments are grouped at sentence or strong punctuation boundaries, with
a maximum group window of 12 seconds. Natural-rate TTS is generated first.
Timing correction may adjust speech rate only within `0.90`–`1.10`. A group
that exceeds its source window by more than 250 ms or overlaps the following
group moves the job to `TIMING_REVIEW_REQUIRED`; it is never compressed further
automatically. The administrator must shorten/resegment the script and approve
a new script version.

### 9.4 Mixing

MVP supports narration over attenuated source audio. Reliable separation of
dialogue from background music is not guaranteed and is not an acceptance
criterion. If the original dialogue remains distracting, the administrator can
choose one of:

- mute source audio;
- keep source at a configured low gain;
- supply a clean music/effects track from the creator.

### 9.5 Subtitles and watermark

Subtitles:

- Vietnamese UTF-8;
- generated as SRT and optionally ASS for styled hard subtitles;
- safe-area presets for 16:9 and 9:16;
- maximum 42 grapheme clusters per line and two lines;
- minimum one-second display time and target reading speed no greater than
  20 grapheme clusters per second;
- visual collision tests against platform controls.

Watermark:

- transparent PNG containing logo and brand name;
- top-left;
- default 30 px horizontal and vertical margin at 1080p, scaled by resolution;
- target width 180–220 px at 1080p;
- default opacity 0.82;
- configurable preview;
- one shared asset for YouTube and Facebook in MVP to avoid a second render.

## 10. Preview without R2

The final render remains on OCI. Nginx serves it with HTTP Range support only
after an authorization subrequest validates:

- Cloudflare Access identity;
- job ownership (single administrator);
- preview token with a maximum five-minute lifetime;
- token expiry;
- job state.

Preview URLs are not permanent. The Worker never relays the media body.

Default retention:

| Artifact | Retention |
|---|---|
| Ingested source | Keep through publish/reject or job expiry |
| Extracted audio | Keep through publish/reject or job expiry |
| Generated voice segments | Keep through publish/reject or job expiry |
| Final render awaiting approval | Keep until decision, maximum 7 days from `READY_FOR_PREVIEW` |
| Failed job artifacts | Keep up to 7 days from terminal failure |
| Transcript, translation, metadata, audit events | Persist in D1 |

The dashboard warns continuously from day five. At the end of day seven, an
undecided job moves to `EXPIRED`, an audit event is appended, active preview
tokens are revoked, and any incomplete destination moves to
`MANUAL_ACTION_REQUIRED` before cleanup begins. Reprocessing an expired job
requires a new import and fresh rights validation.

Disk protection:

- reject new imports at 80% utilization;
- emergency cleanup only of expired temporary files;
- keep at least 20 GB free for active processing;
- never delete a non-expired artifact or the sole retryable render before its
  job expires;
- surface disk warnings in the dashboard.

## 11. Publishing

### 11.1 YouTube

1. Use OAuth 2.0 and resumable upload.
2. Upload with `privacyStatus=private`.
3. Set approved title, description, tags, language and audience flags.
4. Upload thumbnail and caption track if enabled.
5. Poll upload and processing status.
6. Require manual YouTube Studio copyright/check review.
7. On final approval, update to `public` or set `publishAt` while still private.

The application must display an explicit precondition: an unverified API project
may remain private-only until Google's audit is completed.

The agent records resumable session and video IDs before retrying ambiguous
responses. It tracks the dedicated 100-upload-calls/day quota separately from
general YouTube API units. Scheduling accepts administrator local time but
stores and submits UTC; a past or non-private `publishAt` request is rejected.

### 11.2 Facebook Page

The guaranteed MVP behavior is upload-after-approval: the system does not send
the media to Facebook until the administrator approves the exact render,
caption and destination. The upload may be immediate or scheduled only if the
selected current Page endpoint documents and passes scheduling tests.

Hidden staging is an optional integration PoC using the current Meta Graph API:

1. obtain the correct Page access token and Page task/permissions;
2. upload a test video to a non-public staging state using the chosen Page video
   endpoint;
3. confirm visibility using a separate non-admin account;
4. confirm whether the same behavior applies to the Page's Reel flow;
5. confirm transition from staged to published;
6. document token renewal and app review requirements.

Until all checks pass:

- Facebook hidden staging remains disabled;
- the local final render is retained under the seven-day policy;
- the system may upload YouTube Private for review;
- Facebook upload happens only after final approval.

The system must never label a Facebook item “private” unless the API response
and external visibility test prove that state.

## 12. Data model

Core tables:

- `settings`
- `creator_sources`
- `source_permissions`
- `source_scan_runs`
- `source_videos`
- `ingest_artifacts`
- `processing_jobs`
- `job_attempts`
- `transcript_segments`
- `translation_segments`
- `glossary_terms`
- `metadata_drafts`
- `voice_segments`
- `render_artifacts`
- `destination_publications`
- `approval_events`
- `audit_events`
- `idempotency_keys`
- `quota_usage`
- `adapter_health_events`

Important unique constraints:

- `(platform, platform_creator_id)` for creator sources;
- `(platform, platform_video_id)` for source videos;
- one active idempotency result per `(actor_id, operation, idempotency_key)`;
- one current publication record per `(job_id, destination)`;
- one active `SCRIPT_APPROVED` event per script version;
- one active `PUBLISH_APPROVED` event per render version and metadata hash.

Media paths stored in D1 are relative opaque identifiers, never arbitrary
filesystem paths supplied by a client.

`processing_jobs` stores the job type, current status, step, script version, render
version, lease owner, lease-token hash, lease expiry, attempt number and
retention deadline. `job_attempts` is append-only history; it is not the
coordination primitive. `approval_events` retains superseded approvals for
audit, with an invalidation timestamp and reason.

`audit_events` is append-only through application APIs. It is an audit trail,
not cryptographically immutable storage: operators with database administration
access can still alter D1. Every event records actor/service identity, operation,
target type/ID, target version/hash, timestamp, request ID and prior/new state.

## 13. Job leasing and concurrency

The OCI agent claims a job atomically with one conditional D1 statement:

- `lease_owner`;
- `lease_token_hash` (the raw token is returned only to the claimant);
- `lease_until`;
- attempt number.

The statement updates one eligible row only when its status is queueable and its
lease is absent or expired, and returns the claimed row using `RETURNING`.
Separate select-then-update leasing is forbidden. Only the holder of the
current token can update progress. A lost lease returns to `QUEUED` only when
the current step is classified safe and idempotent; otherwise it becomes
`MANUAL_REVIEW_REQUIRED`.

Initial concurrency:

```text
creator scans: 1
downloads: 2
transcriptions: 1
OCR: 1
voice generation: 1
FFmpeg renders: 1
destination uploads: 1 per destination
```

Transcription and rendering must not run concurrently until the OCI benchmark
shows adequate CPU headroom.

Leased job types are `DISCOVERY_SCAN`, `TRANSLATE_METADATA`,
`INGEST_AND_PROCESS`, `DESTINATION_UPLOAD` and `CLEANUP`. Source discovery,
media processing and destination publication retain their separate state
machines from section 7; job type determines which state machine and retry
rules apply.

## 14. Error handling and notification

Errors are categorized:

- `RETRYABLE_NETWORK`;
- `RATE_LIMITED`;
- `AUTH_REQUIRED`;
- `ADAPTER_BROKEN`;
- `SOURCE_REMOVED`;
- `RIGHTS_BLOCKED`;
- `QUOTA_EXHAUSTED`;
- `INVALID_AI_RESPONSE`;
- `MEDIA_INVALID`;
- `DISK_PRESSURE`;
- `DESTINATION_REJECTED`;
- `MANUAL_REVIEW_REQUIRED`.

Retry policy:

- exponential backoff with jitter for transient network and 5xx errors;
- honor `Retry-After`;
- no automated retry for CAPTCHA, invalid credentials, rights failure, rejected
  media, or platform policy errors;
- maximum five automated attempts per step;
- retries resume the failed step when outputs are still valid.

“Report immediately” in MVP means:

- persistent red alert in the dashboard;
- failure recorded in `audit_events`;
- optional email notification through a configurable provider;
- processing stops when continuing could publish incorrectly, exceed quota,
  delete the only render, or violate rights.

## 15. Security

- Cloudflare Access allowlist contains only the administrator email.
- Worker validates the Access JWT signature, issuer, audience, expiry and
  expected identity on every administrator route.
- Mutating administrator requests are same-origin only and require CSRF
  protection in addition to Access authentication.
- OCI agent uses a renewable Access service token accepted only by the agent API
  hostname and least-privilege routes.
- Provider keys, source cookies and OAuth tokens are stored in OCI Vault and
  accessed by instance principal; Worker-only signing secrets use Cloudflare
  secrets. No secret is stored in D1, logs, source code or frontend bundles.
- Source cookies and authorization headers are redacted from logs and child
  process arguments.
- Creator URLs and every redirect are restricted to HTTPS and checked against
  loopback, link-local, private and otherwise disallowed addresses before fetch.
- Preview uses Access plus expiring token and path validation.
- Preview tokens bind administrator identity, job ID, render version and expiry;
  URLs set `Referrer-Policy: no-referrer`, and query tokens are redacted from
  Nginx and application logs.
- Nginx supports Range requests but prevents directory listing.
- OAuth refresh tokens are revocable and rotated through OCI Vault versions.
- Every publish/delete action records actor, timestamp, render version,
  destination and external ID.
- No browser automation or CAPTCHA bypass.

## 16. Performance and capacity

The 2 OCPU/12 GB OCI shape is approved only for an MVP with:

- one administrator;
- one CPU-heavy job at a time;
- default maximum imported duration of 60 minutes;
- overnight processing, with a 12-hour maximum for the configured duration
  limit after benchmark.

Spring Boot is explicitly rejected for the OCI MVP because it would add another
runtime and process boundary without accelerating yt-dlp, Whisper, OCR or
FFmpeg. Python owns orchestration close to the media tools. Cloudflare Worker
owns the public API and UI.

Required benchmark before feature completion:

- 15-, 30-, and 60-minute authorized representative videos;
- download reliability by adapter;
- transcription real-time factor and peak RAM;
- OCR throughput if enabled;
- Azure voice generation latency;
- 1080p render real-time factor and peak disk;
- concurrent preview impact;
- upload retry behavior.

Results determine whether 60 minutes remains the limit or is reduced.

The pass criteria for the complete offline pipeline are:

- a 60-minute representative video completes within 12 hours;
- peak measured memory remains below 10 GB;
- the media volume retains at least 20 GB free throughout;
- no CPU-heavy stage overlaps another CPU-heavy stage.

If the 60-minute sample fails any criterion, repeat with 30 minutes and then 15
minutes. The production duration limit is the longest sample that passes all
criteria. If 15 minutes fails, the OCI media gate fails and full implementation
stops pending an explicit infrastructure or provider decision.

Always Free is not a production SLA. OCI may reclaim an idle instance and
regional A1 capacity may prevent immediate recreation. The MVP therefore
requires reproducible provisioning, agent heartbeat/reclamation alerts and a
documented restore procedure; it does not manufacture load to avoid idle
reclamation.

## 17. Cost controls

Initial target is zero infrastructure/API cost beyond any existing domain and
rights fees:

- Cloudflare Workers/D1/Access/Tunnel free allowances;
- OCI Always Free eligibility;
- Gemini free-tier model;
- Azure Speech F0;
- local speech recognition/OCR/rendering.

Cost is not guaranteed because free tiers, quotas and provider terms can change.
The system stores provider usage counters and never silently upgrades to a paid
tier.

Default safeguards:

- at most two imports per day;
- production imports disabled until the benchmark sets a passing maximum of no
  more than 60 minutes;
- one CPU-heavy job;
- Azure warning at 80%, block at 95%;
- Gemini request/rate-limit monitoring;
- D1 row-read/row-write monitoring and indexed polling queries;
- YouTube upload-bucket monitoring;
- no R2 media storage;
- no automatic paid fallback.

## 18. Acceptance criteria

### Source and rights

- An inactive or expired permission record cannot scan/import.
- A creator HTTPS URL and an OCI inbox file can each be imported without a
  platform adapter.
- A source that does not allow named third-party AI processing cannot be sent
  to Gemini.
- For every enabled platform adapter, new metadata appears without downloading
  media.
- Repeated scans do not duplicate a stable source video ID.
- Adapter failure is isolated and visible.

### Localization

- Original and translated title/description are both preserved.
- Translation segment IDs and timestamps remain aligned.
- Glossary terms remain consistent across chunks.
- Administrator can edit and approve metadata/transcript before synthesis.
- Quota guard warns at 80% and stops voice generation at 95% of the configured
  monthly allowance.
- A timing group outside the `0.90`–`1.10` adjustment or 250 ms tolerance stops
  at `TIMING_REVIEW_REQUIRED`.

### Media

- Authorized test video is transcribed on OCI ARM64.
- Vietnamese voice groups do not overlap, use only the allowed rate range and
  finish within 250 ms of their approved source windows.
- Watermark appears top-left and does not collide with subtitles in 16:9 or
  9:16 test renders.
- Preview supports seeking and expires.
- No rendered video is persisted to D1.

### Publishing and deletion

- YouTube upload is private by default and cannot become public without final
  approval.
- YouTube processing failure retains the local render.
- Facebook staging is enabled only after the Meta PoC passes.
- Without that gate, Facebook receives no upload before final approval.
- Publication states are independent per destination.
- The local render is deleted only when no required retry or review depends on
  it or after the audited seven-day expiry.
- Every approval/publication is auditable.

### Recovery

- Agent restart causes an expired lease to be safely reclaimed.
- An unsafe interrupted step requires manual review rather than blind replay.
- Disk pressure blocks new imports.
- CAPTCHA/auth/rate-limit failures do not create retry storms.
- Duplicate idempotency keys do not create duplicate jobs, approvals or uploads.

## 19. Mandatory proof-of-concept gates

Implementation planning must place the guaranteed-path gates before the full
build and treat platform adapters as independently optional:

1. **Manual ingest PoC:** rights-gated import from one creator HTTPS URL and one
   OCI Bastion/SCP inbox file, including hash verification and SSRF/path checks.
2. **Access and lease PoC:** administrator JWT validation, agent service auth,
   atomic claim, heartbeat, expired-lease recovery and duplicate idempotency key.
3. **OCI media benchmark:** whisper.cpp plus FFmpeg on 15-, 30- and 60-minute
   representative clips against the 12-hour/10-GB/20-GB pass criteria.
4. **Azure Vietnamese voice PoC:** enumerate available voices and synthesize an
   approved sample under F0, including timing-review behavior.
5. **Gemini free-tier PoC:** schema-valid translation of metadata and timed
   segments, with documented quotas, data-use approval and exact live model.
6. **YouTube PoC:** private resumable upload, persisted upload IDs, metadata,
   thumbnail/caption, processing polling, upload quota and project audit status.
7. **Preview PoC:** Cloudflare Access + Tunnel + Nginx Range playback without
   exposing OCI ingress, leaking preview tokens or accepting a stale render.
8. **Facebook upload-after-approval PoC:** Page access, token lifecycle, upload
   after final approval and external visibility confirmation. Hidden staging,
   Reels and scheduling remain optional feature gates.
9. **Bilibili authorized-source optional PoC:** discover metadata and download
   one creator-authorized video on OCI ARM64.
10. **Douyin authorized-source optional PoC:** determine whether official OAuth is
   available for the intended account and whether creator-page discovery is
   possible. If not, validate yt-dlp only within written authorization and
   record the operational/terms risk.

Failure of a gate changes the design through the documented fallback; it must
not be bypassed. Failure of optional adapter or hidden-staging gates disables
only that capability and does not fail the manual-first MVP.

## 20. Fallbacks

| Failure | Approved fallback |
|---|---|
| Creator HTTPS URL unavailable | Administrator delivers the authorized file through OCI Bastion/SCP |
| Bilibili/Douyin page extraction fails | Creator supplies an authorized HTTPS URL or the administrator delivers the file through OCI Bastion/SCP |
| Douyin developer access unavailable | Disable automated Douyin discovery; use manual authorized URLs/files |
| whisper.cpp too slow | Reduce duration/model size, run overnight, or add a paid speech provider after explicit approval |
| PaddleOCR ARM packaging fails | Disable OCR; require transcript/source script or manual text correction |
| Gemini free tier unavailable/insufficient | Pause translation; do not silently switch to paid provider |
| Azure F0 exhausted | Pause synthesis until reset or request explicit paid-provider approval |
| YouTube project remains private-only | Continue private staging; publish manually until audit passes |
| Facebook hidden staging unsupported | Upload Facebook only after final approval |
| OCI capacity unavailable | Do not assume another free region/shape; request an explicit infrastructure choice |
| 60-minute OCI benchmark fails | Reduce the limit to the longest passing 30- or 15-minute sample |
| 15-minute OCI benchmark fails | Stop the media build and request an explicit infrastructure/provider decision |

## 21. External references

All URLs below were reviewed on 2026-07-24. Provider documentation and limits
must be rechecked during implementation.

- Cloudflare Workers limits: https://developers.cloudflare.com/workers/platform/limits/
- Cloudflare Workers and static assets: https://developers.cloudflare.com/workers/
- Cloudflare Cron Triggers: https://developers.cloudflare.com/workers/configuration/cron-triggers/
- Cloudflare D1 limits: https://developers.cloudflare.com/d1/platform/limits/
- Cloudflare D1 pricing: https://developers.cloudflare.com/d1/platform/pricing/
- Cloudflare D1 Time Travel: https://developers.cloudflare.com/d1/reference/time-travel/
- Cloudflare Tunnel: https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/
- Cloudflare Access for workers.dev: https://developers.cloudflare.com/workers/configuration/routing/workers-dev/
- Cloudflare Access service tokens: https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/
- Cloudflare Access JWT validation: https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/
- OCI Always Free resources: https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm
- OCI A1 compute shape: https://docs.oracle.com/iaas/Content/Compute/References/computeshapes.htm
- yt-dlp supported sites and reliability warning: https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md
- Douyin video-data API: https://open.douyin.com/platform/resource/docs/openapi/video-management/douyin/search-video/video-data
- Douyin OAuth overview: https://open.douyin.com/platform/resource/docs/develop/permission/overall-permission
- Douyin platform accession: https://open.douyin.com/platform/resource/docs/accession-guide/platform-accession
- Bilibili user agreement: https://www.bilibili.com/blackboard/activity-qKrC5olzgg.html
- Gemini API pricing: https://ai.google.dev/gemini-api/docs/pricing
- Gemini API rate limits: https://ai.google.dev/gemini-api/docs/rate-limits
- Gemini structured outputs: https://ai.google.dev/gemini-api/docs/structured-output
- Azure Speech pricing: https://azure.microsoft.com/en-us/pricing/details/speech/
- whisper.cpp: https://github.com/ggerganov/whisper.cpp
- PaddleOCR ARM deployment: https://paddlepaddle.github.io/PaddleOCR/v2.9/en/ppocr/infer_deploy/index.html
- FFmpeg filters: https://ffmpeg.org/ffmpeg-filters.html
- YouTube Data API video resource: https://developers.google.com/youtube/v3/docs/videos
- YouTube `videos.insert`: https://developers.google.com/youtube/v3/docs/videos/insert
- YouTube OAuth: https://developers.google.com/youtube/v3/guides/authentication
- YouTube upload guide: https://developers.google.com/youtube/v3/guides/uploading_a_video
- Meta Video API publishing: https://developers.facebook.com/documentation/video-api/guides/publishing
- Meta Reels Publishing API: https://developers.facebook.com/documentation/video-api/guides/reels-publishing
- Meta Page feed/unpublished posts: https://developers.facebook.com/docs/graph-api/reference/page/feed/

## 22. Spec self-review result

- No `TBD` or `TODO` placeholders remain.
- Unverified external behavior is explicitly marked as a PoC gate or blocker.
- The architecture does not depend on R2 or Spring Boot.
- The guaranteed ingest path does not depend on Bilibili, Douyin, yt-dlp or
  browser uploads.
- Downloading and third-party AI processing are restricted to the corresponding
  rights-approved scopes and do not bypass access controls.
- Facebook hidden staging and YouTube copyright clearance are not overstated.
- The approved fallback for every mandatory external-risk gate is documented.
- Version-bound approvals cannot survive edits to the content they approved.
- Retention keeps all inputs required for correction until publish/reject or
  audited expiry.
- Failed optional adapter gates reduce capability rather than failing the MVP;
  failed guaranteed-path gates stop implementation instead of expanding scope.
