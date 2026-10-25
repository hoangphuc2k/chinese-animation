# Risk-Gate Runbook (gates 1–3 first)

Spec section 19 requires gates 1–3 to run on the target host before any new
feature phase starts. This runbook covers what the administrator must provide,
what the repository must still build, and how results are recorded.

## Current state (2026-10-25)

All gate artifacts in `artifacts/pocs/` are `BLOCKED`. The gate harnesses do
not yet execute anything on a real host:

| Gate | Harness | State |
| --- | --- | --- |
| 1 Manual ingest | `pocs/manual-ingest/run.py` | Offline URL/path validators only; no download, Bastion/SCP or `ffprobe` run |
| 2 Access and lease | `pocs/access-lease/run.mjs` | Prints a fixed `BLOCKED` result |
| 3 OCI media benchmark | `pocs/oci-media/benchmark.sh` | Prints a message; `collect.py` only classifies supplied numbers |
| Fixtures | `pocs/fixtures/manifest.json` | Synthetic placeholders with zero hashes |

Running the gates therefore needs two things: a provisioned host (administrator)
and real harnesses (repository work, listed under each gate).

## 0. Administrator prerequisites

1. **OCI tenancy decision.** Decide whether to upgrade to Pay-As-You-Go to
   avoid idle reclamation (spec sections 3 and 16). If yes, add a small budget
   alert first.
2. **Provision the host.** `VM.Standard.A1.Flex`, 2 OCPU, 12 GB, Linux ARM64
   (Ubuntu 24.04 or Oracle Linux 9), 50 GB boot volume, 150 GB block volume
   mounted at `/mnt/localizer`. No public inbound port; SSH through OCI Bastion.
   The repository has no provisioning code yet, only
   `infra/processing-agent/localizer-agent.service`. Record every console step
   so it can become reproducible provisioning (spec section 16).
3. **Rights-cleared fixtures.** Synthetic media cannot measure Chinese speech
   recognition error, burned-in subtitle detection or dub timing. Provide 15-,
   30- and 60-minute authorized donghua clips, at least one with burned-in
   Chinese subtitles, and the creator's Chinese subtitle file for one clip to
   serve as the reference transcript. Keep them out of Git.
4. **Cloudflare.** A D1 database for the PoC, the administrator and agent Access
   applications, and an Access service token (gate 2).

## Gate 3 — OCI media benchmark (highest priority)

This gate decides whether the MVP is viable on the free host, so run it first.

Repository work still needed:

- Replace `benchmark.sh` with a harness that, per fixture:
  extracts PCM with FFmpeg; runs whisper.cpp with each candidate model
  (`small`, `medium`, `large-v3-turbo`, quantized); runs subtitle-band detection
  and OCR on the hardsubbed clip; renders 1080p with subtitles, cover band and
  watermark; and records elapsed time, peak RSS (`/usr/bin/time -v`), minimum
  free disk on `/mnt/localizer`, output hash and failure stage.
- Compute Chinese character error rate against the reference transcript.
- Write `artifacts/pocs/oci-media.json` through `collect.py`, adding per-model
  metrics.

Host setup: FFmpeg (ARM64 build), whisper.cpp built at a pinned commit, the
model files with recorded checksums, PaddleOCR, Python with `uv`.

Pass criteria are in spec section 16. If 60 minutes fails, rerun with 30, then
15. If 15 fails, stop and escalate (spec section 20).

## Gate 2 — Access and lease

Repository work still needed: make `pocs/access-lease/run.mjs` call the deployed
PoC Worker. It should verify the administrator JWT and the service token,
perform two concurrent claims on one job (exactly one must win), send
heartbeats, let a lease expire and reclaim it, and replay a duplicate
idempotency key.

Host note: Wrangler runs on Linux ARM64, so the OCI host (or a Linux CI
runner) also removes the local macOS 12.6 blocker for native D1 migrations.

## Gate 1 — Manual ingest

Repository work still needed: run the real agent ingest path against one
creator HTTPS URL and one file copied to `/mnt/localizer/inbox` through Bastion.
Assert hash verification, the rights check before any fetch, redirect
revalidation to a private address (expect rejection) and an inbox symlink
(expect rejection).

## Recording results

Each harness writes `artifacts/pocs/<gate_id>.json` with a real fixture hash.
Then run:

```sh
node scripts/run-poc-gates.mjs artifacts/pocs
```

Update `docs/implementation/status.md` with each gate's result. Never commit
media, credentials or absolute host paths; the runner rejects absolute paths and
secret-like fields.
