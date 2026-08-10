# Phase 03 Control-Plane Core Report

## Execution status

The control-plane implementation is complete and passes its repository-wide
static, unit, contract, and generated-model checks. It includes authenticated
workflow APIs, optimistic state transitions, idempotency, agent leases,
approval invalidation, retention cleanup commands, provider quotas, and
structured JSON logging.

Task 7 adds a daily UTC cron trigger that expires due preview work, blocks
revoked rights, queues `DELETE_EXPIRED` work for the processing agent, and
retains terminal metadata and audit records. It does not claim OCI media has
been deleted. Quota use warns at 80 percent, blocks work that would reach 95
percent, and respects both delta-seconds and HTTP-date `Retry-After` values.
Structured logs retain request/job/project correlation while redacting headers,
URLs, sensitive source titles, and token-like fields.

## Verification evidence

On 2026-08-07, `npm run verify` passed:

- control plane: 56 tests across 11 files;
- contracts: 6 tests across 2 files;
- lint, formatting, TypeScript type checks, generated-contract drift check, and
  generated Python model compilation.

`npm run cf-typegen --workspace=@localizer/control-plane` regenerated the
Worker configuration types successfully. The clean-D1 migration behavior is
also covered by the migration suite, including applying all migrations from an
empty database.

## Remaining environment gate

The required native command was attempted:

```sh
npm run db:migrate:local --workspace=@localizer/control-plane
```

Wrangler 4.110.0 stopped before running Workerd because the current host is
macOS 12.6.0; Workerd requires macOS 13.5 or newer. This is an environment
blocker, not a migration failure. Run the command in a DevContainer with
supported Linux (glibc 2.35+) or on macOS 13.5+; if it succeeds, change Phase
03 status to `PASS`.
