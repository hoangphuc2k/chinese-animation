# Implementation Status

| Phase | Status | Contract | Migration | Notes |
| --- | --- | --- | --- | --- |
| 01 Foundation and contracts | BLOCKED (environment) | 0.1.0 | none | Source/contracts are scaffolded; dependency installation and executable suites require network-enabled CI. |
| 02 Mandatory risk-gate PoCs | BLOCKED (external runtime) | 0.1.0 | none | Schema-valid blocked artifacts exist for all mandatory gates; provider/OCI execution still requires credentialed CI and target hosts. See `docs/implementation/phase-02-report.md`. |
| 03 Control-plane core | BLOCKED (local Workerd runtime) | 0.1.0 | 0001–0003 validated in test | All API, leasing, approval, retention, quota, and observability code is verified by the full suite. Native D1 migration cannot run on macOS 12.6; Wrangler requires macOS 13.5+ or supported Linux. See `docs/implementation/phase-03-report.md`. |
| 04 Processing-agent ingest | BLOCKED (local Workerd runtime) | 0.1.0 | 0001–0004 validated in test | IMPORT protocol, rights-aware leasing, ingest persistence, safe source ingest, media normalization, OCI packaging, and recovery are verified by the full suite. Native D1 migration still requires macOS 13.5+ or supported Linux. See `docs/implementation/phase-04-report.md`. |
| 05 Localization and preview pipeline | BLOCKED (external runtime) | 0.1.0 | 0001–0006 validated in test | The continuous ingest-to-preview runtime, sanitized artifact lineage, rights/approval gates, provider adapters, media stages, and opaque five-minute preview registration are verified by the full suite. Native OCI, provider, media-binary, D1, and preview-delivery checks remain. See `docs/implementation/phase-05-report.md`. |

Spec revision 3 (2026-10-25) requires gates 1–3 to run on the target host before any new feature phase starts. Gate harnesses for 1–3 are still offline stubs; see `docs/implementation/gate-runbook.md`.

## Local end-to-end run (2026-10-25)

`infra/local/` runs the real Worker, D1 migrations, agent, FFmpeg and whisper.cpp in containers, with Cloudflare Access, Gemini and Azure replaced by local fakes. A synthetic 17-second clip now runs from import through transcription, translation, script approval and voice generation, and stops at `MIXING`.

Open gaps found by the run:

- Long whisper segments break the subtitle rules (`SubtitleError` at `MIXING`). Decision: split segments after transcription (spec section 9.2); not implemented.
- A permanent stage error is retried on every lease expiry (every 5 minutes) instead of moving the job to `MANUAL_REVIEW_REQUIRED`, and the agent logs only the error type.
- The script API returns hashes only, so the administrator cannot read or edit the transcript and translation.
- Azure Speech has no rights-record check; only Gemini is gated by `allowedProcessors`.
