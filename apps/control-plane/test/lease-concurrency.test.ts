import { describe, expect, it } from "vitest";

import { LeaseInvalidError, LeaseService, type LeaseDatabase, type LeaseStatement } from "../src/services/lease-service";

interface Call {
  sql: string;
  values: unknown[];
}

class AtomicClaimDatabase implements LeaseDatabase {
  readonly calls: Call[] = [];
  private claimed = false;

  constructor(
    private readonly claimedRow: Record<string, unknown> = {
      id: "job-1",
      job_type: "LOCALIZE",
      lease_until: "2026-08-04T00:05:00Z",
      status: "QUEUED",
      version: 2,
    },
  ) {}

  prepare(sql: string) {
    return this.bound(sql, []);
  }

  async batch() {
    return [];
  }

  private bound(sql: string, values: unknown[]) {
    return {
      bind: (...next: unknown[]) => this.bound(sql, [...values, ...next]),
      first: async <Row extends Record<string, unknown>>() => {
        this.calls.push({ sql, values });
        if (!sql.includes("RETURNING")) return null;
        if (this.claimed) return null;
        this.claimed = true;
        return this.claimedRow as Row;
      },
      run: async () => ({ meta: { changes: 1 }, results: [] }),
    };
  }
}

class ConditionalLeaseDatabase implements LeaseDatabase {
  readonly calls: Call[] = [];
  readonly batches: Call[][] = [];

  constructor(private readonly changes: number) {}

  prepare(sql: string) {
    return this.bound(sql, []);
  }

  private bound(sql: string, values: unknown[]) {
    return {
      bind: (...next: unknown[]) => this.bound(sql, [...values, ...next]),
      first: async () => null,
      run: async () => {
        this.calls.push({ sql, values });
        return { meta: { changes: this.changes }, results: [] };
      },
      sql,
      values,
    };
  }

  async batch(statements: LeaseStatement[]) {
    this.batches.push(
      statements.map((statement) => {
        const entry = statement as unknown as Call;
        return { sql: entry.sql, values: entry.values };
      }),
    );
    return [{ meta: { changes: this.changes }, results: [] }, { results: [] }, { results: [] }];
  }
}

