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
          pipeline_lineage_json: "[]",
          rights_metadata_json: JSON.stringify({ evidenceHash: "c".repeat(64) }),
          script_hash: null,
          script_version: null,
          source_asset_id: "00000000-0000-4000-8000-000000000002",
          status: "EXTRACTING_AUDIO",
          version: 3,
        }) as unknown as Row | null,
      run: async () => ({ meta: { changes: 1 }, results: [] }),
    };
  }

  async batch() {
    return [];
  }
}

class TranslationClaimDatabase implements LeaseDatabase {
  prepare(sql: string): LeaseStatement {
    return {
      bind: () => this.prepare(sql),
      first: async <Row extends Record<string, unknown>>() =>
        ({
          id: "00000000-0000-4000-8000-000000000001",
          ingest_result_json: null,
          job_type: "LOCALIZE",
          lease_until: "2026-08-07T00:01:00Z",
          metadata_json: null,
          pipeline_lineage_json: JSON.stringify([
            [
              {
                artifactRef: "transcript-01.json",
                byteCount: 512,
                kind: "TRANSCRIPT",
                sha256: "a".repeat(64),
                toolVersion: "whisper.cpp-1.7.4",
              },
            ],
          ]),
          rights_metadata_json: JSON.stringify({
            allowedProcessors: ["GOOGLE_GEMINI"],
            evidenceHash: "c".repeat(64),
          }),
          script_hash: null,
          script_version: null,
          source_asset_id: "00000000-0000-4000-8000-000000000002",
          status: "TRANSLATING",
          version: 4,
        }) as unknown as Row | null,
      run: async () => ({ meta: { changes: 1 }, results: [] }),
    };
  }

  async batch() {
    return [];
  }
}

class TranscriptionClaimDatabase implements LeaseDatabase {
  prepare(sql: string): LeaseStatement {
    return {
      bind: () => this.prepare(sql),
      first: async <Row extends Record<string, unknown>>() =>
        ({
          id: "00000000-0000-4000-8000-000000000001",
          ingest_result_json: null,
          job_type: "IMPORT",
          lease_until: "2026-08-07T00:01:00Z",
          metadata_json: null,
          pipeline_lineage_json: JSON.stringify([
            [{ artifactRef: "audio.wav", byteCount: 512, kind: "EXTRACTED_AUDIO", sha256: "d".repeat(64), toolVersion: "ffmpeg-pcm16-v1" }],
          ]),
          rights_metadata_json: null,
          script_hash: null,
          script_version: null,
          source_asset_id: "00000000-0000-4000-8000-000000000002",
          status: "TRANSCRIBING",
          version: 4,
        }) as unknown as Row | null,
      run: async () => ({ meta: { changes: 1 }, results: [] }),
    };
  }

  async batch() {
    return [];
  }
}

describe("pipeline artifact lineage", () => {
  it("leases audio extraction with only sanitized normalized-media metadata", async () => {
    const database = new ImportClaimDatabase();

    const claim = await new LeaseService(database, () => "t".repeat(43)).claim({
      agentId: "agent-a",
      leaseExpiresAtUtc: "2026-08-07T00:01:00Z",
      nowUtc: "2026-08-07T00:00:00Z",
      supportedJobTypes: ["IMPORT"],
      supportedStates: ["EXTRACTING_AUDIO"],
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
        state: "EXTRACTING_AUDIO",
      },
      state: "EXTRACTING_AUDIO",
    });
    expect(claim).not.toHaveProperty("importInput");
    expect(JSON.stringify(claim)).not.toContain("creator-submission-42");
  });

  it("leases transcription with only extracted-audio lineage", async () => {
    const claim = await new LeaseService(new TranscriptionClaimDatabase(), () => "t".repeat(43)).claim({
      agentId: "agent-a",
      leaseExpiresAtUtc: "2026-08-07T00:01:00Z",
      nowUtc: "2026-08-07T00:00:00Z",
      supportedJobTypes: ["IMPORT"],
      supportedStates: ["TRANSCRIBING"],
    });

    expect(claim).toMatchObject({
      pipelineInput: {
        extractedAudio: { artifactRef: "audio.wav", kind: "EXTRACTED_AUDIO", sha256: "d".repeat(64) },
        state: "TRANSCRIBING",
      },
    });
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

  it("projects only transcript lineage and explicit provider permission for translation", async () => {
    const claim = await new LeaseService(new TranslationClaimDatabase(), () => "t".repeat(43)).claim({
      agentId: "agent-a",
      leaseExpiresAtUtc: "2026-08-07T00:01:00Z",
      nowUtc: "2026-08-07T00:00:00Z",
      supportedJobTypes: ["LOCALIZE"],
      supportedStates: ["TRANSLATING"],
    });

    expect(claim).toMatchObject({
      pipelineInput: {
        googleProcessingAllowed: true,
        state: "TRANSLATING",
        transcript: { artifactRef: "transcript-01.json", kind: "TRANSCRIPT" },
      },
    });
    expect(JSON.stringify(claim)).not.toContain("evidenceHash");
  });

  it("persists an opaque preview descriptor in the lease-conditioned completion", async () => {
    const database = new CaptureDatabase();
    const preview = {
      expiresAtUtc: "2026-08-07T00:05:00Z",
      renderSha256: "a".repeat(64),
      token: "p".repeat(43),
    };

    await new LeaseService(database, () => "t".repeat(43)).complete({
      agentId: "agent-a",
      expectedState: "READY_FOR_PREVIEW",
      jobId: "00000000-0000-4000-8000-000000000001",
      jobVersion: 8,
      leaseToken: "t".repeat(43),
      nextState: "WAITING_PUBLISH_APPROVAL",
      nowUtc: "2026-08-07T00:00:00Z",
      preview,
      step: "preview_registered",
    });

    const update = database.batches[0]?.[0];
    expect(update?.sql).toContain("preview_descriptor_json");
    expect(update?.sql).toContain("json_extract(artifact.value, '$.kind') = 'RENDER'");
    expect(update?.values).toContain(JSON.stringify(preview));
    expect(update?.values).toContain(preview.renderSha256);
  });

  it("requires a bounded descriptor for the canonical preview transition", async () => {
    const service = new LeaseService(new CaptureDatabase(), () => "t".repeat(43));
    const input = {
      agentId: "agent-a",
      expectedState: "READY_FOR_PREVIEW" as const,
      jobId: "00000000-0000-4000-8000-000000000001",
      jobVersion: 8,
      leaseToken: "t".repeat(43),
      nextState: "WAITING_PUBLISH_APPROVAL" as const,
      nowUtc: "2026-08-07T00:00:00Z",
      step: "preview_registered",
    };

    await expect(service.complete(input)).rejects.toThrow(/descriptor/i);
    await expect(
      service.complete({
        ...input,
        preview: { expiresAtUtc: "2026-08-07T00:05:01Z", renderSha256: "a".repeat(64), token: "p".repeat(43) },
      }),
    ).rejects.toThrow(/five-minute/i);
  });
});
