# Processing Agent and Guaranteed Ingest Design

## Status and scope

This design implements Phase 04 in `apps/processing-agent` and includes the
small control-plane protocol extension required for a processing agent to
safely ingest `IMPORT` jobs. The Worker remains a metadata and coordination
plane: it never receives, proxies, stores, or returns media bytes or agent
filesystem paths.

The existing `src/localizer_agent` Python package is the sole agent package.
New modules live beneath it rather than in a second top-level `agent` package.

## Protocol extension

The current agent claim response lacks the source locator and rights basis an
ingest worker needs. The contract and authenticated agent API will therefore
add an import-specific claim payload containing:

- opaque source asset ID;
- typed `IngestLocator` (`CREATOR_HTTPS_URL` or `OCI_INBOX_FILE`);
- recorded rights-evidence hash.

An agent claim declares the job types it supports. The Phase 04 agent supports
only `IMPORT`, preventing it from leasing future localization, rendering, or
publication work.

The completion request gains an optional ingest result for `IMPORT` work:

- source SHA-256 and byte count;
- sanitized technical media metadata and normalized output hash;
- no path, URL, credential, or raw lease token.

On a valid lease and version, the control plane persists this metadata with the
state transition and audit/job events. Event details contain opaque IDs and
hashes only. The source locator stays available only in the authenticated
claim payload and is never copied into logs or events. The human-entered
rights evidence remains outside that payload; its existing hash lets the agent
require that a rights basis was recorded without disclosing its text.

## Agent architecture

`localizer_agent.config` validates environment configuration: HTTPS
control-plane endpoint, agent ID, allowed absolute inbox/work roots, disk
floor, bounded timeouts, and a fixed default concurrency of one. Credentials
are loaded lazily from OCI Vault through instance principal; command-line
secret input is not supported.

`localizer_agent.control_plane` owns bounded HTTP requests, response-model
validation, error classification, and `Retry-After` parsing. `lease` holds
the raw token in memory, sends heartbeats before half of the lease lifetime,
and cancels stage work when renewal leaves the safe window.

`workspace`, `manifest`, and `recovery` create restrictive job-scoped paths:
`work/<job-id>/<stage>/<attempt-id>`. A manifest stores only non-secret inputs,
hashes, and completion markers. Promotion uses fsync and atomic rename;
corrupt or incomplete work is quarantined.

`ingest.https_source` resolves and validates every redirect target, restricts
connections to public HTTPS endpoints, verifies peer address stability, and
streams into a partial file while hashing. `ingest.inbox` accepts only
registered opaque inbox file IDs, validates containment and file stability,
and moves or copy-verifies files into the job workspace. Neither source type
accepts an arbitrary local path.

`media.probe` invokes `ffprobe` without a shell and parses a bounded expected
JSON shape. `media.normalize` invokes a benchmarked FFmpeg mezzanine profile,
records tool version and command metadata, and reports a content hash. These
modules return sanitized technical results to the supervisor.

## Security and failure handling

Structured logging redacts authorization, lease, preview, query-token, and
provider credential values from fields, URLs, and exception text. The raw
lease token never reaches the manifest, checkpoint, error, or log sink.

HTTPS ingest rejects unsupported schemes, userinfo, custom ports, private or
reserved IPv4/IPv6 ranges, DNS rebinding, redirect policy violations, excess
size, MIME mismatch, checksum mismatch, absent rights evidence, and excessive
duration. OCI inbox ingest rejects traversal, symlinks, hardlinks,
non-regular files, unsupported extensions, unstable or young files, duplicates,
and low-disk conditions.

Only classified transient network/provider errors are retried, with bounded
jitter and server `Retry-After`. Authentication, schema, rights, traversal,
and validation errors are terminal. A lease conflict or failed safe-window
heartbeat stops processing without attempting completion.

## Verification

Tests are written before implementation and use local fixtures/fakes for
network, Vault, FFmpeg, and control-plane boundaries. Required coverage
includes protocol contracts, empty queue, lease lifecycle, token
non-persistence, path containment, atomic recovery, SSRF and redirect policy,
OCI inbox safety, malformed/excessive media, and integration flows for both
ingest sources.

The phase verifies with the processing-agent test suite, Ruff, Pyright, and
the repository verifier. OCI deployment evidence remains an explicit external
gate: the status report must record the tested OCI image and FFmpeg version
before Phase 04 can be marked `PASS`.