describe("atomic job leasing", () => {
  it("allows exactly one concurrent claimant and stores only a lease-token hash", async () => {
    const database = new AtomicClaimDatabase();
    const rawToken = "t".repeat(43);
    const service = new LeaseService(database, () => rawToken);

    const claims = await Promise.all(
      ["agent-a", "agent-b"].map((agentId) =>
        service.claim({
          agentId,
          leaseExpiresAtUtc: "2026-08-04T00:05:00Z",
          nowUtc: "2026-08-04T00:00:00Z",
          supportedJobTypes: ["LOCALIZE"],
          supportedStates: ["QUEUED"],
        }),
      ),
    );

    const winners = claims.filter((claim): claim is NonNullable<typeof claim> => claim !== null);
    expect(winners).toHaveLength(1);
    expect(winners[0]).toMatchObject({ jobId: "job-1", jobType: "LOCALIZE", jobVersion: 2, leaseToken: rawToken, state: "QUEUED" });
    expect(database.calls).toHaveLength(2);
    expect(database.calls[0]!.sql).toContain("UPDATE jobs");
    expect(database.calls[0]!.sql).not.toContain("status = 'INGESTING'");
    expect(database.calls[0]!.sql).toContain("job_type IN (?)");
    expect(database.calls[0]!.sql).toContain("RETURNING id, job_type, status, version, lease_until");
    expect(database.calls[0]!.values).not.toContain(rawToken);
    expect(database.calls[0]!.values).toContain("LOCALIZE");
    expect(database.calls[0]!.values.some((value) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value))).toBe(true);
  });

  it("claims IMPORT work only for a compatible agent and returns no raw rights evidence", async () => {
    const database = new AtomicClaimDatabase({
      id: "job-1",
      job_type: "IMPORT",
      lease_until: "2026-08-04T00:05:00Z",
      metadata_json: JSON.stringify({ locator: { inboxFileId: "creator-submission-42", kind: "OCI_INBOX_FILE" } }),
      rights_metadata_json: JSON.stringify({ evidenceHash: "a".repeat(64) }),
      source_asset_id: "00000000-0000-4000-8000-000000000002",
      status: "QUEUED",
      version: 2,
    });
    const service = new LeaseService(database, () => "t".repeat(43));

    const claim = await service.claim({
      agentId: "import-agent",
      leaseExpiresAtUtc: "2026-08-04T00:05:00Z",
      nowUtc: "2026-08-04T00:00:00Z",
      supportedJobTypes: ["IMPORT"],
      supportedStates: ["QUEUED"],
    });

    expect(claim).toMatchObject({
      importInput: {
        locator: { inboxFileId: "creator-submission-42", kind: "OCI_INBOX_FILE" },
        rightsEvidenceHash: "a".repeat(64),
        sourceAssetId: "00000000-0000-4000-8000-000000000002",
      },
      jobType: "IMPORT",
    });
    expect(database.calls[0]!.sql).toContain("rights_revoked_at IS NULL");
    expect(database.calls[0]!.sql).toContain("jobs.status IN (?)");
    expect(database.calls[0]!.values).toContain("QUEUED");
    expect(database.calls[0]!.values).not.toContain("INGESTING");
  });

  it("extends only the matching, unexpired lease at its current job version", async () => {
    const database = new ConditionalLeaseDatabase(1);
    const service = new LeaseService(database, () => "t".repeat(43));

    await service.heartbeat({
      agentId: "agent-a",
      jobId: "job-1",
      jobVersion: 2,
      leaseExpiresAtUtc: "2026-08-04T00:10:00Z",
      leaseToken: "t".repeat(43),
      nowUtc: "2026-08-04T00:05:00Z",
    });

    expect(database.calls[0]!.sql).toContain("lease_until > ?");
    expect(database.calls[0]!.values).toContain(2);
    expect(database.calls[0]!.values).not.toContain("t".repeat(43));
  });

  it("rejects stale completion or token mismatch without recording a result", async () => {
    const database = new ConditionalLeaseDatabase(0);
    const service = new LeaseService(database, () => "t".repeat(43));

    await expect(
      service.complete({
        agentId: "agent-a",
        expectedState: "INGESTING",
        jobId: "job-1",
        jobVersion: 2,
        leaseToken: "t".repeat(43),
        nextState: "PROBING",
        nowUtc: "2026-08-04T00:05:00Z",
        step: "probe",
      }),
    ).rejects.toThrow(LeaseInvalidError);
    expect(database.batches[0]).toHaveLength(3);
    expect(database.batches[0]![0]!.sql).toContain("lease_token_hash = ?");
    expect(database.batches[0]![0]!.sql).toContain("version = ?");
    expect(database.batches[0]![1]!.sql).toContain("INSERT INTO job_events");
    expect(database.batches[0]![2]!.sql).toContain("INSERT INTO audit_events");
  });

  it("stores sanitized ingest metadata in the successful conditional job update", async () => {
    const database = new ConditionalLeaseDatabase(1);
    const service = new LeaseService(database, () => "t".repeat(43));
    const ingestResult = {
      normalizedByteCount: 10,
      normalizedHash: "a".repeat(64),
      sourceByteCount: 12,
      sourceHash: "b".repeat(64),
      technicalMetadata: { durationSeconds: 12.5, formatName: "mov,mp4", hasAudio: true, hasVideo: true },
    };

    await service.complete({
      agentId: "agent-a",
      expectedState: "INGESTING",
      ingestResult,
      jobId: "job-1",
      jobVersion: 2,
      leaseToken: "t".repeat(43),
      nextState: "PROBING",
      nowUtc: "2026-08-04T00:05:00Z",
      step: "ingested",
    });

    expect(database.batches[0]![0]!.sql).toContain("ingest_result_json = COALESCE(?, ingest_result_json)");
    expect(database.batches[0]![0]!.values).toContain(JSON.stringify(ingestResult));
    expect(database.batches[0]![0]!.sql).toContain("job_type = 'IMPORT'");
    expect(database.batches[0]![1]!.values).not.toContain("creator-submission-42");
  });
});
