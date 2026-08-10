# Phase 4 — Processing Agent Ingest

Status: complete in-repository; native D1 migration remains blocked by the local Workerd runtime.

Implemented scope:

- IMPORT-only agent claim capabilities, sanitized import locators, and ingest-result contract validation.
- Rights-revocation-aware lease selection, atomic ingest-result persistence, and conditional audit/event records.
- D1 migration `0004_job_ingest_result.sql`.
- Python control-plane client, lease heartbeat guard, crash-safe workspace manifest, hardened HTTPS/inbox ingest, FFprobe/FFmpeg normalization, and supervisor entrypoint.
- OCI Dockerfile, systemd service unit, and non-secret agent environment template.

Safety properties:

- The agent receives only a rights-evidence hash, never the evidence text.
- Structured logs redact credentials, URLs, locators, and paths.
- HTTPS sources require public resolved addresses and use a pinned resolved address with TLS hostname validation; redirects are not followed.
- Job manifests and completion payloads contain IDs, hashes, byte counts, and technical metadata only.

Verification passed:

- `rtk npm run generate --workspace=@localizer/contracts`
- `rtk npm run verify`
- `rtk uv run --directory apps/processing-agent pytest -q`

Results: 60 control-plane tests, 9 contract tests, and 24 processing-agent tests passed; lint, formatting, TypeScript type checks, generated-contract checks, and generated Python compilation passed.

Remaining environment gate:

- Run native D1 migration on a supported macOS/Workerd host. This Mac is macOS 12 while the retained Wrangler runtime requires macOS 13.5 or newer.
