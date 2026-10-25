import { afterEach, describe, expect, it } from "vitest";

import { ApprovalService } from "../src/services/approval-service";
import { JobService } from "../src/services/job-service";
import { LeaseService, type LeaseDatabase } from "../src/services/lease-service";
import { createSqliteD1, type SqliteD1 } from "./sqlite-d1";

const projectId = "00000000-0000-4000-8000-000000000001";
const jobId = "00000000-0000-4000-8000-000000000010";
const transcript = { artifactRef: "transcript.json", byteCount: 512, kind: "TRANSCRIPT" as const, sha256: "a".repeat(64), toolVersion: "whisper.cpp" };
const script = { artifactRef: "script.json", byteCount: 640, kind: "SCRIPT" as const, sha256: "b".repeat(64), toolVersion: "gemini" };
const databases: SqliteD1[] = [];

afterEach(() => {
  for (const database of databases.splice(0)) database.sqlite.close();
});

async function jobWaitingForTranslation(): Promise<SqliteD1> {
  const database = createSqliteD1();
  databases.push(database);
  database.sqlite
    .prepare("INSERT INTO projects (id, name, status, created_at, updated_at) VALUES (?, 'Project', 'ACTIVE', ?, ?)")
    .run(projectId, "2026-08-07T00:00:00Z", "2026-08-07T00:00:00Z");
  await new JobService(database as never).createImport({
    actorId: "admin",
    actorType: "admin",
    allowedProcessors: ["GOOGLE_GEMINI"],
    jobId,
    nowUtc: "2026-08-07T00:00:00Z",
    projectId,
    requestId: "request-1",
    rightsEvidence: "creator permission",
    sourceAssetId: "00000000-0000-4000-8000-000000000011",
    sourceExternalId: "episode.mp4",
    sourceMetadataJson: JSON.stringify({ locator: { inboxFileId: "episode.mp4", kind: "OCI_INBOX_FILE" } }),
    sourceProvider: "OCI_INBOX_FILE",
  });
  database.sqlite
    .prepare("UPDATE jobs SET status = 'TRANSLATING', step = 'TRANSLATING', pipeline_lineage_json = ? WHERE id = ?")
    .run(JSON.stringify([[transcript]]), jobId);
  return database;
}

describe("translation completion", () => {
  it("records the generated script as a reviewable version that voice generation can claim after approval", async () => {
    const database = await jobWaitingForTranslation();
    const leases = new LeaseService(database as unknown as LeaseDatabase);
    const lease = { agentId: "agent-a", leaseExpiresAtUtc: "2026-08-07T00:05:00Z", nowUtc: "2026-08-07T00:00:00Z" };

    const translating = await leases.claim({ ...lease, supportedJobTypes: ["IMPORT"], supportedStates: ["TRANSLATING"] });
    expect(translating?.state).toBe("TRANSLATING");
    await leases.complete({
      agentId: "agent-a",
      expectedState: "TRANSLATING",
      jobId,
      jobVersion: translating!.jobVersion,
      leaseToken: translating!.leaseToken,
      nextState: "WAITING_SCRIPT_REVIEW",
      nowUtc: "2026-08-07T00:01:00Z",
      pipelineArtifacts: [script],
      step: "translated",
    });

    expect(database.sqlite.prepare("SELECT version, content_hash, transcript_hash FROM script_versions WHERE job_id = ?").all(jobId)).toEqual([
      { content_hash: script.sha256, transcript_hash: transcript.sha256, version: 1 },
    ]);

    await new ApprovalService(database as never).approveScript({
      actorId: "admin",
      contentHash: script.sha256,
      jobId,
      nowUtc: "2026-08-07T00:02:00Z",
      requestId: "request-2",
      scriptVersion: 1,
    });
    const voice = await leases.claim({
      ...lease,
      nowUtc: "2026-08-07T00:03:00Z",
      supportedJobTypes: ["IMPORT"],
      supportedStates: ["GENERATING_VOICE"],
    });
    expect(voice?.pipelineInput).toMatchObject({ scriptSha256: script.sha256, scriptVersion: 1, state: "GENERATING_VOICE" });
  });

  it("does not record a script version when the lease-conditioned completion is rejected", async () => {
    const database = await jobWaitingForTranslation();
    const leases = new LeaseService(database as unknown as LeaseDatabase);
    const translating = await leases.claim({
      agentId: "agent-a",
      leaseExpiresAtUtc: "2026-08-07T00:05:00Z",
      nowUtc: "2026-08-07T00:00:00Z",
      supportedJobTypes: ["IMPORT"],
      supportedStates: ["TRANSLATING"],
    });

    await expect(
      leases.complete({
        agentId: "agent-a",
        expectedState: "TRANSLATING",
        jobId,
        jobVersion: translating!.jobVersion,
        leaseToken: "x".repeat(43),
        nextState: "WAITING_SCRIPT_REVIEW",
        nowUtc: "2026-08-07T00:01:00Z",
        pipelineArtifacts: [script],
        step: "translated",
      }),
    ).rejects.toThrow();
    expect(database.sqlite.prepare("SELECT COUNT(*) AS count FROM script_versions").get()).toEqual({ count: 0 });
  });
});
