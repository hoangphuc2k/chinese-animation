import { describe, expect, it } from "vitest";

import { LeaseService, type LeaseDatabase, type LeaseStatement } from "../src/services/lease-service";

interface CapturedStatement {
  sql: string;
  values: unknown[];
}

class CaptureDatabase implements LeaseDatabase {
  batches: CapturedStatement[][] = [];

  prepare(sql: string): LeaseStatement {
    return this.statement(sql, []);
  }

  async batch(statements: LeaseStatement[]) {
    this.batches.push(
      statements.map((statement) => {
        const captured = statement as unknown as CapturedStatement;
        return { sql: captured.sql, values: captured.values };
      }),
    );
    return [{ meta: { changes: 1 }, results: [] }, { results: [] }, { results: [] }];
  }

  private statement(sql: string, values: unknown[]): LeaseStatement {
    return {
      bind: (...next) => this.statement(sql, [...values, ...next]),
      first: async <Row extends Record<string, unknown>>() => null as Row | null,
      run: async () => ({ meta: { changes: 1 }, results: [] }),
      sql,
      values,
    } as LeaseStatement;
  }
}

class ImportClaimDatabase implements LeaseDatabase {
  claimSql = "";

  prepare(sql: string): LeaseStatement {
    this.claimSql = sql;
    return {
      bind: () => this.prepare(sql),
      first: async <Row extends Record<string, unknown>>() =>
        ({
          id: "00000000-0000-4000-8000-000000000001",
          ingest_result_json: JSON.stringify({
            normalizedByteCount: 1024,
            normalizedHash: "a".repeat(64),
            sourceByteCount: 2048,
            sourceHash: "b".repeat(64),
            technicalMetadata: { durationSeconds: 12.5, formatName: "mov,mp4", hasAudio: true, hasVideo: true },
          }),
          job_type: "IMPORT",
          lease_until: "2026-08-07T00:01:00Z",
          metadata_json: JSON.stringify({ locator: { inboxFileId: "creator-submission-42", kind: "OCI_INBOX_FILE" } }),
          rights_metadata_json: JSON.stringify({ evidenceHash: "c".repeat(64) }),
          source_asset_id: "00000000-0000-4000-8000-000000000002",
          status: "TRANSCRIBING",
          version: 3,
        }) as unknown as Row | null,
      run: async () => ({ meta: { changes: 1 }, results: [] }),
    };
  }

  async batch() {
    return [];
  }
}

describe("pipeline artifact lineage", () => {
  it("leases post-ingest import work with only sanitized normalized-media metadata", async () => {
    const database = new ImportClaimDatabase();

    const claim = await new LeaseService(database, () => "t".repeat(43)).claim({
      agentId: "agent-a",
      leaseExpiresAtUtc: "2026-08-07T00:01:00Z",
      nowUtc: "2026-08-07T00:00:00Z",
      supportedJobTypes: ["IMPORT"],
      supportedStates: ["TRANSCRIBING"],
    });

    expect(database.claimSql).not.toContain("jobs.status IN ('QUEUED', 'INGESTING')");
    expect(claim).toMatchObject({
      pipelineInput: {
        normalizedMedia: {
          artifactRef: "normalized-media",
          byteCount: 1024,
          kind: "NORMALIZED_MEDIA",
          sha256: "a".repeat(64),
        },
      },
      state: "TRANSCRIBING",
    });
    expect(claim).not.toHaveProperty("importInput");
    expect(JSON.stringify(claim)).not.toContain("creator-submission-42");
  });

  it("persists a sanitized transcript artifact in the lease-conditioned completion update", async () => {
    const database = new CaptureDatabase();
    const artifact = {
      artifactRef: "artifact-01",
      byteCount: 512,
      kind: "TRANSCRIPT" as const,
      sha256: "a".repeat(64),
      toolVersion: "whisper.cpp-1.7.4",
    };

    await new LeaseService(database, () => "t".repeat(43)).complete({
      agentId: "agent-a",
      expectedState: "TRANSCRIBING",
      jobId: "00000000-0000-4000-8000-000000000001",
      jobVersion: 2,
      leaseToken: "t".repeat(43),
      nextState: "TRANSLATING",
      nowUtc: "2026-08-07T00:00:00Z",
      pipelineArtifacts: [artifact],
      step: "transcribed",
    });

    const update = database.batches[0]?.[0];
    expect(update?.sql).toContain("pipeline_lineage_json");
    expect(update?.values).toContain(JSON.stringify([artifact]));
  });
});
